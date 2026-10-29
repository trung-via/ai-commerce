"""Reproducible SYNTHETIC A10 v1 demonstration; local CLI/APIs, no provider.

Run from the repository: python scripts/a10_demo.py --output .local/a10-demo-1
For byte-identical replay, retain that package and add --replay-from .local/a10-demo-1
with a new --output directory. Fresh imports retain M01's real recorded_at clock.
No files are overwritten, clocks patched or uncertain operations retried.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Permit a source checkout without installing a package or contacting an index.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_commerce.cli import main as commerce_main
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes
from ai_commerce.creative import CreativeStore, brief_digest, intake_brief
from ai_commerce.opportunity.core.schemas import SOURCE_STATUS
from ai_commerce.opportunity.core.models import parse_json
from ai_commerce.opportunity.signals import SignalLedger, ledger_path, read_local

AS_OF = "2026-10-29T12:00:00Z"
CSV_TOPIC = "SYNTHETIC — khoảng nghỉ yên tĩnh"
JSON_TOPIC = "SYNTHETIC — câu chuyện chiếc lá"


def _write(path: Path, value: dict | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else canonical_bytes(value) + b"\n"
    with path.open("xb") as output:
        output.write(raw)


def _cli(*args: str) -> dict:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = commerce_main(["opportunity", *map(str, args)])
    if code:
        raise ValueError("SYNTHETIC_DEMO_CLI_FAILED: " + stderr.getvalue())
    return json.loads(stdout.getvalue())


def _row(topic: str, day: int, value: int | None) -> dict:
    return {
        "topic": topic, "signal_type": "TOPIC", "source_type": "MANUAL_NOTES",
        "locale": "vi-VN", "geo": "VN", "query_context": "synthetic:counting-protocol.v1",
        "observed_at": f"2026-10-{day:02d}T08:00:00+07:00", "observation_window": None,
        "collected_at": AS_OF, "timezone": "UTC+07:00", "unit": "count", "denominator": None,
        "status": "NOT_AVAILABLE" if value is None else "AVAILABLE", "value": value,
        "rights_status": "UNKNOWN",
    }


def _snapshot(raw: bytes, kind: str) -> dict:
    return {
        "schema_version": "SignalSourceSnapshot.v1", "source_id": "synthetic-demo-" + kind,
        "source_kind": "SYNTHETIC_FIXTURE", "source_ref": "synthetic://a10-demo/" + kind,
        "captured_at": AS_OF, "collected_at": AS_OF, "sha256": sha256_bytes(raw),
        "rights_status": "UNKNOWN", "verification_status": SOURCE_STATUS, "geo": "VN", "locale": "vi-VN",
        "notes": "SYNTHETIC fixture only; toy values are not market metrics or a provider export.",
    }


def _comparison() -> dict:
    return {
        "schema_version": "TrendComparisonContext.v1", "metric": "synthetic_manual_mentions",
        "metric_kind": "COUNT", "baseline_id": "synthetic-counting-protocol.v1",
        "normalization_id": "raw-count.v1", "query_batch": None, "relative_scale": None,
        "window_kind": "POINT", "evidence_refs": ["synthetic://a10-demo/comparison"], "source_status": SOURCE_STATUS,
    }


def run_demo(output: Path, *, replay_from: Path | None = None) -> dict:
    output = Path(output)
    if output.exists():
        raise ValueError("SYNTHETIC_DEMO_REQUIRES_NEW_OUTPUT_DIRECTORY")
    source_manifest = None
    if replay_from is not None:
        replay_from = Path(replay_from)
        source_manifest = parse_json(read_local(replay_from / "demo.json"))
        if source_manifest.get("schema_version") != "A10SyntheticDemo.v1" or source_manifest.get("data_kind") != "SYNTHETIC_FIXTURE":
            raise ValueError("SYNTHETIC_DEMO_REPLAY_PACKAGE_INVALID")
    # Use M01's portable path policy before creating any local output.
    ledger_path(output / "signals.sqlite", create=True)
    if replay_from is not None:
        for name in ("signals.sqlite", "audience.sqlite"):
            raw = read_local(replay_from / name)
            if sha256_bytes(raw) != source_manifest["journal_sha256"][name]:
                raise ValueError("SYNTHETIC_DEMO_REPLAY_JOURNAL_HASH_MISMATCH")
            _write(output / name, raw)
    rows = [_row(CSV_TOPIC, day, value) for day, value in ((27, 0), (28, 0), (29, 4))]
    columns = {
        "topic": "chủ_đề", "signal_type": "loại", "source_type": "nguồn",
        "locale": "ngôn_ngữ", "geo": "vùng", "query_context": "ngữ_cảnh",
        "observed_at": "thời_điểm", "collected_at": "thu_thập", "timezone": "múi_giờ",
        "unit": "đơn_vị", "denominator": "mẫu_số", "status": "trạng_thái", "value": "giá_trị",
        "rights_status": "quyền",
    }
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(columns.values()), lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({header: "" if row[field] is None else row[field] for field, header in columns.items()})
    csv_raw = b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")
    json_raw = canonical_bytes({"schema_version": "ManualSignalRows.v1",
                               "rows": [_row(JSON_TOPIC, 28, 0), _row(JSON_TOPIC, 29, None)]}) + b"\n"
    mapping = {"profile_id": "synthetic.vi-VN.manual.v1", "mapping_version": "1",
               "adapter_version": "manual-signal.v1", "native_export_layout_verified": False, "columns": columns}
    _write(output / "inputs/signals.csv", csv_raw)
    _write(output / "inputs/signals.json", json_raw)
    _write(output / "inputs/mapping.json", mapping)
    _write(output / "inputs/csv-snapshot.json", _snapshot(csv_raw, "csv"))
    _write(output / "inputs/json-snapshot.json", _snapshot(json_raw, "json"))
    imports, import_execution = [], {}
    for kind in ("csv", "json"):
        args = ["import", "--file", output / f"inputs/signals.{kind}",
                "--snapshot", output / f"inputs/{kind}-snapshot.json", "--ledger", output / "signals.sqlite",
                "--format", kind]
        if kind == "csv":
            args += ["--mapping", output / "inputs/mapping.json", "--dialect", "comma"]
        result = _cli(*args)
        duplicate = _cli(*args)  # Explicit determinate idempotency check, not an uncertain retry.
        if result["duplicate"] != (replay_from is not None) or not duplicate["duplicate"]:
            raise ValueError("SYNTHETIC_DEMO_IDEMPOTENCY_FAILED")
        imports.append(result)
        import_execution[kind] = {"initial_duplicate": result["duplicate"], "repeat_duplicate": duplicate["duplicate"]}
        # Immutable content is distinct from this invocation's duplicate flag.
        _write(output / f"{kind}-import.json", {"import": result["import"], "signals": result["signals"]})
        _write(output / f"{kind}-inspection.json", _cli("inspect", "--ledger", output / "signals.sqlite",
                                                      "--import-id", result["import"]["import_id"]))
    contexts = {item["import"]["import_id"]: _comparison() for item in imports}
    _write(output / "inputs/comparisons.json", contexts)
    trends = []
    for kind, topic, item in zip(("csv", "json"), (CSV_TOPIC, JSON_TOPIC), imports):
        _write(output / f"inputs/{kind}-comparison.json", {item["import"]["import_id"]: _comparison()})
        trend = _cli("trend", "--ledger", output / "signals.sqlite", "--import-id", item["import"]["import_id"],
                     "--topic", topic, "--contexts", output / f"inputs/{kind}-comparison.json", "--as-of", AS_OF)
        trends.append(trend)
        _write(output / f"{kind}-trend.json", trend)
    ref = _cli("audience", "ref", "--signals", output / "signals.sqlite",
               "--import-id", imports[0]["import"]["import_id"], "--signal-id", imports[0]["signals"][0]["signal_id"])
    notes = {
        "schema_version": "AudienceNotes.v1", "data_kind": "SYNTHETIC_FIXTURE",
        "source_note": "  Ghi chú TỔNG HỢP GIẢ cho minh họa; không có người tham gia thật.\r\n  ",
        "noted_at": AS_OF, "audience_segment": None,
        "statements": [
            {"kind": "JOB_TO_BE_DONE", "status": "OBSERVED_USER_SUPPLIED",
             "text": "Trong tình huống giả định, muốn có một khoảng nghỉ nhẹ nhàng.", "evidence_refs": [ref]},
            {"kind": "CONTENT_CONTEXT", "status": "INFERRED", "text": "Thử kể chuyện qua chuyển động của chiếc lá.",
             "uncertainty": "Diễn giải sáng tạo chưa được kiểm chứng."},
            {"kind": "COUNTEREXAMPLE", "status": "OBSERVED_USER_SUPPLIED", "text": "Một ghi chú giả khác thích nhịp vui."},
        ],
    }
    note_raw = b"\xef\xbb\xbf" + (json.dumps(notes, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write(output / "inputs/audience.json", note_raw)
    insight = _cli("audience", "append", "--file", output / "inputs/audience.json", "--ledger", output / "audience.sqlite",
                   "--signals", output / "signals.sqlite")["insight"]
    _write(output / "audience-insight.json", insight)
    proposals = _cli("propose", "--signals", output / "signals.sqlite", "--series", output / "csv-trend.json",
                     "--series", output / "json-trend.json", "--audience-ledger", output / "audience.sqlite",
                     "--insight-id", insight["insight_id"], "--as-of", AS_OF)
    _write(output / "proposals.json", proposals)
    main_proposal = next(p for p in proposals["proposals"] if p["topic"] == CSV_TOPIC)
    brief = _cli("bridge", "--proposal", output / "proposals.json", "--proposal-id", main_proposal["proposal_id"],
                 "--synthetic", "--store", output / "creative")
    _write(output / "brief.json", brief)
    freeform_text = "  SYNTHETIC — Một đám mây học cách thì thầm. 🌧️\r\n  "
    _write(output / "inputs/human.txt", b"\xef\xbb\xbf" + freeform_text.encode("utf-8"))
    freeform = _cli("bridge", "--text-file", output / "inputs/human.txt", "--synthetic", "--store", output / "creative")
    _write(output / "freeform-brief.json", freeform)
    store = CreativeStore(output / "creative")
    for created in (brief, freeform):
        if intake_brief(created) != created or store.load_brief(brief_digest(created)) != created:
            raise ValueError("SYNTHETIC_DEMO_A03_REPLAY_FAILED")
    # Read exact raw evidence through the real replay APIs, then compare bytes.
    signals = SignalLedger(output / "signals.sqlite")
    for item, expected in zip(imports, (csv_raw, json_raw)):
        record = signals.get(item["import"]["import_id"])["import"]
        # M01 exposes replay, not a public raw-bytes getter. Read its BLOB
        # explicitly in read-only mode after replay, without inventing an API.
        with sqlite3.connect((output / "signals.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
            retained = bytes(db.execute("SELECT payload FROM raw_evidence WHERE sha256=?", (record["raw_sha256"],)).fetchone()[0])
        if retained != expected:
            raise ValueError("SYNTHETIC_DEMO_RAW_REPLAY_FAILED")
    manifest = {
        "schema_version": "A10SyntheticDemo.v1", "data_kind": "SYNTHETIC_FIXTURE",
        "notice": "Toy data only, not market metrics, provider exports, audience research or validated demand.",
        "as_of": AS_OF, "locale": "vi-VN", "rights_status": "UNKNOWN", "publication_status": "NOT_ATTEMPTED",
        "external_provider_calls": 0, "concept_generation_performed": False,
        "raw_sha256": {"csv": sha256_bytes(csv_raw), "json": sha256_bytes(json_raw), "audience": sha256_bytes(note_raw)},
        "journal_sha256": {name: sha256_bytes(read_local(output / name)) for name in ("signals.sqlite", "audience.sqlite")},
        "import_ids": [i["import"]["import_id"] for i in imports],
        "trend_statuses": [t["hypothesis"]["status"] for t in trends],
        "sparse_conditions": trends[1]["hypothesis"]["conditions"],
        "insight_id": insight["insight_id"], "proposal_ids": [p["proposal_id"] for p in proposals["proposals"]],
        "brief_id": brief["brief_id"], "brief_digest": brief_digest(brief),
        "freeform_brief_digest": brief_digest(freeform), "duplicate_imports_checked": True,
        "raw_bytes_replayed": True, "a03_intake_and_store_replayed": True,
    }
    if source_manifest is not None and manifest != source_manifest:
        raise ValueError("SYNTHETIC_DEMO_REPLAY_RESULT_MISMATCH")
    _write(output / "demo.json", manifest)
    _write(output / "execution.json", {
        "data_kind": "SYNTHETIC_FIXTURE", "mode": "REPLAY" if replay_from is not None else "FRESH_IMPORT",
        "imports": import_execution,
        "clock_policy": "M01 actual recorded_at is preserved; replay copies exact retained journals and reruns real CLI APIs.",
    })
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="A new local directory")
    parser.add_argument("--replay-from", type=Path, help="Retained synthetic demo package for exact offline replay")
    args = parser.parse_args(argv)
    try:
        result = run_demo(args.output, replay_from=args.replay_from)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
