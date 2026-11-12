"""Prevent retired WP/EPWO task replay on restored pre-WP EF02 paths."""
import pytest

from ai_commerce.remote.validation import (
    RemoteRequestError, reject_retired_wp_identifiers,
)
from ai_commerce.remote.worker import LocalBatchWorker


@pytest.mark.parametrize("key,value", [
    ("batch_id", "EPWO-REPLAY"),
    ("action_id", "WP01-a"),
    ("attempt_id", "WP06-a"),
    ("action_id", "wP03-mixed"),
])
def test_retired_machine_identifier_denied(key, value):
    batch = {"batch_id": "fresh-nonwp", "actions": [{
        "action_id": "safe", "attempt_id": "safe-attempt",
    }]}
    if key == "batch_id":
        batch["batch_id"] = value
    else:
        batch["actions"][0][key] = value
    with pytest.raises(RemoteRequestError, match="RETIRED_WP_NOT_DISPATCHABLE"):
        reject_retired_wp_identifiers(batch)


def test_unrelated_nonwp_prose_is_not_a_program_identifier():
    reject_retired_wp_identifiers({
        "batch_id": "safe-batch",
        "description": "Historical EPWO audit must remain read only",
        "actions": [{"action_id": "fresh", "attempt_id": "fresh-1"}],
    })


def test_direct_worker_no_claim_no_provider_on_retired_id(tmp_path):
    worker = LocalBatchWorker.__new__(LocalBatchWorker)
    worker.root = tmp_path
    worker.batch = {"batch_id": "safe-batch", "source_sha": "a" * 40}
    action = {
        "action_id": "WP01-replay", "attempt_id": "old-attempt",
        "executor": "codex", "model": "gpt-6.1-sol", "effort": "xhigh",
        "depends_on": [],
    }
    outcome = worker.execute_action(action)
    assert outcome["status"] == "INVALID_ACTION"
    assert outcome["error_code"] == "RETIRED_WP_NOT_DISPATCHABLE"
    assert outcome["executor_invoked"] is False
