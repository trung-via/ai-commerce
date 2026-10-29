"""Bounded EF02 DAG scheduler. Threads supervise real EF01 child processes."""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

def run_schedule(batch: dict, work, *, max_workers: int | None = None) -> dict:
    actions = {a["action_id"]: a for a in batch["actions"]}
    budget = max_workers or batch.get("max_parallel", 2)
    if budget < 1:
        raise ValueError("INVALID_PARALLEL_LIMIT")
    pending = set(actions)
    active = {}
    held_resources: set[str] = set()
    results: dict[str, dict] = {}
    max_active = 0

    def call(action):
        start = time.time()
        try:
            result = work(action)
            if not isinstance(result, dict) or "status" not in result:
                raise ValueError("WORKER_RETURN_MALFORMED")
        except Exception as exc:
            result = {"status": "EXECUTION_UNKNOWN",
                      "error_code": type(exc).__name__, "diagnostic": str(exc)}
        result.setdefault("action_id", action["action_id"])
        result.setdefault("attempt_id", action["attempt_id"])
        result["scheduler_started_at"] = start
        result["scheduler_finished_at"] = time.time()
        return result

    with ThreadPoolExecutor(max_workers=budget) as pool:
        while pending or active:
            progress = False
            for aid in sorted(tuple(pending)):
                deps = actions[aid]["depends_on"]
                if any(parent in results and results[parent]["status"] != "CANDIDATE_READY"
                       for parent in deps):
                    pending.remove(aid)
                    results[aid] = {"action_id": aid, "attempt_id": actions[aid]["attempt_id"],
                                    "status": "BLOCKED_DEPENDENCY",
                                    "error_code": "PREREQUISITE_NOT_SUCCESSFUL"}
                    progress = True
            ready = sorted(
                (actions[aid] for aid in pending if all(
                    p in results and results[p]["status"] == "CANDIDATE_READY"
                    for p in actions[aid]["depends_on"])),
                key=lambda a: (-a.get("priority", 50), a["action_id"])
            )
            for action in ready:
                if len(active) >= budget:
                    break
                resources = set(action.get("resources", []))
                if resources & held_resources:
                    continue
                dispatch_action = dict(action)
                dispatch_action["resolved_dependencies"] = [results[p] for p in action["depends_on"]]
                future = pool.submit(call, dispatch_action)
                active[future] = (action["action_id"], resources)
                held_resources.update(resources)
                pending.remove(action["action_id"])
                progress = True
            max_active = max(max_active, len(active))
            if active:
                completed, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
                for future in completed:
                    aid, resources = active.pop(future)
                    held_resources.difference_update(resources)
                    results[aid] = future.result()
                    progress = True
            if not active and pending and not progress:
                raise RuntimeError("NO_RUNNABLE_ACTIONS")
    ordered = [results[action["action_id"]] for action in batch["actions"]]
    failed = sum(item["status"] != "CANDIDATE_READY" for item in ordered)
    return {"kind": "AI_COMMERCE_REMOTE_BATCH_RESULT", "version": 1,
            "batch_id": batch["batch_id"], "source_sha": batch["source_sha"],
            "status": "COMPLETE" if failed == 0 else "PARTIAL_OR_FAILED",
            "total": len(ordered), "succeeded": len(ordered) - failed,
            "unsuccessful": failed, "scheduler_max_active": max_active,
            # COMPLETE is dispatch completion, never a combined-source PASS.
            "aggregate_verification": {"state": "UNPROVEN", "reason": "ACTION_RESULTS_ARE_NOT_COMBINED_SOURCE_EVIDENCE"},
            "actions": ordered}
