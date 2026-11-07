"""Fixed attended provisioning specification; never invokes an installer.

Readback is input to an OFFLINE plan, never installation/custody evidence. There
is no apply/reset/repair command. Partial or unknown state requires independent
reconciliation, including when an earlier installer ACK was lost.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re

from ai_commerce.wakehub import native_broker as broker
from ai_commerce.wakehub import private_operator as op
from ai_commerce.wakehub.native_journal import canonical, deny, OS_TRUSTED_SIDS


TASK_PARENT = "4a9b07bd41df8cfe4b011e575c4d0ea8a729357f"
ATTEMPT = "wakehub_wh01b_g3_owner_scope_r2_4a9b07b_20261107_codex"
G3_ANCHOR = r"SOFTWARE\AI-Commerce\WakeHubG3"
SERVICE_IMAGE = r"C:\Program Files\AI-Commerce\WakeHubG3\WakeHubBroker.exe"
PRIVATE_ROOT = r"C:\ProgramData\AI-Commerce\WakeHubG3\Broker"
OBSERVER_ROOT = r"C:\ProgramData\AI-Commerce\WakeHubG3\Observer"
NOT_ENROLLED = (
    "SOURCE_ONLY", "NATIVE_SERVICE_NOT_INSTALLED", "SIGNER_NOT_ENROLLED",
    "DESKTOP_OBSERVER_NOT_ENROLLED", "CDP_9444_NOT_STARTED",
    "LOGON_TASK_NOT_INSTALLED", "WAKE_NOT_SENT",
)


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
