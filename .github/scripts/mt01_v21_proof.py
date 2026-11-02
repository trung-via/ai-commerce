"""Offline verification of MT-01 v2.1 GitHub artifact bytes.

This validates internal integrity against an exact-head workflow run, not the
independent affected-test selection oracle. Caller must additionally require
trusted CI jobs and a separately approved coverage policy before merging.
"""
from __future__ import annotations

import hashlib
import io
import json
import posixpath
import xml.etree.ElementTree as ET
import zipfile

MAX_ZIP_BYTES = 24 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_receipt_zip(
    payload: bytes, *, expected_head: str, expected_base: str, os_name: str,
) -> bool:
    """Reject malformed, missing, inconsistent or unverifiable receipts."""
    try:
        if not isinstance(payload, bytes) or not 64 < len(payload) <= MAX_ZIP_BYTES:
            return False
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = {}
            names = archive.namelist()
            if len(names) > 100 or len(names) != len(set(names)):
                return False
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                return False
            for info in archive.infolist():
                name = info.filename
                if (info.is_dir() or "\\" in name or name.startswith("/") or
                    posixpath.normpath(name) != name or
                    ".." in name.split("/") or info.file_size > MAX_UNCOMPRESSED_BYTES or
                    ((info.external_attr >> 16) & 0o170000) == 0o120000):
                    return False
                basename = name.rsplit("/", 1)[-1]
                if basename in files:
                    return False
                files[basename] = archive.read(info)
        if not {"evidence.json", "plan.json"} <= set(files):
            return False
        receipt = json.loads(files["evidence.json"])
        plan = json.loads(files["plan.json"])
        if not isinstance(receipt, dict) or not isinstance(plan, dict):
            return False
        expected_platform = {"ubuntu-latest": "Linux", "windows-latest": "Windows"}.get(os_name)
        if (receipt.get("schema") != "MT01_VERIFICATION_EVIDENCE_V2" or
            receipt.get("base_sha") != expected_base or
            receipt.get("pr_head_sha") != expected_head or
            receipt.get("platform") != expected_platform or
            receipt.get("source_sha") != plan.get("candidate_sha") or
            receipt.get("base_sha") != plan.get("base_sha") or
            receipt.get("selection_mode") != plan.get("mode") or
            plan.get("mode") not in ("TARGETED", "AFFECTED", "BOUNDED_BROAD",
                                   "NO_PYTEST_APPLICABLE")):
            return False
        canonical = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        if receipt.get("plan_sha256") != _sha(canonical):
            return False
        tests = receipt.get("selected_test_files")
        if (not isinstance(tests, list) or tests != plan.get("selected_tests") or
            len(tests) != len(set(tests)) or
            any(not isinstance(t, str) or not t.startswith("tests/test_") or
                not t.endswith(".py") for t in tests)):
            return False
        if not isinstance(plan.get("changed_paths"), list) or not plan["changed_paths"]:
            return False
        if not all(isinstance(receipt.get(k), str) and len(receipt[k]) == 64
                   for k in ("selector_sha256", "registry_sha256")):
            return False
        chunks = receipt.get("chunks")
        if not isinstance(chunks, list):
            return False
        if plan.get("mode") == "NO_PYTEST_APPLICABLE":
            return tests == [] and chunks == [] and all(
                receipt.get(k) == 0 for k in
                ("collected_count", "executed_count", "passed_count", "failed_count",
                 "error_count", "skipped_count"))
        if not chunks or len(chunks) > 100:
            return False
        covered = []
        totals = dict(collected_count=0, executed_count=0, passed_count=0,
                      failed_count=0, error_count=0, skipped_count=0)
        for i, chunk in enumerate(chunks):
            targets = chunk.get("test_files")
            if (not isinstance(targets, list) or not targets or len(targets) > 40 or
                any(not isinstance(t, str) for t in targets) or
                chunk.get("exit_code") != 0):
                return False
            covered.extend(targets)
            xml_name = f"chunk-{i}.xml"
            if xml_name not in files or chunk.get("junit_sha256") != _sha(files[xml_name]):
                return False
            cases = list(ET.fromstring(files[xml_name]).iter("testcase"))
            if not cases or chunk.get("testcases") != len(cases):
                return False
            observed = sorted(str(case.get("classname", "")) + "::" +
                              str(case.get("name", "")) for case in cases)
            if chunk.get("observed_cases") != observed:
                return False
            observed_counts = dict(passed=0, skipped=0, failed=0, errors=0)
            for case in cases:
                if case.find("failure") is not None:
                    observed_counts["failed"] += 1
                elif case.find("error") is not None:
                    observed_counts["errors"] += 1
                elif case.find("skipped") is not None:
                    observed_counts["skipped"] += 1
                else:
                    observed_counts["passed"] += 1
            if (chunk.get("counts") != observed_counts or
                observed_counts["failed"] or observed_counts["errors"]):
                return False
            totals["collected_count"] += len(cases)
            totals["executed_count"] += len(cases) - observed_counts["skipped"]
            for category, total_name in (("passed", "passed_count"),
                                         ("skipped", "skipped_count"),
                                         ("failed", "failed_count"), ("errors", "error_count")):
                totals[total_name] += observed_counts[category]
        if covered != tests or len(covered) != len(set(covered)):
            return False
        return totals["executed_count"] > 0 and all(
            receipt.get(name) == value for name, value in totals.items())
    except (ValueError, TypeError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile,
            RuntimeError, OverflowError, MemoryError):
        return False
