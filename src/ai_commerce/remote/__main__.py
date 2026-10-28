"""EF02 command line: validate trusted batch and execute on Windows."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from .validation import (RemoteRequestError, validate_batch, verify_registry,
                         check_source)
from .transport import GitHubTransport
from .worker import LocalBatchWorker, _write_json
from .reuse import load_approved_candidates
from .scheduler import run_schedule


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_commerce.remote")
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, default=Path("."))
    parser.add_argument("--issuer-login", required=True)
    parser.add_argument("--owner-login", required=True)
    parser.add_argument("--brain-issuers", default="")
    parser.add_argument("--issue-number", type=int, default=0)
    parser.add_argument("--max-parallel", type=int, default=0)
    args = parser.parse_args(argv)
    checkout = args.checkout.resolve()
    try:
        packet = json.loads(args.request_file.read_text(encoding="utf-8-sig"))
        profiles = verify_registry(checkout / ".github" / "ef02" / "verify-profiles.json")
        approved_reuse = load_approved_candidates(checkout / ".github" / "ef02" / "approved-candidates.v1.json")
        batch = validate_batch(packet, approved_reuse=approved_reuse,
                               schema=checkout / "contracts" /
                               "RemoteActionBatch.v1.schema.json",
                               registry=profiles, issuer_login=args.issuer_login,
                               owner_login=args.owner_login,
                               brain_issuers=tuple(v.strip() for v in
                                                   args.brain_issuers.split(",") if v.strip()))
        check_source(checkout, batch["source_sha"])
    except Exception as exc:
        print("EF02_REQUEST_REJECTED: " + type(exc).__name__ + ": " + str(exc),
              file=sys.stderr)
        return 2
    if args.command == "validate":
        print(json.dumps({"status": "VALIDATED", "batch_id": batch["batch_id"],
                          "count": len(batch["actions"])}))
        return 0
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        github = GitHubTransport(repo, token)
        worker = LocalBatchWorker(checkout=checkout, batch=batch, profiles=profiles,
                                  github=github)
        host_slots = int(os.environ.get("EF02_HOST_SLOTS", "2"))
        if host_slots < 1 or host_slots > 128:
            raise ValueError("EF02_HOST_SLOTS must be in 1..128")
        requested = args.max_parallel or batch.get("max_parallel", 2)
        if requested < 1:
            raise ValueError("INVALID_MAX_PARALLEL")
        report = run_schedule(batch, worker.execute_action,
                              max_workers=min(host_slots, requested))
        report["host_parallel_slots"] = host_slots
        report["github_workflow_run_id"] = os.environ.get("GITHUB_RUN_ID")
        report["github_issue_number"] = args.issue_number or None
        report["feedback_status"] = "NOT_ATTEMPTED"
        result_path = worker.root / "batch_result.json"
        # This deliberately reports delivery uncertainty without rerunning model calls.
        if args.issue_number:
            summary = ["EF02 batch " + batch["batch_id"] + ": " + report["status"],
                       "GitHub workflow run: " + str(report["github_workflow_run_id"] or "UNKNOWN"),
                       "Results: " + str(report["succeeded"]) + "/" + str(report["total"])]
            for item in report["actions"]:
                summary.append("- " + item["action_id"] + ": " + item["status"] +
                               (" (" + item.get("candidate_ref", "") + ")" if item.get("candidate_ref") else ""))
            summary.append("Full per-attempt diagnostics are in this workflow's artifact.")
            try:
                github.comment(args.issue_number, "\n".join(summary))
                report["feedback_status"] = "GITHUB_ISSUE_COMMENT_PUBLISHED"
            except Exception as exc:
                report["feedback_status"] = "FEEDBACK_PUBLICATION_FAILED"
                report["feedback_error_code"] = type(exc).__name__
        _write_json(result_path, report)
        print(json.dumps({"batch_id": report["batch_id"], "status": report["status"],
                          "succeeded": report["succeeded"], "total": report["total"],
                          "feedback_status": report["feedback_status"]}))
        return 0 if report["status"] == "COMPLETE" and report["feedback_status"] != "FEEDBACK_PUBLICATION_FAILED" else 1
    except Exception as exc:
        print("EF02_EXECUTION_ERROR: " + type(exc).__name__ + ": " + str(exc),
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
