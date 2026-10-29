"""A09 E02: synthetic offline imports matched to actual A08 journal replays."""
from __future__ import annotations

import csv
import io
import json
import socket
import sqlite3
from hashlib import sha256

import pytest

from ai_commerce.analytics import AnalyticsError, AnalyticsLedger, LedgerError, match_import
from ai_commerce.analytics.model import digest
from ai_commerce.analytics.schemas import VERIFICATION
from ai_commerce.cli import main
from ai_commerce.core.provenance import canonical_bytes
from ai_commerce.distribution.assisted import export_package
from ai_commerce.distribution.receipt import PublicationJournal


@pytest.fixture
def plan():
    return {
        "schema_version": "MeasurementPlan.v1", "plan_id": "e02-synthetic",
        "campaign_ref": "e02-fixture", "primary_metric": "plays", "denominator": None,
        "metric_definition_version": "fixture-1", "platform": "TIKTOK", "surface": "VIDEO",
        "account_ref": {"namespace": "tiktok_open_id", "id": "00001"},
        "measurement_window": ["2026-10-01T00:00:00+07:00", "2026-10-03T00:00:00+07:00"],
        "timezone": "UTC+07:00", "cohort": None, "source_mode": "MANUAL_IMPORT",
        "attribution_tier": "NOT_AVAILABLE", "missingness_rule": "Unknown is null, never zero",
        "source_status": VERIFICATION,
        "metric_definitions": [{
            "schema_version": "MetricDefinition.v1", "metric_key": "plays",
            "provider_name": "Synthetic plays", "definition_version": "fixture-1",
            "platform": "TIKTOK", "unit": "count", "definition": "User-declared fixture count",
            "denominator": None, "cohort": None,
            "comparison_policy": "NO_CROSS_PROVIDER_EQUIVALENCE", "source_status": VERIFICATION,
        }],
    }


def row(post="00007", *, url=None, value=0, status="AVAILABLE"):
    return {"provider_post_id": post, "public_url": url, "metric_key": "plays",
            "provider_name": "Synthetic plays", "definition_version": "fixture-1",
            "status": status, "value": value}


def imported(tmp_path, plan, rows=None, *, file_format="json", source_kind="SYNTHETIC_FIXTURE"):
    rows = [row()] if rows is None else rows
    path = tmp_path / ("manual." + file_format)
    if file_format == "json":
        raw = canonical_bytes({"schema_version": "ManualAnalyticsRows.v1", "rows": rows})
    else:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(row()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        raw = stream.getvalue().encode("utf-8")
    path.write_bytes(raw)
    ledger = AnalyticsLedger(tmp_path / "analytics.sqlite")
    result = ledger.import_file(path, plan=plan, profile=plan["platform"].lower() + ".manual.v1",
                                file_format=file_format, source_kind=source_kind)
    return ledger, result["import"]["import_id"]


@pytest.fixture
def publication(tmp_path, monkeypatch):
    # Only A06 rendering is stubbed, as in the A08 unit fixtures. A08 exports,
    # package validation, journal events, get(), and evidence replay are real.
    source = tmp_path / "source"
    source.mkdir()
    video = b"SYNTHETIC E02 media fixture; not a real rendered MP4"
    (source / "video.mp4").write_bytes(video)
    (source / "e01-manifest.json").write_bytes(canonical_bytes({
        "run_id": "e01-synthetic", "publication_status": "NOT_ATTEMPTED",
        "output_sha256": sha256(video).hexdigest(),
    }))
    monkeypatch.setattr("ai_commerce.distribution.assisted.verify_e01", lambda _: {"state": "DELIVERED"})
    journal = PublicationJournal(tmp_path / "publications.sqlite")
    number = 0

    def create(*, platform="TIKTOK", surface="VIDEO", account="tiktok_open_id:00001",
               post="00007", receipt=True, url=None, removed=False):
        nonlocal number
        number += 1
        package = tmp_path / f"package-{number}"
        export_package(source, package, platform=platform, surface=surface, account=account)
        ident = journal.create(package, idempotency_key=f"fixture-{number}")["publication_id"]
        journal.record(ident, "SUBMISSION_REPORTED")
        if receipt:
            proof = tmp_path / f"receipt-{number}.txt"
            proof.write_bytes(b"Synthetic manual receipt; no real provider event")
            journal.record(ident, "PUBLICATION_RECEIPT", provider_post_id=post, evidence_file=proof)
        if url is not None:
            visibility = tmp_path / f"visibility-{number}.txt"
            visibility.write_bytes(b"Separate synthetic visibility evidence")
            journal.record(ident, "VISIBILITY_EVIDENCE", public_url=url, evidence_file=visibility)
        if removed:
            removal = tmp_path / f"removal-{number}.txt"
            removal.write_bytes(b"Synthetic removal evidence")
            journal.record(ident, "REMOVAL_REPORTED", evidence_file=removal)
        return journal.get(ident)

    create.journal = journal
    return create


def match(ledger, ident, publication, *receipts):
    return match_import(ledger, ident, publication_db=publication.journal.path,
                        publication_ids=[p["publication_id"] for p in receipts])


@pytest.mark.parametrize("file_format", ["csv", "json"])
@pytest.mark.parametrize("platform,surface,namespace", [
    ("TIKTOK", "VIDEO", "tiktok_open_id"), ("TIKTOK", "PROFILE_VIDEO", "internal_alias"),
    ("YOUTUBE", "SHORT", "youtube_channel_id"), ("FACEBOOK", "PAGE_REEL", "facebook_page_id"),
    ("INSTAGRAM", "REEL", "instagram_user_id"),
])
def test_exact_match_reopens_actual_receipt_and_preserves_provenance(
        tmp_path, plan, publication, monkeypatch, file_format, platform, surface, namespace):
    plan.update(platform=platform, surface=surface, account_ref={"namespace": namespace, "id": "00001"})
    plan["metric_definitions"][0]["platform"] = platform
    ledger, ident = imported(tmp_path, plan, file_format=file_format,
                             source_kind="USER_PROVIDED_PLATFORM_EXPORT")
    receipt = publication(platform=platform, surface=surface, account=namespace + ":00001")
    original_get = PublicationJournal.get
    calls = []

    def checked_get(self, publication_id):
        calls.append(publication_id)
        return original_get(self, publication_id)

    monkeypatch.setattr(PublicationJournal, "get", checked_get)
    before = {p: p.read_bytes() for p in (ledger.path, publication.journal.path)}
    result = match(ledger, ident, publication, receipt)
    assert calls == [receipt["publication_id"]]
    assert result["status"] == "MATCHED"
    item = result["matches"][0]
    assert item["publication"] == receipt
    assert item["publication"]["account_ref"] == namespace + ":00001"
    assert item["publication"]["provider_post_id"] == "00007"
    assert item["publication"]["visibility"] == "UNKNOWN"
    assert item["observation"] == ledger.inspect(ident)["observations"][0]
    assert item["observation"]["source"]["source_kind"] == "USER_PROVIDED_PLATFORM_EXPORT"
    assert item["observation"]["source_status"] == result["source_status"] == VERIFICATION
    assert item["observation"]["value"] == 0
    assert result["publications"] == [{"receipt": receipt, "snapshot_sha256": digest(receipt)}]
    assert {p: p.read_bytes() for p in before} == before
    assert result["result_sha256"] == digest({k: v for k, v in result.items() if k != "result_sha256"})


@pytest.mark.parametrize("changes", [
    {"account": "tiktok_open_id:other"}, {"account": "internal_alias:00001"},
    {"surface": "PROFILE_VIDEO"},
    {"platform": "YOUTUBE", "surface": "VIDEO", "account": "internal_alias:00001"},
])
def test_wrong_account_namespace_platform_or_surface_never_bind(tmp_path, plan, publication, changes):
    if changes.get("platform") == "YOUTUBE":
        plan["account_ref"]["namespace"] = "internal_alias"
    ledger, ident = imported(tmp_path, plan)
    receipt = publication(**changes)
    result = match(ledger, ident, publication, receipt)
    assert result["status"] == "UNMATCHED"
    assert result["matches"][0]["reason_codes"] == ["TARGET_MISMATCH"]
    assert result["matches"][0]["publication_id"] is None


@pytest.mark.parametrize("post", ["7", "00007 ", "00007X", "0000a"])
def test_post_ids_are_exact_without_trimming_or_numeric_coercion(tmp_path, plan, publication, post):
    ledger, ident = imported(tmp_path, plan, [row(post)])
    result = match(ledger, ident, publication, publication())
    assert result["status"] == "UNMATCHED"
    assert result["matches"][0]["reason_codes"] == ["POST_ID_MISMATCH"]


def test_distinct_receipts_for_same_post_are_ambiguous_and_order_independent(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    a, b = publication(), publication()
    result = match(ledger, ident, publication, a, b)
    assert result == match(ledger, ident, publication, b, a, a)
    assert result["status"] == "PARTIAL"
    assert result["matches"][0]["publication"] is None
    assert result["matches"][0]["reason_codes"] == ["AMBIGUOUS_PUBLICATION"]
    assert match(ledger, ident, publication, a, a) == match(ledger, ident, publication, a)


@pytest.mark.parametrize("post", [None, "wrong"])
def test_url_alone_or_url_with_wrong_post_id_is_only_partial(tmp_path, plan, publication, post):
    url = "https://example.invalid/post/00007"
    ledger, ident = imported(tmp_path, plan, [row(post, url=url)])
    result = match(ledger, ident, publication, publication(url=url))
    assert result["status"] == "PARTIAL"
    assert result["matches"][0]["publication_id"] is None


def test_identical_url_in_wrong_account_cannot_supply_a_candidate(tmp_path, plan, publication):
    url = "https://example.invalid/post/00007"
    ledger, ident = imported(tmp_path, plan, [row(None, url=url)])
    result = match(ledger, ident, publication, publication(account="tiktok_open_id:other", url=url))
    assert result["status"] == "UNMATCHED"
    assert result["matches"][0]["candidate_publication_ids"] == []


@pytest.mark.parametrize("suffix", ["/", "?view=1", "#fragment"])
def test_url_candidates_require_exact_strings_without_rewriting(tmp_path, plan, publication, suffix):
    url = "https://example.invalid/post/00007"
    ledger, ident = imported(tmp_path, plan, [row(None, url=url + suffix)])
    result = match(ledger, ident, publication, publication(url=url))
    assert result["status"] == "UNMATCHED"
    assert result["matches"][0]["publication"] is None


def test_wrong_target_candidate_does_not_contaminate_unique_exact_match(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    wrong, correct = publication(account="tiktok_open_id:other"), publication()
    result = match(ledger, ident, publication, wrong, correct)
    assert result["status"] == "MATCHED"
    assert result["matches"][0]["publication"] == correct
    assert result["matches"][0]["candidate_publication_ids"] == [correct["publication_id"]]


def test_conflicting_url_blocks_even_an_exact_post_id(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan, [row(url="https://example.invalid/post/other")])
    result = match(ledger, ident, publication, publication(url="https://example.invalid/post/00007"))
    assert result["status"] == "PARTIAL"
    assert result["matches"][0]["reason_codes"] == ["IDENTIFIER_CONFLICT"]


def test_url_pointing_to_another_receipt_cannot_override_post_id(tmp_path, plan, publication):
    url = "https://example.invalid/post/other"
    ledger, ident = imported(tmp_path, plan, [row(url=url)])
    result = match(ledger, ident, publication, publication(), publication(post="other", url=url))
    assert result["status"] == "PARTIAL"
    assert result["matches"][0]["publication"] is None


def test_exact_post_id_does_not_infer_public_from_observation_url(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan, [row(url="https://example.invalid/post/00007")])
    receipt = publication()
    result = match(ledger, ident, publication, receipt)
    assert result["status"] == "MATCHED"
    assert result["matches"][0]["publication"]["visibility"] == "UNKNOWN"
    assert result["matches"][0]["publication"]["public_url"] is None


def test_missing_receipt_and_similar_timing_never_fabricate_confirmation(tmp_path, plan, publication):
    receipt = publication(receipt=False)
    # Matching a report to a handoff date is never evidence of publication.
    plan["measurement_window"] = ["2026-10-29T00:00:00Z", "2026-10-30T00:00:00Z"]
    ledger, ident = imported(tmp_path, plan)
    result = match(ledger, ident, publication, receipt)
    assert result["status"] == "PARTIAL"
    assert result["matches"][0]["reason_codes"] == ["RECEIPT_MISSING"]
    assert result["matches"][0]["publication"] is None
    assert result["publications"][0]["receipt"]["state"] == "SUBMITTED"
    assert result["publications"][0]["receipt"]["visibility"] == "UNKNOWN"


def test_missing_requested_publication_prevents_binding(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    missing = "pub-" + "f" * 24
    result = match_import(ledger, ident, publication_db=publication.journal.path,
                          publication_ids=[receipt["publication_id"], missing])
    assert result["status"] == "PARTIAL" and result["missing_publication_ids"] == [missing]
    assert result["matches"][0]["publication_id"] is None
    assert match_import(ledger, ident, publication_db=publication.journal.path,
                        publication_ids=[missing])["status"] == "UNMATCHED"


def test_account_aggregate_without_post_identifier_stays_unmatched(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan, [row(None)])
    result = match(ledger, ident, publication, publication())
    assert result["status"] == "UNMATCHED"
    assert result["matches"][0]["reason_codes"] == ["NO_POST_IDENTIFIER"]


def test_zero_unknown_mixed_status_and_repeatability(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan, [row(), row("unknown", value=None, status="NOT_AVAILABLE")])
    receipt = publication()
    before = ledger.inspect(ident)
    first = match(ledger, ident, publication, receipt)
    assert first == match(ledger, ident, publication, receipt)
    assert first["status"] == "PARTIAL"
    assert [m["match_status"] for m in first["matches"]] == ["MATCHED", "UNMATCHED"]
    assert [(m["observation"]["status"], m["observation"]["value"]) for m in first["matches"]] == [
        ("AVAILABLE", 0), ("NOT_AVAILABLE", None)]
    assert ledger.inspect(ident) == before
    # Missing a metric is independent of successful identity matching.
    unknown = publication(post="unknown")
    result = match(ledger, ident, publication, receipt, unknown)
    assert result["status"] == "MATCHED"
    assert result["matches"][1]["observation"]["value"] is None


def test_timestamps_and_timezone_are_preserved_not_used_as_matching_heuristics(tmp_path, plan, publication):
    # A08 fixture's created_at is today, outside this explicit measurement window.
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    result = match(ledger, ident, publication, receipt)
    assert result["status"] == "MATCHED"
    assert result["matches"][0]["observation"]["measurement_window"] == plan["measurement_window"]
    assert result["matches"][0]["observation"]["timezone"] == "UTC+07:00"
    assert result["matches"][0]["publication"]["created_at"] == receipt["created_at"]


def test_receipt_reopened_after_removal_without_cached_public_state(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication(url="https://example.invalid/post/00007")
    first = match(ledger, ident, publication, receipt)
    proof = tmp_path / "removed.txt"
    proof.write_bytes(b"Synthetic later removal")
    publication.journal.record(receipt["publication_id"], "REMOVAL_REPORTED", evidence_file=proof)
    current = match(ledger, ident, publication, receipt)
    assert current["status"] == "MATCHED"
    assert current["matches"][0]["publication"]["state"] == "REMOVED"
    assert current["matches"][0]["publication"]["visibility"] == "UNKNOWN"
    assert current["result_sha256"] != first["result_sha256"]


@pytest.mark.parametrize("column", ["event_json", "evidence_blob", "event_digest", "evidence_sha256"])
def test_a08_event_and_evidence_tamper_fails_without_partial_success(tmp_path, plan, publication, column):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    with sqlite3.connect(publication.journal.path) as db:
        db.execute(f"UPDATE publication_events SET {column}=? WHERE evidence_sha256 IS NOT NULL", (b"tampered",))
    with pytest.raises(LedgerError, match="PUBLICATION_EVIDENCE_INVALID"):
        match(ledger, ident, publication, receipt)


@pytest.mark.parametrize("column,value", [
    ("account_ref", '{"namespace":"facebook_page_id","id":"00001"}'),
    ("account_ref", "{}"), ("platform", "tiktok"), ("surface", "PAGE_REEL"),
    ("package_sha256", "a" * 64),
])
def test_malformed_or_inconsistent_a08_metadata_fails_closed(tmp_path, plan, publication, column, value):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    with sqlite3.connect(publication.journal.path) as db:
        db.execute(f"UPDATE publications SET {column}=?", (value,))
    with pytest.raises(LedgerError):
        match(ledger, ident, publication, receipt)


@pytest.mark.parametrize("table", ["raw_evidence", "imports", "observations"])
def test_a09_tamper_is_rejected_before_receipt_matching(tmp_path, plan, publication, table):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    with sqlite3.connect(ledger.path) as db:
        db.execute(f"DROP TRIGGER {table}_no_update")
        previous = db.execute(f"SELECT payload FROM {table}").fetchone()[0]
        db.execute(f"UPDATE {table} SET payload=?", (b"x" + previous[1:],))
    with pytest.raises(LedgerError):
        match(ledger, ident, publication, receipt)


def test_empty_deleted_or_missing_observations_are_errors(tmp_path, plan, publication):
    with pytest.raises(AnalyticsError):
        imported(tmp_path, plan, [])
    assert not (tmp_path / "analytics.sqlite").exists()
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    with pytest.raises(AnalyticsError, match="IMPORT_NOT_FOUND"):
        match(ledger, "a09-" + "f" * 64, publication, receipt)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TRIGGER observations_no_delete")
        db.execute("DELETE FROM observations")
    with pytest.raises(LedgerError, match="OBSERVATION_COUNT_MISMATCH"):
        match(ledger, ident, publication, receipt)


def test_missing_foreign_and_corrupt_journals_are_never_created_or_modified(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    ids = [receipt["publication_id"]]
    missing = tmp_path / "no-parent" / "missing.sqlite"
    with pytest.raises(LedgerError, match="PUBLICATION_JOURNAL_NOT_FOUND"):
        match_import(ledger, ident, publication_db=missing, publication_ids=ids)
    assert not missing.parent.exists()
    foreign = tmp_path / "foreign.sqlite"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a database")
    for path in (foreign, corrupt):
        before = path.read_bytes()
        with pytest.raises(LedgerError, match="PUBLICATION_JOURNAL_INVALID"):
            match_import(ledger, ident, publication_db=path, publication_ids=ids)
        assert path.read_bytes() == before


def test_wal_journal_is_rejected_without_creating_sqlite_sidecars(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    db = sqlite3.connect(publication.journal.path)
    try:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        db.close()
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(LedgerError, match="PUBLICATION_JOURNAL_NOT_QUIESCENT_ROLLBACK"):
        match(ledger, ident, publication, receipt)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == files
    assert not publication.journal.path.with_name("publications.sqlite-wal").exists()
    assert not publication.journal.path.with_name("publications.sqlite-shm").exists()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_existing_sidecars_are_rejected_without_recovery_or_writes(tmp_path, plan, publication, suffix):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    (tmp_path / ("publications.sqlite" + suffix)).write_bytes(b"synthetic active sidecar")
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(LedgerError, match="PUBLICATION_JOURNAL_NOT_QUIESCENT_ROLLBACK"):
        match(ledger, ident, publication, receipt)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == files


@pytest.mark.parametrize("ids", [["private-input"], ["pub-" + "a" * 24] * 101, "pub-" + "a" * 24])
def test_candidate_bounds_are_validated_before_opening_databases(tmp_path, ids):
    with pytest.raises(AnalyticsError) as error:
        match_import(AnalyticsLedger(tmp_path / "missing.sqlite"), "invalid",
                      publication_db=tmp_path / "missing-a08.sqlite", publication_ids=ids)
    assert len(str(error.value)) < 100 and "private-input" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_empty_candidate_set_is_explicitly_unmatched(tmp_path, plan, publication):
    ledger, ident = imported(tmp_path, plan)
    publication()
    assert match(ledger, ident, publication)["status"] == "UNMATCHED"


def test_matching_is_offline_and_uses_read_only_database_connections(tmp_path, plan, publication, monkeypatch):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()

    def forbidden(*args, **kwargs):
        pytest.fail("E02 attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original_connect = sqlite3.connect
    connections = []

    def readonly_connect(database, *args, **kwargs):
        assert isinstance(database, str) and "mode=ro" in database and kwargs.get("uri") is True
        db = original_connect(database, *args, **kwargs)
        connections.append(database)
        return db

    monkeypatch.setattr(sqlite3, "connect", readonly_connect)
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert match(ledger, ident, publication, receipt)["status"] == "MATCHED"
    assert len(connections) == 2
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == files


def test_cli_match_reports_status_and_bounded_errors(tmp_path, plan, publication, capsys):
    ledger, ident = imported(tmp_path, plan)
    receipt = publication()
    args = ["analytics", "match", "--db", str(ledger.path), "--import-id", ident,
            "--publication-db", str(publication.journal.path), "--publication-id", receipt["publication_id"]]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == match(ledger, ident, publication, receipt)
    assert main([*args, "--publication-id", "pub-" + "f" * 24]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PARTIAL"
    assert main([*args, "--publication-id", "sensitive-untrusted-input"]) == 40
    error = capsys.readouterr()
    assert not error.out and "sensitive-untrusted-input" not in error.err
    assert json.loads(error.err)["error"] == "INVALID_PUBLICATION_ID"
    with sqlite3.connect(publication.journal.path) as db:
        db.execute("UPDATE publication_events SET evidence_blob=? WHERE evidence_sha256 IS NOT NULL", (b"changed",))
    assert main(args) == 50
    error = capsys.readouterr()
    assert not error.out and json.loads(error.err)["error"] == "PUBLICATION_EVIDENCE_INVALID"
    with pytest.raises(SystemExit) as help_exit:
        main(["analytics", "match", "--help"])
    assert help_exit.value.code == 0 and "read-only" in capsys.readouterr().out
