"""One-shot E04 #223 integration from SHA256-verified HostOps #228 private archive.

Fixed source identity and file allowlist. Executes on an isolated review branch only.
No model, EF02 retry, main write, provider calls or live affiliate effects.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PINNED_MAIN = "2e08787ab74374c6918bc8d95f4f34e9ad024dfa"
ARCHIVE_SHA256 = "9182200e1fc255a74430f7e78248b18fa70e48718d19e0f25dfadc8f83e5b0d6"
EXPECTED = {
    "contracts/AffiliateCommerceRun.v1.schema.json": "18b8392063f11cfc55ba94a8d6eb2288f2be7fa46325c6b23be29be366e42df9",
    "docs/engineering/A14_E04_IMPLEMENTATION_REPORT.md": "6f3beb700f08d28b2035ebeb8f7b55e39d84a5e41ce6f6d6888411443c36771f",
    "src/ai_commerce/affiliate_commerce/__init__.py": "92b8b9300463b6d6014085733a1c47284defedc58ec9ee0403330c31c35a0c84",
    "src/ai_commerce/affiliate_commerce/__main__.py": "da76430896f7765b661bfd5ad5efe0139dd673ab1c674eec71daf304b626818a",
    "src/ai_commerce/affiliate_commerce/schemas.py": "7f34dafb11e9306ad7429ae77c1db0a55d52310d7106fd6242878556d9b536d4",
    "src/ai_commerce/affiliate_commerce/service.py": "62a2fb879f266ac7e86283bbc526adc8301323df99c9a87a5e6796894bd27bac",
    "tests/test_a14_e04.py": "c18b8a3bed90e738293cb58773e9720dbb9aa14fb6b6a39e79733d8000d638df",
}
GROUP_NAME = "a14-e04"
REGISTERED_TEST = "tests/test_a14_commerce_e2e.py"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def required_base() -> None:
    assert os.environ.get("GITHUB_REF_NAME") == "repair/a14-e04-223-integration-20261102", "NOT_REVIEW_BRANCH"
    common = subprocess.check_output(["git", "merge-base", "HEAD", PINNED_MAIN], cwd=ROOT, text=True).strip()
    assert common == PINNED_MAIN, "WRONG_BASE_ANCESTRY"
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip(), "DIRTY_CHECKOUT"
    for path in (*EXPECTED, REGISTERED_TEST):
        assert not (ROOT / path).exists(), f"DEST_ALREADY_EXISTS:{path}"


def recover() -> dict[str, bytes]:
    data = (ROOT / ".local" / "e04" / "verified-source.zip").read_bytes()
    assert sha(data) == ARCHIVE_SHA256, "ARTIFACT_ARCHIVE_SHA256_MISMATCH"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == set(EXPECTED), "ARTIFACT_FILE_SET_MISMATCH"
        assert archive.testzip() is None, "ARTIFACT_ZIP_CRC_FAILED"
        files = {name: archive.read(name) for name in EXPECTED}
    for name, contents in files.items():
        assert sha(contents) == EXPECTED[name], f"SOURCE_SHA256_MISMATCH:{name}"
    return files


def update_registration() -> None:
    selector = ROOT / "src/ai_commerce/engineering/minimum_test.py"
    before = selector.read_text(encoding="utf-8")
    anchor = '        TestGroup("foundation", ("tests/test_foundation.py",)'
    assert before.count(anchor) == 1, "TESTGROUP_ANCHOR_DRIFT"
    group = '''        # A14 E04: reviewed integration of SHA256-verified #223 source; independent of L04.
        TestGroup("a14-e04", ("tests/test_a14_commerce_e2e.py",),
                  ("A14.E04.offline-affiliate-commerce",),
                  sources("affiliate_commerce", "__init__ __main__ schemas service"),
                  contracts=(
                      "contracts/AffiliateCommerceRun.v1.schema.json",
                      "docs/engineering/A14_E04_IMPLEMENTATION_REPORT.md",
                  )),
'''
    assert GROUP_NAME not in before, "E04_GROUP_ALREADY_REGISTERED"
    selector.write_text(before.replace(anchor, group + anchor), encoding="utf-8", newline="\n")
    registry = ROOT / ".github/ef02/verify-profiles.json"
    profiles = json.loads(registry.read_text(encoding="utf-8"))
    name = "mt01-a14-e04-tests"
    assert name not in profiles, "PROFILE_ALREADY_REGISTERED"
    profiles[name] = ["python", "-m", "pytest", REGISTERED_TEST, "-q", "-n", "12"]
    registry.write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    required_base()
    files = recover()
    for source, original in files.items():
        target_name = REGISTERED_TEST if source == "tests/test_a14_e04.py" else source
        content = original
        if source == "tests/test_a14_e04.py":
            text = original.decode("utf-8")
            assert text.count("tests/test_a14_e04.py") == 2, "TEST_FILENAME_REFERENCES_DRIFT"
            text = text.replace("tests/test_a14_e04.py", REGISTERED_TEST)
            obsolete = "def test_new_family_is_provisional_and_requires_registered_full_fallback():"
            assert text.count(obsolete) == 1, "PROVISIONAL_TEST_DRIFT"
            # The original test asserted expected provisional failure for the unpublished worktree.
            # Once integrated, verify the reviewed group/profile instead of preserving that obsolete assertion.
            text = text[:text.index(obsolete)] + '''def test_e04_component_is_registered_for_targeted_verification():
    from ai_commerce.engineering.minimum_test import approved_groups
    root = Path(__file__).resolve().parents[1]
    groups = {group.name: group for group in approved_groups()}
    assert "a14-e04" in groups
    assert "tests/test_a14_commerce_e2e.py" in groups["a14-e04"].tests
    profiles = json.loads((root / ".github/ef02/verify-profiles.json").read_bytes())
    assert profiles["mt01-a14-e04-tests"] == [
        "python", "-m", "pytest", "tests/test_a14_commerce_e2e.py", "-q", "-n", "12",
    ]
'''
            content = text.encode("utf-8")
        dest = ROOT / target_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    report = ROOT / "docs/engineering/A14_E04_IMPLEMENTATION_REPORT.md"
    report.write_text(report.read_text(encoding="utf-8") + """

## Integration of recovered source (separate from historical EF02 worktree report)

- Original EF02 Issue #223 remained VERIFICATION_FAILED; this report's earlier
  references to test_a14_e04.py and provisional broad fallback describe that
  original immutable run and are retained as historical evidence.
- HostOps2 Issue #228 / run 41426161990 attested all seven SHA256 source files;
  inner archive SHA256 9182200e1fc255a74430f7e78248b18fa70e48718d19e0f25dfadc8f83e5b0d6.
- A separate review branch imports those verified bytes. The E04 test file
  is deliberately renamed to tests/test_a14_commerce_e2e.py and its obsolete
  provisional-mapping assertion replaced by a reviewed registration assertion.
- This changes test bytes, so the original #223 E04 test PASS is historical,
  not proof of final integration. Current-branch tests and hosted CI must run.
- Main merge requires fresh Human approval; no real affiliate provider or
  commercial outcomes are implied.
""", encoding="utf-8", newline="\n")
    update_registration()
    print("E04_223_SOURCE_RECOVERED_AND_REGISTERED_FOR_REVIEW")
    print("source_files_sha256_verified=7")
    print("test_path_renamed=tests/test_a14_commerce_e2e.py")
    print("registered_profile=mt01-a14-e04-tests")


if __name__ == "__main__":
    main()
