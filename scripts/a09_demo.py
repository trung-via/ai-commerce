"""Synthetic A06 -> A08 -> A09 offline demo; no provider events or real metrics."""
from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path

from ai_commerce.analytics import AnalyticsLedger, match_import
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.distribution.assisted import export_package
from ai_commerce.distribution.receipt import PublicationJournal
from ai_commerce.e2e import run as e01_run


def _png() -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    header = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + bytes((60, 100, 180)) * 16) * 16
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    image = output / "synthetic.png"
    image.write_bytes(_png())
    e01_run("Synthetic abstract color study, no real publication", [image], output / "e01",
            width=64, height=96, fps=24, frames=4)
    metric = {
        "schema_version": "MetricDefinition.v1", "metric_key": "plays", "provider_name": "Synthetic plays",
        "definition_version": "fixture-1", "platform": "TIKTOK", "unit": "count",
        "definition": "Synthetic demonstration only", "denominator": None, "cohort": None,
        "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION,
    }
    plan = {
        "schema_version": "MeasurementPlan.v1", "plan_id": "synthetic-a09", "campaign_ref": "synthetic",
        "primary_metric": "plays", "denominator": None, "metric_definition_version": "fixture-1",
        "platform": "TIKTOK", "surface": "VIDEO", "account_ref": {"namespace": "internal_alias", "id": "demo"},
        "measurement_window": ["2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"],
        "timezone": "UTC+07:00", "cohort": None, "source_mode": "MANUAL_IMPORT",
        "attribution_tier": "NOT_AVAILABLE", "missingness_rule": "Unknown remains null",
        "source_status": VERIFICATION, "metric_definitions": [metric],
    }
    (output / "plan.json").write_bytes(canonical_bytes(plan) + b"\n")
    ledger = AnalyticsLedger(output / "analytics.sqlite")
    imports = {}
    for name, status, value in (("zero", "AVAILABLE", 0), ("unknown", "NOT_AVAILABLE", None)):
        source = output / f"{name}.json"
        source.write_bytes(canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": [{
            "provider_post_id": "synthetic-00007", "public_url": None, "metric_key": "plays",
            "provider_name": "Synthetic plays", "definition_version": "fixture-1", "status": status, "value": value,
        }]}) + b"\n")
        imports[name] = ledger.import_file(source, plan=plan, profile="tiktok.manual.v1", file_format="json",
                                           source_kind="SYNTHETIC_FIXTURE")["import"]["import_id"]
    journal = PublicationJournal(output / "publications.sqlite")
    proof = output / "synthetic-receipt.txt"
    proof.write_bytes(b"SYNTHETIC fixture only: no real platform post, account access or receipt.\n")
    receipts = {}
    for name, account, with_receipt in (
            ("exact", "demo", True), ("wrong_account", "other", True),
            ("missing_receipt", "demo", False), ("duplicate", "demo", True)):
        package = output / ("package-" + name)
        export_package(output / "e01", package, platform="TIKTOK", surface="VIDEO", account="internal_alias:" + account)
        ident = journal.create(package, idempotency_key="synthetic-" + name)["publication_id"]
        journal.record(ident, "SUBMISSION_REPORTED")
        if with_receipt:
            journal.record(ident, "PUBLICATION_RECEIPT", provider_post_id="synthetic-00007", evidence_file=proof)
        receipts[name] = ident
    results = {}
    for name, import_id, candidates in (
            ("exact_zero", imports["zero"], [receipts["exact"]]),
            ("exact_unknown", imports["unknown"], [receipts["exact"]]),
            ("wrong_account", imports["zero"], [receipts["wrong_account"]]),
            ("missing_receipt", imports["zero"], [receipts["missing_receipt"]]),
            ("ambiguous", imports["zero"], [receipts["exact"], receipts["duplicate"]])):
        result = match_import(ledger, import_id, publication_db=journal.path, publication_ids=candidates)
        (output / (name + "-result.json")).write_bytes(canonical_bytes(result) + b"\n")
        item = result["matches"][0]
        results[name] = {"status": result["status"], "reason_codes": item["reason_codes"],
                         "metric_status": item["observation"]["status"], "value": item["observation"]["value"]}
    return {"source_kind": "SYNTHETIC_FIXTURE", "verification": VERIFICATION, "results": results,
            "import_id": imports["zero"], "publication_id": receipts["exact"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; requires local ffmpeg/ffprobe")
    result = run(parser.parse_args().output)
    expected = {"exact_zero": "MATCHED", "exact_unknown": "MATCHED", "wrong_account": "UNMATCHED",
                "missing_receipt": "PARTIAL", "ambiguous": "PARTIAL"}
    if {k: v["status"] for k, v in result["results"].items()} != expected:
        raise RuntimeError("SYNTHETIC_DEMO_MATCH_MISMATCH")
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
