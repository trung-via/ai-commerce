"""EF01 profile validation adapted from a pinned AIOS execution_profile boundary.

No implicit model choice, default executor or cross-executor retry.
PYTEST_DONT_REWRITE: runtime hooks contain no pytest assertions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

EXECUTORS = frozenset({"codex", "antigravity"})
MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
EFFORT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")


class SelectionError(ValueError):
    """Reject a malformed or missing human-selected executor profile."""


@dataclass(frozen=True, slots=True)
class ExecutorSelection:
    executor: str
    model: str
    effort: str

    def __post_init__(self) -> None:
        if self.executor not in EXECUTORS:
            raise SelectionError("Executor must be manually selected: codex or antigravity")
        if not isinstance(self.model, str) or not MODEL_PATTERN.fullmatch(self.model):
            raise SelectionError("Exact model identifier is required (1-128 safe characters)")
        if not isinstance(self.effort, str) or not EFFORT_PATTERN.fullmatch(self.effort):
            raise SelectionError("Exact reasoning effort is required")


class FailureClass(str, Enum):
    INTAKE_AUTH = "INTAKE_AUTH"
    SOURCE_GIT = "SOURCE_GIT"
    PROVIDER_QUOTA = "PROVIDER_QUOTA"
    PROVIDER_TRANSPORT = "PROVIDER_TRANSPORT"
    TEST_ENVIRONMENT = "TEST_ENVIRONMENT"
    CODE_ASSERTION = "CODE_ASSERTION"
    MT01_SELECTOR = "MT01_SELECTOR"
    TRUSTED_PROOF = "TRUSTED_PROOF"
    REMOTE_GITHUB = "REMOTE_GITHUB"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    phase: str
    root_owner: str
    failure_class: FailureClass
    disposition: str
    evidence_codes: tuple[str, ...]
    evidence_gaps: tuple[str, ...] = ()

    def payload(self) -> dict:
        return {"version": 1, "phase": self.phase, "root_owner": self.root_owner,
                "failure_class": self.failure_class.value, "disposition": self.disposition,
                "evidence_codes": list(self.evidence_codes), "evidence_gaps": list(self.evidence_gaps),
                "replay_authorized": False, "proves_pass": False}


def diagnose_failure(result: dict) -> FailureDiagnosis | None:
    """Classify native boundary outcomes, never provider prose or a PASS assertion.

    This advisory contract cannot authorize a candidate or replay. Source/scope
    failures take precedence over their downstream selector/proof symptoms.
    Only a completed, determinate, repair-eligible pytest failure is code-owned.
    Logs, prompts, arbitrary paths and credentials never enter this projection.
    """
    code = result.get("error_code")
    if not code:
        return None
    code = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code) else "UNCLASSIFIED_ERROR"
    status = result.get("status", "")
    phase, owner, kind = "UNKNOWN", "ENGINEERING_OWNER", FailureClass.UNKNOWN
    if result.get("scope_valid") is False or status in ("SOURCE_DRIFT", "SCOPE_VIOLATION", "GIT_UNEXPECTED_COMMIT") or code.startswith(("SOURCE_", "GIT_", "WORKTREE_", "PREVERIFY_GIT_")):
        phase, owner, kind = "SOURCE", "SOURCE_OWNER", FailureClass.SOURCE_GIT
    elif code.startswith(("EF01_HASH_", "EF01_SOURCE_", "EF01_VERIFIED_", "CANDIDATE_", "MT01_PROOF_")) or "EVIDENCE" in code or "RECEIPT" in code:
        phase, owner, kind = "PROOF", "TRUSTED_VERIFIER_OWNER", FailureClass.TRUSTED_PROOF
    elif status in ("INVALID_ACTION", "INVALID_REQUEST") or code.startswith(("ISSUER_", "INVALID_", "UNAUTHORIZED_", "VERIFICATION_PROFILE_")):
        phase, owner, kind = "INTAKE", "INTAKE_OWNER", FailureClass.INTAKE_AUTH
    elif status in ("QUOTA_EXHAUSTED", "MODEL_AT_CAPACITY"):
        phase, owner, kind = "PROVIDER", "PROVIDER_OWNER", FailureClass.PROVIDER_QUOTA
    elif status in ("EXECUTOR_UNAVAILABLE", "MODEL_UNSUPPORTED", "EXECUTOR_PERMISSION_DENIED", "EXECUTOR_FAILED") or code.startswith(("EXECUTOR_", "HEADLESS_")):
        phase, owner, kind = "PROVIDER", "PROVIDER_OWNER", FailureClass.PROVIDER_TRANSPORT
    elif code.startswith(("GITHUB_", "REMOTE_", "CLAIM_", "CHECKPOINT_")):
        phase, owner, kind = "REMOTE", "REMOTE_GITHUB_OWNER", FailureClass.REMOTE_GITHUB
    elif code.startswith(("MT01_", "PYTEST_FILTERED_", "PYTEST_CONFIG_", "PYTEST_UNMANAGED_", "REGISTERED_", "APPROVED_BROAD_")):
        phase, owner, kind = "SELECTION", "MT01_OWNER", FailureClass.MT01_SELECTOR
    elif code.startswith(("VERIFY_", "VERIFICATION_TOOL_", "PYTEST_SCRATCH_", "PYTEST_WORKER_", "XDIST_")):
        phase, owner, kind = "TEST", "RUNNER_ENVIRONMENT_OWNER", FailureClass.TEST_ENVIRONMENT
    if (kind is FailureClass.UNKNOWN and code == "TARGETED_VERIFY_NONZERO_OR_UNAVAILABLE"
            and result.get("repair_eligible") is True
            and result.get("verify_completed") is True and result.get("verify_exit_code") == 1
            and result.get("provider_return_determinate") is True):
        phase, owner, kind = "TEST", "EXECUTOR_SOURCE_OWNER", FailureClass.CODE_ASSERTION
    scope_reconciliation = (kind is FailureClass.SOURCE_GIT and result.get("verify_first_scope") is True
        and status == "VERIFICATION_FAILED" and result.get("repair_eligible") is True
        and result.get("verify_completed") is True and result.get("verify_exit_code") == 1
        and result.get("provider_return_determinate") is True)
    return FailureDiagnosis(phase, owner, kind,
        "SCOPED_REPAIR" if kind is FailureClass.CODE_ASSERTION else
        "SCOPED_RECONCILIATION" if scope_reconciliation else "OWNER_ESCALATION", (code,),
        ("ROOT_CAUSE_UNPROVEN",) if kind is FailureClass.UNKNOWN else ())


# This existing module has no pytest_* helpers that could be mistaken for
# hooks. Verification loads it as a runtime plugin, preserving registered argv.
try:
    import pytest
except ImportError:
    pytest = None

if pytest is not None:
    @pytest.hookimpl(tryfirst=True)
    def pytest_configure(config):
        import os
        import tempfile
        from pathlib import Path
        scratch = os.environ.get("AI_COMMERCE_PYTEST_SCRATCH")
        if not scratch or hasattr(config, "workerinput"):
            return
        root = Path(os.environ["AI_COMMERCE_PYTEST_REPO"]).resolve()
        location = Path(scratch).resolve()
        if location == root or root in location.parents or not location.is_dir():
            raise pytest.UsageError("PYTEST_SCRATCH_INSIDE_WORKSPACE_OR_MISSING")
        # A separate directory for every nested controller prevents fixture
        # collisions. Xdist's own worker basetemp allocation remains intact.
        session = Path(tempfile.mkdtemp(prefix="", dir=location))
        config.option.basetemp = str(session)
        config.inicfg["cache_dir"] = str(location / (session.name + "-cache"))
        if not getattr(config.option, "xmlpath", None):
            config.option.xmlpath = str(location / "junit.xml")
