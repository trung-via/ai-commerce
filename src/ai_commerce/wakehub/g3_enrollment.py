"""Fixed attended provisioning specification; never invokes an installer.

Readback is input to an OFFLINE plan, never installation/custody evidence. There
is no apply/reset/repair command. Partial or unknown state requires independent
reconciliation, including when an earlier installer ACK was lost.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import ntpath
import re

from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_inventory import ProcessIdentity
from ai_commerce.wakehub.native_journal import canonical, deny, OS_TRUSTED_SIDS


TASK_PARENT = "4a9b07bd41df8cfe4b011e575c4d0ea8a729357f"
ATTEMPT = "wakehub_wh01b_g3_owner_scope_r2_4a9b07b_20261107_codex"
G3_ANCHOR = r"SOFTWARE\AI-Commerce\WakeHubG3"
SERVICE_IMAGE = r"C:\Program Files\AI-Commerce\WakeHubG3\WakeHubBroker.exe"
PRIVATE_ROOT = r"C:\ProgramData\AI-Commerce\WakeHubG3\Broker"
OBSERVER_ROOT = r"C:\ProgramData\AI-Commerce\WakeHubG3\Observer"
STATUS_FRAME_BODY_LIMIT = 65536
STATUS_TRANSPORT_MISSING_OWNERS = (
    "INSTALLED_SCM_OWNED_FIXED_PIPE_SERVER",
    "ENROLLED_INTERACTIVE_PEER_AND_CLIENT_HANDLE_AUTHENTICATION",
    "BOUNDED_DEADLINE_IO_CANCELLATION_AND_DISCONNECT",
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

    The executable exchange consumes complete frames on an ALREADY authenticated
    server HANDLE. No I/O callbacks or caller-reported readiness are authority.
    The native owner must authenticate the client end independently, assemble
    exactly one bounded frame under the challenge deadline, cancel on timeout,
    and abandon on EOF, partial read/write, crash or ambiguous delivery. It must
    never reconnect/retry a reserved exchange or create/reset either G2 ledger.
    """
    return Resource("status_transport", canonical(dict(
        service=broker.SERVICE, pipe=broker.PIPE, schema="WakeHubG3StatusFrame.v1",
        operation="STATUS", framing="UINT32_BIG_ENDIAN_CANONICAL_JSON_EXACT_SINGLE_FRAME",
        body_limit=STATUS_FRAME_BODY_LIMIT, deadline="EXISTING_FIVE_SECOND_G3_CHALLENGE",
        server_identity="EXISTING_G2_KERNEL_HANDLE_SCM_PRIMARY_TOKEN_IMAGE_AND_PROTECTED_ACL",
        client_identity="INDEPENDENT_CLIENT_HANDLE_CHECK_AGAINST_PROTECTED_ENROLLMENT",
        roles=("BROKER_CHALLENGE_ONLY", "INDEPENDENT_OBSERVER_STATUS_ONLY"),
        binding="EXACT_SOURCE_RELEASE_GENERATION_LOGON_PROCESS_AND_SIGNED_REQUEST_SHA256",
        nonce_custody="EXISTING_G3_CHALLENGES_SQLITE_FIRST_WINS_UNKNOWN_BEFORE_OFFER",
        request_custody="EXISTING_G2_REQUESTS_AND_EFFECT_JOURNAL_NO_NEW_LEDGER",
        grant="EXISTING_G2_ISSUER_HUMAN_PAUSE_EXPIRY_AND_REVOCATION_RECHECK",
        ambiguous_delivery="PERMANENT_UNKNOWN_RECONCILE_ONLY_NO_RETRY",
        missing_owners=STATUS_TRANSPORT_MISSING_OWNERS, start="DENY", reuse="DENY")))


def status_transport_preflight() -> dict:
    """Fixed public source disposition; accepts no 'all green' attestations."""
    return dict(schema_version="WakeHubG3StatusTransportPublic.v1",
        status="SOURCE_READY_FOR_ENROLLMENT", native_transport="HOLD",
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
