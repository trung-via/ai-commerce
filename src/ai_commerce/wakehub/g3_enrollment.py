"""Fixed attended provisioning specification; never invokes an installer.

Readback is input to an OFFLINE plan, never installation/custody evidence. There
is no apply/reset/repair command. Partial or unknown state requires independent
reconciliation, including when an earlier installer ACK was lost.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import re
import sys
import time

from ai_commerce.wakehub import native_authority as auth
from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_inventory import ProcessIdentity
from ai_commerce.wakehub.native_journal import (
    canonical, decode, deny, lane_for, shape, WindowsPathCustody, OS_TRUSTED_SIDS,
)


TASK_PARENT = "4a9b07bd41df8cfe4b011e575c4d0ea8a729357f"
ATTEMPT = "wakehub_wh01b_g3_owner_scope_r2_4a9b07b_20261107_codex"
G3_ANCHOR = r"SOFTWARE\AI-Commerce\WakeHubG3"
SERVICE_IMAGE = r"C:\Program Files\AI-Commerce\WakeHubG3\WakeHubBroker.exe"
PRIVATE_ROOT = r"C:\ProgramData\AI-Commerce\WakeHubG3\Broker"
OBSERVER_ROOT = r"C:\ProgramData\AI-Commerce\WakeHubG3\Observer"
CHALLENGES_PATH = PRIVATE_ROOT + r"\challenges.sqlite3"
STATUS_FRAME_BODY_LIMIT = 65536
STATUS_TRANSPORT_MISSING_OWNERS = (
    "INSTALLED_SCM_OWNED_FIXED_PIPE_SERVER",
    "ENROLLED_INTERACTIVE_PEER_AND_CLIENT_HANDLE_AUTHENTICATION",
    "SEPARATE_PROTECTED_BROKER_AND_OBSERVER_SIGNING_HANDOFFS",
    "ATTENDED_NATIVE_INSTALLATION_AND_PROTECTED_READBACK",
)
NOT_ENROLLED = (
    "SOURCE_ONLY", "NATIVE_SERVICE_NOT_INSTALLED", "SIGNER_NOT_ENROLLED",
    "DESKTOP_OBSERVER_NOT_ENROLLED", "CDP_9444_NOT_STARTED",
    "LOGON_TASK_NOT_INSTALLED", "WAKE_NOT_SENT",
)


def status_transport_contract() -> Resource:
    """Missing native owner's exact private contract, never an installer input.

    Source adapters consume existing, independently authenticated server/client
    HANDLEs. They require overlapped message mode, one message per direction,
    and a monotonic budget that never exceeds the five-second challenge. No I/O
    callbacks or caller-reported readiness are authority. Unfinished cancellation
    retains kernel-owned memory and blocks further adapters for reconciliation.
    The installed owner supplies sole HANDLE custody and separate signing roles;
    it must never reconnect/retry or create/reset either existing G2 ledger.
    """
    return Resource("status_transport", canonical(dict(
        service=broker.SERVICE, pipe=broker.PIPE, schema="WakeHubG3StatusFrame.v1",
        operation="STATUS", framing="UINT32_BIG_ENDIAN_CANONICAL_JSON_EXACT_SINGLE_FRAME",
        body_limit=STATUS_FRAME_BODY_LIMIT, deadline="EXISTING_FIVE_SECOND_G3_CHALLENGE",
        io="OVERLAPPED_MESSAGE_MODE_MONOTONIC_DEADLINE_SINGLE_READ_SINGLE_WRITE",
        cancellation="CANCEL_IO_EX_NO_RETRY_RETAIN_UNTIL_COMPLETED",
        handle_custody="SOLE_OWNERSHIP_TRANSFER_NO_CONCURRENT_HANDLER_OR_HANDLE_READOPTION",
        server_identity="EXISTING_G2_KERNEL_HANDLE_SCM_PRIMARY_TOKEN_IMAGE_AND_PROTECTED_ACL",
        client_identity="INDEPENDENT_CLIENT_HANDLE_CHECK_AGAINST_PROTECTED_ENROLLMENT",
        roles=("BROKER_CHALLENGE_ONLY", "INDEPENDENT_OBSERVER_STATUS_ONLY"),
        binding="EXACT_SOURCE_RELEASE_GENERATION_LOGON_PROCESS_AND_SIGNED_REQUEST_SHA256",
        nonce_custody="EXISTING_G3_CHALLENGES_SQLITE_FIRST_WINS_UNKNOWN_BEFORE_OFFER",
        request_custody="EXISTING_G2_REQUEST_UNKNOWN_BEFORE_OFFER_AND_EFFECT_JOURNAL_NO_NEW_LEDGER",
        grant="EXISTING_G2_ISSUER_HUMAN_PAUSE_EXPIRY_AND_REVOCATION_RECHECK",
        ambiguous_delivery="PERMANENT_UNKNOWN_RECONCILE_ONLY_NO_RETRY",
        missing_owners=STATUS_TRANSPORT_MISSING_OWNERS, start="DENY", reuse="DENY")))


def status_transport_preflight() -> dict:
    """Fixed public source disposition; accepts no 'all green' attestations."""
    return dict(schema_version="WakeHubG3StatusTransportPublic.v1",
        status="SOURCE_READY_FOR_ENROLLMENT", native_transport="HOLD",
        dependency="HOLD_DEPENDENCY",
        authority="SOURCE_ONLY", missing_owners=STATUS_TRANSPORT_MISSING_OWNERS,
        native_effect="NOT_ATTEMPTED", start="DENY", reuse="DENY")


@dataclass(frozen=True)
class EnrollmentPins:
    source: op.SourceBinding
    generation: int
    operator_sid: str
    observer_sid: str
    runner_sids: tuple[str, ...]
    service_sha256: str
    observer_sha256: str
    # Public key fingerprints, not key material. Each role is independent.
    issuer_key: str
    human_key: str
    observer_key: str
    broker_key: str
    journal_id: str

    def __post_init__(self):
        sids = (self.operator_sid, self.observer_sid, *self.runner_sids)
        if (type(self.source) is not op.SourceBinding
                or type(self.generation) is not int or not 1 <= self.generation < 2**53
                or type(self.runner_sids) is not tuple or not self.runner_sids
                or len(set(self.runner_sids)) != len(self.runner_sids)
                or self.observer_sid == self.operator_sid or self.observer_sid in self.runner_sids
                or any(type(s) is not str or not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", s)
                       or s in {*OS_TRUSTED_SIDS, broker.service_sid()} for s in sids)
                or any(type(v) is not str or not re.fullmatch(r"[0-9a-f]{64}", v)
                       for v in (self.service_sha256, self.observer_sha256,
                                 self.issuer_key, self.human_key, self.observer_key, self.broker_key))
                or len({self.issuer_key, self.human_key, self.observer_key, self.broker_key}) != 4
                or type(self.journal_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", self.journal_id)):
            deny()


@dataclass(frozen=True)
class Resource:
    name: str
    # Canonical desired bytes, so mutable caller dictionaries cannot change a plan.
    value: bytes


@dataclass(frozen=True)
class Readback:
    name: str
    state: str  # ABSENT / PRESENT / UNKNOWN, no truthy substitutes
    value: bytes | None = None


@dataclass(frozen=True)
class AttendedPlan:
    digest: str
    resources: tuple[Resource, ...]
    state: str
    steps: tuple[str, ...]
    native_state: tuple[str, ...] = NOT_ENROLLED


def desired_resources(pins: EnrollmentPins) -> tuple[Resource, ...]:
    if type(pins) is not EnrollmentPins:
        deny()
    sid = broker.service_sid()
    # These are reviewable data, never shell argv or installer inputs. All ACL
    # descriptions require protected DACLs and trusted administrative ownership.
    specifications = (
        ("release", dict(image=SERVICE_IMAGE, service_sha256=pins.service_sha256,
                         observer_sha256=pins.observer_sha256, source=asdict(pins.source),
                         generation=pins.generation, owner="ADMIN", writers="ADMIN", protected=True)),
        ("service", dict(name=broker.SERVICE, image=SERVICE_IMAGE, arguments=(), kind=0x10,
                         account="NT SERVICE\\" + broker.SERVICE, sid_type=1, start="DEMAND",
                         interactive=False, owner="ADMIN", writers="ADMIN", protected=True)),
        ("anchors", dict(keys=(broker.ANCHOR, G3_ANCHOR), task_parent=TASK_PARENT,
                         generation=pins.generation, journal_id=pins.journal_id,
                         source=asdict(pins.source), owner="ADMIN", writers="ADMIN", protected=True)),
        ("public_signers", dict(issuer=pins.issuer_key, human=pins.human_key,
                                observer=pins.observer_key, broker=pins.broker_key,
                                owner="ADMIN", writers="ADMIN", protected=True)),
        ("broker_custody", dict(root=PRIVATE_ROOT, readers=(sid,), writers=(sid,), owner="ADMIN",
                                protected=True, key_role="BROKER_CHALLENGE_ONLY",
                                journals=("effects.sqlite3", "requests.sqlite3", "challenges.sqlite3"),
                                journal_id=pins.journal_id, ledger_action="CREATE_ONCE_IF_PROVEN_ABSENT")),
        ("observer_custody", dict(root=OBSERVER_ROOT, readers=(pins.observer_sid,),
                                  writers=(pins.observer_sid,), owner="ADMIN", protected=True,
                                  key_role="DESKTOP_STATUS_ONLY", interactive_user=pins.operator_sid,
                                  runner_sids=pins.runner_sids, service_access=False,
                                  signing_handoff="SEPARATE_ENROLLED_OBSERVER_CUSTODY")),
        ("pipe_policy", dict(name=broker.PIPE, server=sid, client=pins.operator_sid,
                              protected=True, remote=False, client_create_instance=False,
                              operations=("STATUS", "RECONCILE_ONLY"))),
    )
    return tuple(Resource(name, canonical(value)) for name, value in specifications)


def attended_plan(pins: EnrollmentPins, readback: tuple[Readback, ...], *,
                  previous_digest: str | None = None) -> AttendedPlan:
    """Same pins/readback => same plan; never infer absence from an error.

    A complete matching readback is still SOURCE_ONLY. It never enables native
    authority; only the separate production custody checks can establish that.
    """
    resources = desired_resources(pins)
    digest = hashlib.sha256(canonical({r.name: r.value.decode("ascii") for r in resources})).hexdigest()
    hold = AttendedPlan(digest, resources, "HOLD_RECONCILE_ONLY", ())
    if previous_digest is not None and previous_digest != digest:
        return hold
    if (type(readback) is not tuple or any(type(r) is not Readback for r in readback)
            or len(readback) != len(resources) or len({r.name for r in readback}) != len(readback)
            or {r.name for r in readback} != {r.name for r in resources}):
        return hold
    if all(r.state == "ABSENT" and r.value is None for r in readback):
        return AttendedPlan(digest, resources, "READY_FOR_ATTENDED_REVIEW", (
            "VERIFY_INDEPENDENT_HUMAN_AND_SOURCE_PINS", "STAGE_ADMIN_OWNED_RELEASE",
            "PROVISION_SEPARATE_ROLE_CUSTODY", "CREATE_NEW_LEDGERS_ONCE",
            "STAGE_DEMAND_START_NONINTERACTIVE_SERVICE", "PIN_PROTECTED_ANCHORS_AND_PIPE_POLICY",
            "READBACK_EVERY_RESOURCE", "RECONCILE_ANY_UNKNOWN_WITHOUT_RETRY",
        ))
    expected = {r.name: r.value for r in resources}
    if all(r.state == "PRESENT" and type(r.value) is bytes and r.value == expected[r.name]
           for r in readback):
        return AttendedPlan(digest, resources, "READBACK_MATCH_SOURCE_ONLY", ())
    return hold


def postflight(plan: AttendedPlan, pins: EnrollmentPins, readback: tuple[Readback, ...]) -> AttendedPlan:
    # An absent postflight is a failed/partial installation, not an invitation
    # to execute the provisioning steps again.
    checked = attended_plan(pins, readback, previous_digest=plan.digest)
    if checked.state != "READBACK_MATCH_SOURCE_ONLY":
        return AttendedPlan(checked.digest, checked.resources, "HOLD_RECONCILE_ONLY", ())
    return checked


DESKTOP_IMAGE = r"C:\Program Files\AI-Commerce\WakeHubG3\WakeHubDesktopExecutor.exe"
DESKTOP_MISSING_OWNERS = (
    "PROTECTED_DESKTOP_SERVICE_AND_PRIVATE_PEER_TRANSPORT",
    "ATTESTED_INTERACTIVE_PRIMARY_TOKEN_AND_IMAGE",
    "SIGNED_ONE_SHOT_START_GRANT_HANDOFF",
    "ATOMIC_G2_FIRST_WINS_LEASE_AND_LIVE_RECHECK",
    "INDEPENDENT_DURABLE_ORIGINAL_OPERATION_OUTCOME",
    "NATIVE_DEPLOYMENT_AND_PROTECTED_READBACK",
)


class DesktopBootstrapState(Enum):
    PREREQUISITES_REQUIRED = "HOLD_PRE_ENROLLED_PREREQUISITES_REQUIRED"
    RECONCILE_ONLY = "HOLD_RECONCILE_ONLY"
    FIRST_BOOT = "FIRST_BOOT_ELIGIBLE_SOURCE_ONLY"
    READBACK_MATCH = "RECONCILED_READBACK_MATCH_SOURCE_ONLY"


@dataclass(frozen=True, slots=True, repr=False)
class DesktopBootstrapPins:
    """Expected private readback, NOT an attested peer or executable capability.

    The dedicated Chrome/config must already be enrolled separately. A future
    native owner must obtain these identities from protected custody and recheck
    the ACTUAL primary token, logon LUID, desktop, image hashes, PID creation time
    and every path component immediately before an effect. Offline equality
    proves none of those native facts and cannot enroll that owner.
    """
    enrollment: EnrollmentPins
    target: op.PrivateTarget
    peer: ProcessIdentity
    logon_id: str
    executor_sha256: str
    chrome_sha256: str
    config_path: str
    config_sha256: str
    profile_directory: str
    allow_launch: bool

    def __post_init__(self):
        if (type(self.enrollment) is not EnrollmentPins or type(self.target) is not op.PrivateTarget
                or type(self.peer) is not ProcessIdentity
                or self.target.user_sid != self.enrollment.operator_sid
                or (self.peer.user_sid, self.peer.session_id) !=
                   (self.target.user_sid, self.target.session_id)
                or ntpath.normcase(self.peer.executable) != ntpath.normcase(DESKTOP_IMAGE)
                or type(self.logon_id) is not str or not re.fullmatch(r"[0-9a-f]{16}", self.logon_id)
                or self.logon_id == "0" * 16
                or any(type(v) is not str or not re.fullmatch(r"[0-9a-f]{64}", v)
                       for v in (self.executor_sha256, self.chrome_sha256, self.config_sha256))
                or not op._path(self.config_path)
                or not self.config_path.casefold().endswith(
                    r"\ai-commerce\chrome-acquisition\config.json")
                or type(self.profile_directory) is not str or not 0 < len(self.profile_directory) <= 128
                or self.profile_directory in (".", "..")
                or any(c in self.profile_directory for c in '\\/:*?"<>|')
                or any(ord(c) < 32 for c in self.profile_directory)
                or type(self.allow_launch) is not bool):
            deny()


@dataclass(frozen=True, slots=True, repr=False)
class DesktopBootstrapPlan:
    digest: str
    resources: tuple[Resource, ...]
    state: DesktopBootstrapState

    @property
    def source_ready(self):
        return self.state in (DesktopBootstrapState.FIRST_BOOT, DesktopBootstrapState.READBACK_MATCH)

    def to_json(self):
        # Never serialize desired bytes/digests: they contain private identities,
        # file locations and durable lane selectors. Missing owners are fixed.
        if type(self.state) is not DesktopBootstrapState:
            deny()
        return canonical(dict(schema_version="WakeHubN3B2DesktopBootstrapPublic.v1",
            status="SOURCE_READY_FOR_ENROLLMENT" if self.source_ready else "HOLD",
            readback=self.state.value, first_boot_eligible=self.state is DesktopBootstrapState.FIRST_BOOT,
            authority="SOURCE_ONLY", missing_owners=DESKTOP_MISSING_OWNERS,
            native_effect="NOT_ATTEMPTED", start="DENY", reuse="DENY",
            signer_enrollment="NOT_PROVEN_BY_SOURCE_OR_OFFLINE_TESTS")).decode("ascii")


def desktop_bootstrap_resources(pins: DesktopBootstrapPins) -> tuple[Resource, ...]:
    """Exact offline requirements; there is deliberately no apply/install route.

    The STATUS-only G3 pipe remains STATUS-only. The missing effect transport
    must authenticate both enrolled endpoints and a separate one-shot start
    grant; neither this specification nor an existing challenge grants START.
    """
    if type(pins) is not DesktopBootstrapPins:
        deny()
    target = pins.target
    specifications = (
        ("chrome_config", dict(path=pins.config_path, sha256=pins.config_sha256,
            executable=target.chrome_executable, image_sha256=pins.chrome_sha256,
            user_data_dir=target.dedicated_profile, profile_directory=pins.profile_directory,
            endpoint="http://127.0.0.1:9444", address="127.0.0.1", port=9444,
            allow_launch=pins.allow_launch, enrollment="SEPARATE_PRE_ENROLLED_DEDICATED_CHROME",
            owner="ADMIN", writers="ADMIN", protected=True, reparse_points=False, hardlinks=False,
            profile_owner=target.user_sid, foreign_profile_access=False)),
        ("desktop_identity", dict(peer=asdict(pins.peer), image_sha256=pins.executor_sha256,
            logon_id=pins.logon_id, desktop=target.desktop, token="PRIMARY", elevated=False,
            interactive=True, unlocked=True, connected=True, session_zero=False,
            owner="ADMIN", writers="ADMIN", protected=True, reparse_points=False, hardlinks=False,
            live_recheck="REQUIRED_BY_EXTERNAL_NATIVE_OWNER")),
        ("effect_lane", dict(journal_id=pins.enrollment.journal_id, state="EMPTY",
            target_sid=target.user_sid, profile=ntpath.normcase(target.dedicated_profile),
            ledger="EXISTING_G2_DURABLE_FIRST_WINS", consumed_keys=(),
            reservation="NOT_RESERVED", create_reset_migrate=False)),
        ("desktop_transport", dict(service=broker.SERVICE, image=DESKTOP_IMAGE,
            image_sha256=pins.executor_sha256, source=asdict(pins.enrollment.source),
            generation=pins.enrollment.generation, status_pipe_effects="FORBIDDEN",
            peer_authentication="PROTECTED_SERVICE_AND_EXACT_INTERACTIVE_PEER",
            desktop_challenge="EXISTING_G3_SIGNED_NONCE_SOURCE_GENERATION_AND_LOGON_LUID",
            nonce_custody="EXISTING_G3_DURABLE_CHALLENGE_FIRST_WINS", status_grants_allow_effects=False,
            grant="G2_SIGNED_ISSUER_AND_INDEPENDENT_HUMAN_CONSENT",
            start_handoff="SEPARATE_PROTECTED_SIGNED_ONE_SHOT_START_GRANT",
            reuse_handoff="G2_REUSE_SCOPE_NO_START_GRANT_SUBSTITUTION",
            freshness="RECHECK_REVOCATION_SOURCE_CONFIG_TOKEN_DESKTOP_HASH_AND_PID_CREATION",
            start="COMPLETE_ABSENT_AND_EXCLUSIVE_UNCONSUMED_LANE_AND_ALLOW_LAUNCH_TRUE",
            reuse="EXACT_OWNED_PROCESS_NEVER_SPAWN", lease="AUTHENTICATE_EXISTING_G2_PRIVATE_LEASE",
            arbitrary_commands=False, shell=False, url_dispatch=False, profile_changes=False)),
        ("desktop_outcome", dict(journal_id=pins.enrollment.journal_id,
            receipt="INDEPENDENT_SIGNED_DURABLE_ORIGINAL_OPERATION_AND_LEASE",
            before_dispatch="DURABLE_FIRST_WINS_RESERVATION",
            reservation_or_spawn_error="UNKNOWN_EFFECT", timeout_or_lost_ack="UNKNOWN_EFFECT",
            unknown="RECONCILE_ONLY_NO_SECOND_START", observed_no_effect="KEYS_REMAIN_CONSUMED",
            presence_or_local_ack_is_outcome=False, retry=False)),
    )
    return (*desired_resources(pins.enrollment),
            *(Resource(name, canonical(value)) for name, value in specifications))


def desktop_bootstrap_plan(pins: DesktopBootstrapPins, readback: tuple[Readback, ...], *,
                           previous_digest: str | None = None) -> DesktopBootstrapPlan:
    """Deterministic source eligibility, never native authorization.

    First boot requires exact PRESENT G3, Chrome, desktop identity and EMPTY G2
    lane readbacks, with BOTH new transport/outcome resources proven ABSENT.
    Partial/unknown state cannot bootstrap, replace custody, or reset a lane.
    These supplied offline readbacks still need independent native collection.
    """
    resources = desktop_bootstrap_resources(pins)
    digest = hashlib.sha256(canonical({r.name: r.value.decode("ascii") for r in resources})).hexdigest()
    def result(state):
        return DesktopBootstrapPlan(digest, resources, state)
    hold = result(DesktopBootstrapState.RECONCILE_ONLY)
    if previous_digest is not None and (type(previous_digest) is not str or previous_digest != digest):
        return hold
    if (type(readback) is not tuple or any(type(r) is not Readback for r in readback)
            or len(readback) != len(resources) or any(type(r.name) is not str for r in readback)
            or len({r.name for r in readback}) != len(readback)
            or {r.name for r in readback} != {r.name for r in resources}):
        return hold
    if all(type(r.state) is str and r.state == "ABSENT" and r.value is None for r in readback):
        return result(DesktopBootstrapState.PREREQUISITES_REQUIRED)
    expected = {r.name: r.value for r in resources}
    matching = {r.name for r in readback if type(r.state) is str and r.state == "PRESENT"
                and type(r.value) is bytes and r.value == expected[r.name]}
    if matching == set(expected):
        return result(DesktopBootstrapState.READBACK_MATCH)
    new = {"desktop_transport", "desktop_outcome"}
    if (matching == set(expected) - new
            and all(type(r.state) is str and r.state == "ABSENT" and r.value is None
                    for r in readback if r.name in new)):
        return result(DesktopBootstrapState.FIRST_BOOT)
    return hold


def desktop_bootstrap_postflight(plan: DesktopBootstrapPlan, pins: DesktopBootstrapPins,
                                readback: tuple[Readback, ...]) -> DesktopBootstrapPlan:
    """After any attempted provisioning, absence/timeout/lost ACK is NOT retryable."""
    if type(plan) is not DesktopBootstrapPlan:
        deny()
    checked = desktop_bootstrap_plan(pins, readback, previous_digest=plan.digest)
    if (plan.resources != checked.resources or not plan.source_ready
            or checked.state is not DesktopBootstrapState.READBACK_MATCH):
        return DesktopBootstrapPlan(checked.digest, checked.resources, DesktopBootstrapState.RECONCILE_ONLY)
    return checked


class ScmBindingState(Enum):
    HOLD_RECONCILE = "HOLD_RECONCILE_ONLY"
    READBACK_VERIFIED = "LIVE_READBACK_VERIFIED_NO_WRITE"
    PID_BOUND_READBACK_VERIFIED = "PID_BOUND_AFTER_INDEPENDENT_READBACK"


_SCM_BINDING_REASONS = frozenset({
    "ADMIN_OR_HOST_PREREQUISITES_UNPROVEN",
    "EXISTING_ANCHOR_AMBIGUOUS_OR_CHANGED",
    "LIVE_SCM_CHANGED_BEFORE_WRITE",
    "AMBIGUOUS_WRITE_RECONCILE_ONLY",
    "POSTWRITE_READBACK_MISMATCH_RECONCILE_ONLY",
    "INDEPENDENT_READBACK_MATCH",
    "PID_BOUND_AFTER_INDEPENDENT_READBACK",
    "INVALID_BINDING_SPEC",
})


def _spec_path(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 1024
            and not any(ord(c) < 32 for c in value)
            and not any(bad in value for bad in ("..", "*", "?"))
            and (op._path(value) or Path(value).is_absolute()))


@dataclass(frozen=True, slots=True, repr=False)
class AttendedScmBindingSpec:
    """Pre-existing admin-pinned inputs for live SCM PID/creation-time binding.

    Never carries private key material, installer commands, or shell arguments.
    The service binary, SCM noninteractive account, signed release, public
    signer policies, and separate broker/observer custodies must already exist.
    """
    pins: EnrollmentPins
    target: op.PrivateTarget
    desktop_peer: ProcessIdentity
    desktop_peer_sha256: str
    logon_id: str
    verifier_path: str
    verifier_sha256: str
    issuer_signers_path: str
    human_signers_path: str
    observer_signers_path: str
    broker_signers_path: str
    enrollment_path: str
    journal_path: str
    requests_path: str
    challenges_path: str
    private_key_path: str
    desktop_path: str
    observer_executable: str
    observer_key_path: str

    def __post_init__(self):
        paths = (
            self.verifier_path, self.issuer_signers_path, self.human_signers_path,
            self.observer_signers_path, self.broker_signers_path, self.enrollment_path,
            self.journal_path, self.requests_path, self.challenges_path,
            self.private_key_path, self.desktop_path, self.observer_executable,
            self.observer_key_path,
        )
        private_paths = (
            self.journal_path, self.requests_path, self.challenges_path,
            self.private_key_path, self.desktop_path, self.observer_key_path,
        )
        norm_private = [ntpath.normcase(p) for p in private_paths if type(p) is str]
        allowed_verifiers = ("ssh-keygen.exe",) if os.name == "nt" else ("ssh-keygen.exe", "ssh-keygen")
        if (type(self.pins) is not EnrollmentPins
                or type(self.target) is not op.PrivateTarget
                or type(self.desktop_peer) is not ProcessIdentity
                or self.target.user_sid != self.pins.operator_sid
                or (self.desktop_peer.user_sid, self.desktop_peer.session_id)
                != (self.target.user_sid, self.target.session_id)
                or self.desktop_peer.session_id <= 0
                or type(self.logon_id) is not str
                or not re.fullmatch(r"[0-9a-f]{16}", self.logon_id)
                or self.logon_id == "0" * 16
                or any(type(h) is not str or not re.fullmatch(r"[0-9a-f]{64}", h)
                       for h in (self.desktop_peer_sha256, self.verifier_sha256))
                or any(not _spec_path(p) for p in paths)
                or Path(self.verifier_path).name.casefold() not in allowed_verifiers
                or ntpath.normcase(self.challenges_path) != ntpath.normcase(CHALLENGES_PATH)
                or len(norm_private) != len(private_paths)
                or len(set(norm_private)) != len(private_paths)):
            deny()


@dataclass(frozen=True, slots=True, repr=False)
class AttendedScmBindingResult:
    """Bounded result of an admin-attended SCM PID/creation-time stage."""
    state: ScmBindingState
    reason: str
    bound_pid: int | None = None
    bound_created_at: float | None = None
    anchor_digest: str | None = None
    retry_allowed: bool = False

    def __post_init__(self):
        verified = self.state in (ScmBindingState.READBACK_VERIFIED, ScmBindingState.PID_BOUND_READBACK_VERIFIED)
        if (type(self.state) is not ScmBindingState
                or type(self.reason) is not str or self.reason not in _SCM_BINDING_REASONS
                or self.retry_allowed is not False):
            deny()
        if verified:
            if (type(self.bound_pid) is not int or not 0 < self.bound_pid < 2**32
                    or type(self.bound_created_at) not in (int, float)
                    or not math.isfinite(self.bound_created_at) or self.bound_created_at <= 0
                    or type(self.anchor_digest) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", self.anchor_digest)):
                deny()
        elif self.bound_pid is not None or self.bound_created_at is not None:
            deny()

    def to_public_json(self) -> str:
        """Sanitized public output only: never leaks PID, path, SID, logon or digest."""
        verified = self.state in (ScmBindingState.READBACK_VERIFIED, ScmBindingState.PID_BOUND_READBACK_VERIFIED)
        return canonical(dict(
            schema_version="WakeHubG3AttendedScmBindingPublic.v1",
            state=self.state.value,
            reason=self.reason,
            pid_bound=self.state is ScmBindingState.PID_BOUND_READBACK_VERIFIED,
            readback_verified=verified,
            retry_allowed=False,
            authority="ATTENDED_ADMIN_READBACK_ONLY" if verified else "SOURCE_ONLY",
            native_effect="NOT_ATTEMPTED",
            start="DENY",
            reuse="DENY",
            wake="NOT_SENT",
            chatgpt_account_session="ACCOUNT_UNPROVEN",
        )).decode("ascii")


def _verify_preinstalled_prerequisites(spec: AttendedScmBindingSpec, *, now: float):
    """Verify pre-existing signed release, signers, binaries, custodies and live SCM."""
    from ai_commerce.wakehub import g3_desktop_observer as observer

    if type(spec) is not AttendedScmBindingSpec or type(now) not in (int, float) or not math.isfinite(now) or now <= 0:
        deny()
    auth.windows_only()
    broker._check_admin_token()
    custody = WindowsPathCustody()
    pins = spec.pins
    # 1. Verify all four independent public signer files and hashes.
    role_specs = (
        ("issuer", spec.issuer_signers_path, pins.issuer_key, auth.IDENTITIES["issuer"]),
        ("human", spec.human_signers_path, pins.human_key, auth.IDENTITIES["human"]),
        ("observer", spec.observer_signers_path, pins.observer_key, auth.IDENTITIES["observer"]),
        ("broker", spec.broker_signers_path, pins.broker_key, observer.BROKER_IDENTITY),
    )
    pubkeys = []
    for _, signer_path, expected_sha, expected_identity in role_specs:
        raw = custody.check(signer_path).read_bytes()
        if len(raw) > 8192 or auth._digest(raw) != expected_sha:
            deny()
        lines = [line.split() for line in raw.decode("ascii").splitlines() if line.strip()]
        if len(lines) != 1 or len(lines[0]) != 3 or lines[0][:2] != [expected_identity, "ssh-ed25519"]:
            deny()
        pubkeys.append(lines[0][2])
    if len(set(pubkeys)) != 4:
        deny()
    # 2. Verify pinned ssh-keygen verifier binary.
    verifier = custody.check(spec.verifier_path)
    allowed_verifiers = ("ssh-keygen.exe",) if os.name == "nt" else ("ssh-keygen.exe", "ssh-keygen")
    if verifier.name.casefold() not in allowed_verifiers or auth._digest(verifier.read_bytes()) != spec.verifier_sha256:
        deny()
    # 3. Verify pre-existing signed release enrollment and files.
    lane = lane_for(spec.target)
    authority_anchor = dict(
        schema="WakeHubG2Anchor.v1",
        issuer_signers=dict(path=spec.issuer_signers_path, sha256=pins.issuer_key),
        human_signers=dict(path=spec.human_signers_path, sha256=pins.human_key),
        observer_signers=dict(path=spec.observer_signers_path, sha256=pins.observer_key),
        verifier=dict(path=spec.verifier_path, sha256=spec.verifier_sha256),
        enrollment=spec.enrollment_path,
        operator_sid=pins.operator_sid,
        runner_sids=list(pins.runner_sids),
        journal_path=spec.journal_path,
        journal_id=pins.journal_id,
        lane=lane,
    )
    enrollment_file = custody.check(spec.enrollment_path)
    enrollment_bytes = enrollment_file.read_bytes()
    enrolled = auth._signed(enrollment_bytes, "issuer", "enrollment", authority_anchor)
    shape(enrolled, "schema task_parent source target release_root files journal_path journal_id lane observer_root observer_sid issued_at expires_at enabled")
    if (enrolled["schema"] != "WakeHubG2Enrollment.v1"
            or enrolled["task_parent"] != auth.TASK_PARENT
            or enrolled["enabled"] is not True
            or not 0 < enrolled["issued_at"] <= now < enrolled["expires_at"]
            or op.SourceBinding(**enrolled["source"]) != pins.source
            or op.PrivateTarget(**enrolled["target"]) != spec.target
            or enrolled["journal_id"] != pins.journal_id
            or ntpath.normcase(enrolled["journal_path"]) != ntpath.normcase(spec.journal_path)
            or enrolled["lane"] != lane
            or enrolled["observer_sid"] != broker.service_sid()):
        deny()
    release_root = custody.check(enrolled["release_root"], directory=True)
    files = enrolled["files"]
    if type(files) is not dict or not 5 <= len(files) <= 512:
        deny()
    for relative, expected_hash in files.items():
        if (type(relative) is not str or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
                or any(part in ("", ".", "..") for part in relative.split("/"))
                or type(expected_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
            deny()
        if auth._digest(custody.check(release_root / relative).read_bytes()) != expected_hash:
            deny()
    service_custody = broker.ServiceCustody()
    observer_root = service_custody.check(enrolled["observer_root"], directory=True)
    journal_file = service_custody.check(spec.journal_path)
    if any((root / "KILL").exists() for root in (enrollment_file.parent, observer_root, journal_file.parent)):
        deny()
    rev = auth._signed(service_custody.check(observer_root / "revocations.json").read_bytes(),
                       "issuer", "revocations", authority_anchor)
    shape(rev, "schema journal_id epoch source_epoch release_epoch issued_at expires_at revoked")
    auth._fresh(rev, now)
    if (rev["schema"] != "WakeHubG2Revocations.v1"
            or rev["journal_id"] != pins.journal_id
            or rev["source_epoch"] != pins.source.source_epoch
            or rev["release_epoch"] != pins.source.release_epoch):
        deny()
    # 4. Verify pre-installed service, observer signer, and desktop peer binaries.
    if auth._digest(custody.check(SERVICE_IMAGE).read_bytes()) != pins.service_sha256:
        deny()
    if auth._digest(custody.check(spec.observer_executable).read_bytes()) != pins.observer_sha256:
        deny()
    if auth._digest(custody.check(spec.desktop_peer.executable).read_bytes()) != spec.desktop_peer_sha256:
        deny()
    # 5. Verify pre-existing separate broker and observer key/ledger custodies.
    for private_path in (spec.journal_path, spec.requests_path, spec.challenges_path,
                         spec.private_key_path, spec.desktop_path):
        service_custody.check(private_path)
    broker.ObserverCustody(pins.observer_sid).check(spec.observer_key_path)
    # 6. Query live SCM service (read-only) and live kernel process identity.
    scm = broker._scm_process()
    if (scm["account"] != "NT SERVICE\\" + broker.SERVICE
            or scm["kind"] != 0x10
            or scm["sid_type"] not in (1, 3)
            or scm["binary"] not in (SERVICE_IMAGE, '"' + SERVICE_IMAGE + '"')):
        deny()
    live_server = broker.read_process_identity(scm["pid"])
    if (live_server.pid != scm["pid"]
            or live_server.user_sid != broker.service_sid()
            or live_server.session_id != 0
            or ntpath.normcase(live_server.executable) != ntpath.normcase(SERVICE_IMAGE)
            or live_server.created_at > now):
        deny()
    # 7. Construct exact canonical anchors bound to live SCM PID + created_at.
    desired_broker = dict(
        schema="WakeHubPrivateBrokerAnchor.v1",
        task_parent=auth.TASK_PARENT,
        authority=authority_anchor,
        service=dict(name=broker.SERVICE, process=asdict(live_server), sha256=pins.service_sha256),
        desktop_peer=dict(process=asdict(spec.desktop_peer), sha256=spec.desktop_peer_sha256),
        requests_path=spec.requests_path,
        private_key_path=spec.private_key_path,
        desktop_path=spec.desktop_path,
    )
    desired_g3 = dict(
        schema="WakeHubG3Host.v1",
        task_parent=TASK_PARENT,
        source=asdict(pins.source),
        generation=pins.generation,
        journal_id=pins.journal_id,
        target=asdict(spec.target),
        logon_id=spec.logon_id,
        broker_signers=dict(path=spec.broker_signers_path, sha256=pins.broker_key),
        challenges_path=spec.challenges_path,
    )
    desired_signer = dict(
        schema="WakeHubG3ObserverSignerAnchor.v1",
        task_parent=TASK_PARENT,
        pipe=broker.OBSERVER_SIGNER_PIPE,
        observer_sid=pins.observer_sid,
        observer_executable=spec.observer_executable,
        observer_sha256=pins.observer_sha256,
        observer_key_path=spec.observer_key_path,
    )
    return live_server, scm, dict(broker=desired_broker, g3=desired_g3, observer_signer=desired_signer)


def _static_broker_pins(anchor: dict) -> dict:
    """Static broker pins excluding dynamic runtime process PID/created_at."""
    shape(anchor, "schema task_parent authority service desktop_peer requests_path private_key_path desktop_path")
    shape(anchor["service"], "name process sha256")
    shape(anchor["desktop_peer"], "process sha256")
    return dict(
        schema=anchor["schema"],
        task_parent=anchor["task_parent"],
        authority=anchor["authority"],
        service_name=anchor["service"]["name"],
        service_executable=ntpath.normcase(anchor["service"]["process"]["executable"]),
        service_sha256=anchor["service"]["sha256"],
        desktop_executable=ntpath.normcase(anchor["desktop_peer"]["process"]["executable"]),
        desktop_sha256=anchor["desktop_peer"]["sha256"],
        requests_path=anchor["requests_path"],
        private_key_path=anchor["private_key_path"],
        desktop_path=anchor["desktop_path"],
    )


def _read_existing_anchors():
    """Distinguish cleanly between absent registry values vs present vs error."""
    from ai_commerce.wakehub import g3_desktop_observer as observer
    from ai_commerce.wakehub import g3_service_host as host

    readers = (
        ("broker", broker._read_broker_anchor),
        ("g3", host._read_g3_config),
        ("observer_signer", observer._read_observer_signer_anchor),
    )
    found = {}
    for name, reader in readers:
        try:
            found[name] = ("PRESENT", reader())
        except FileNotFoundError:
            found[name] = ("ABSENT", None)
    return found


def collect_live_scm_readback(spec: AttendedScmBindingSpec, *,
                              clock=time.time) -> AttendedScmBindingResult:
    """Read-only independent native readback of pre-installed SCM + protected anchors."""
    from ai_commerce.wakehub import g3_desktop_observer as observer
    from ai_commerce.wakehub import g3_service_host as host

    if type(spec) is not AttendedScmBindingSpec:
        return AttendedScmBindingResult(ScmBindingState.HOLD_RECONCILE, "INVALID_BINDING_SPEC")
    try:
        live_before, scm_before, desired = _verify_preinstalled_prerequisites(spec, now=clock())
        rb_broker = broker._read_broker_anchor()
        rb_g3 = host._read_g3_config()
        rb_signer = observer._read_observer_signer_anchor()
        scm_after = broker._scm_process()
        live_after = broker.read_process_identity(scm_after["pid"])
        if (live_after != live_before or scm_after != scm_before
                or canonical(rb_broker) != canonical(desired["broker"])
                or canonical(rb_g3) != canonical(desired["g3"])
                or canonical(rb_signer) != canonical(desired["observer_signer"])):
            return AttendedScmBindingResult(
                ScmBindingState.HOLD_RECONCILE, "POSTWRITE_READBACK_MISMATCH_RECONCILE_ONLY"
            )
        facts = dict(server=asdict(live_after), client=asdict(spec.desktop_peer), scm=scm_after)
        broker.validate_peer(rb_broker, facts)
        host._validate_config(rb_g3, rb_broker)
        observer._validate_observer_signer_anchor(rb_signer, rb_broker, rb_g3)
        digest = auth._digest(canonical([rb_broker, rb_g3, rb_signer]))
        return AttendedScmBindingResult(
            ScmBindingState.READBACK_VERIFIED,
            "INDEPENDENT_READBACK_MATCH",
            bound_pid=live_after.pid,
            bound_created_at=live_after.created_at,
            anchor_digest=digest,
        )
    except Exception:
        return AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "ADMIN_OR_HOST_PREREQUISITES_UNPROVEN"
        )


def attend_scm_pid_binding(spec: AttendedScmBindingSpec, *,
                           clock=time.time) -> AttendedScmBindingResult:
    """Windows admin-attended protected enrollment/readback stage for live SCM PID.

    Verifies pre-existing signed release, public signer hashes, pre-installed
    service binary, live SCM noninteractive account, and independent broker /
    observer custodies before writing atomically into protected HKLM anchors.
    Dynamic PID/creation time is bound ONLY AFTER independent native readback.
    Any changed, missing, partial, or ambiguous state returns HOLD_RECONCILE
    with retry_allowed=False.
    """
    from ai_commerce.wakehub import g3_desktop_observer as observer
    from ai_commerce.wakehub import g3_service_host as host

    if type(spec) is not AttendedScmBindingSpec:
        return AttendedScmBindingResult(ScmBindingState.HOLD_RECONCILE, "INVALID_BINDING_SPEC")
    try:
        live_before, scm_before, desired = _verify_preinstalled_prerequisites(spec, now=clock())
    except Exception:
        return AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "ADMIN_OR_HOST_PREREQUISITES_UNPROVEN"
        )
    # Inspect existing anchor state: must be either all ABSENT or all PRESENT
    # with identical static pins (no partial/corrupt/drifted pin adoption).
    try:
        existing = _read_existing_anchors()
        states = {state for state, _ in existing.values()}
        if states == {"PRESENT"}:
            if (canonical(_static_broker_pins(existing["broker"][1]))
                    != canonical(_static_broker_pins(desired["broker"]))
                    or canonical(existing["g3"][1]) != canonical(desired["g3"])
                    or canonical(existing["observer_signer"][1]) != canonical(desired["observer_signer"])):
                return AttendedScmBindingResult(
                    ScmBindingState.HOLD_RECONCILE, "EXISTING_ANCHOR_AMBIGUOUS_OR_CHANGED"
                )
        elif states != {"ABSENT"}:
            return AttendedScmBindingResult(
                ScmBindingState.HOLD_RECONCILE, "EXISTING_ANCHOR_AMBIGUOUS_OR_CHANGED"
            )
    except Exception:
        return AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "EXISTING_ANCHOR_AMBIGUOUS_OR_CHANGED"
        )
    # Re-verify live SCM PID/creation time immediately before atomic write.
    try:
        scm_pre = broker._scm_process()
        live_pre = broker.read_process_identity(scm_pre["pid"])
        if scm_pre != scm_before or live_pre != live_before:
            return AttendedScmBindingResult(
                ScmBindingState.HOLD_RECONCILE, "LIVE_SCM_CHANGED_BEFORE_WRITE"
            )
    except Exception:
        return AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "LIVE_SCM_CHANGED_BEFORE_WRITE"
        )
    # Atomic protected registry writes; any error is ambiguous -> HOLD_RECONCILE, no retry.
    try:
        written_broker = broker._write_protected_anchor_value(broker.ANCHOR, "Anchor", desired["broker"])
        written_g3 = broker._write_protected_anchor_value(G3_ANCHOR, "Anchor", desired["g3"])
        written_signer = broker._write_protected_anchor_value(
            G3_ANCHOR, observer.OBSERVER_SIGNER_ANCHOR_VALUE, desired["observer_signer"]
        )
    except Exception:
        return AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "AMBIGUOUS_WRITE_RECONCILE_ONLY"
        )
    # Independent post-write native readback: bind dynamic PID ONLY AFTER match.
    try:
        rb_broker = broker._read_broker_anchor()
        rb_g3 = host._read_g3_config()
        rb_signer = observer._read_observer_signer_anchor()
        scm_after = broker._scm_process()
        live_after = broker.read_process_identity(scm_after["pid"])
        if (canonical(rb_broker) != written_broker
                or canonical(rb_g3) != written_g3
                or canonical(rb_signer) != written_signer
                or scm_after != scm_before
                or live_after != live_before):
            return AttendedScmBindingResult(
                ScmBindingState.HOLD_RECONCILE, "POSTWRITE_READBACK_MISMATCH_RECONCILE_ONLY"
            )
        facts = dict(server=asdict(live_after), client=asdict(spec.desktop_peer), scm=scm_after)
        broker.validate_peer(rb_broker, facts)
        host._validate_config(rb_g3, rb_broker)
        observer._validate_observer_signer_anchor(rb_signer, rb_broker, rb_g3)
        digest = auth._digest(canonical([rb_broker, rb_g3, rb_signer]))
        return AttendedScmBindingResult(
            ScmBindingState.PID_BOUND_READBACK_VERIFIED,
            "PID_BOUND_AFTER_INDEPENDENT_READBACK",
            bound_pid=live_after.pid,
            bound_created_at=live_after.created_at,
            anchor_digest=digest,
        )
    except Exception:
        return AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "POSTWRITE_READBACK_MISMATCH_RECONCILE_ONLY"
        )


def _spec_from_dict(data: dict) -> AttendedScmBindingSpec:
    shape(data, "pins target desktop_peer desktop_peer_sha256 logon_id verifier_path verifier_sha256 issuer_signers_path human_signers_path observer_signers_path broker_signers_path enrollment_path journal_path requests_path challenges_path private_key_path desktop_path observer_executable observer_key_path")
    p = data["pins"]
    shape(p, "source generation operator_sid observer_sid runner_sids service_sha256 observer_sha256 issuer_key human_key observer_key broker_key journal_id")
    if type(p["runner_sids"]) is not list:
        deny()
    pins = EnrollmentPins(
        op.SourceBinding(**p["source"]), p["generation"], p["operator_sid"], p["observer_sid"],
        tuple(p["runner_sids"]), p["service_sha256"], p["observer_sha256"],
        p["issuer_key"], p["human_key"], p["observer_key"], p["broker_key"], p["journal_id"],
    )
    return AttendedScmBindingSpec(
        pins=pins,
        target=op.PrivateTarget(**data["target"]),
        desktop_peer=ProcessIdentity(**data["desktop_peer"]),
        desktop_peer_sha256=data["desktop_peer_sha256"],
        logon_id=data["logon_id"],
        verifier_path=data["verifier_path"],
        verifier_sha256=data["verifier_sha256"],
        issuer_signers_path=data["issuer_signers_path"],
        human_signers_path=data["human_signers_path"],
        observer_signers_path=data["observer_signers_path"],
        broker_signers_path=data["broker_signers_path"],
        enrollment_path=data["enrollment_path"],
        journal_path=data["journal_path"],
        requests_path=data["requests_path"],
        challenges_path=data["challenges_path"],
        private_key_path=data["private_key_path"],
        desktop_path=data["desktop_path"],
        observer_executable=data["observer_executable"],
        observer_key_path=data["observer_key_path"],
    )


def main(argv: list[str] | None = None) -> int:
    """Windows-callable admin-attended enrollment/readback CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.wakehub.g3_enrollment")
    parser.add_argument("--mode", choices=("preflight", "readback", "bind-scm-pid"), default="preflight")
    parser.add_argument("--spec", default=None)
    args = parser.parse_args(argv)
    if args.mode == "preflight":
        sys.stdout.write(json.dumps(status_transport_preflight(), sort_keys=True) + "\n")
        return 2
    try:
        if type(args.spec) is not str or not args.spec:
            deny()
        auth.windows_only()
        broker._check_admin_token()
        raw = WindowsPathCustody().check(args.spec).read_bytes()
        spec = _spec_from_dict(decode(raw, 65536))
        result = (
            collect_live_scm_readback(spec)
            if args.mode == "readback"
            else attend_scm_pid_binding(spec)
        )
    except Exception:
        result = AttendedScmBindingResult(
            ScmBindingState.HOLD_RECONCILE, "ADMIN_OR_HOST_PREREQUISITES_UNPROVEN"
        )
    sys.stdout.write(result.to_public_json() + "\n")
    return 0 if result.state in (ScmBindingState.READBACK_VERIFIED, ScmBindingState.PID_BOUND_READBACK_VERIFIED) else 2


if __name__ == "__main__":
    raise SystemExit(main())
