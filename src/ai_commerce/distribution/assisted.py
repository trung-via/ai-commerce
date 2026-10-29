"""A08 D02/D03: offline E01-backed distribution package and manual handoff.

No network, OAuth, uploading or provider entitlement inference. The source A06
delivery is fully verified before its bytes or claims are reused.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from importlib.resources import files
from jsonschema import Draft202012Validator
from typing import Any
from urllib.parse import urlsplit

from ai_commerce.e2e import verify as verify_e01
from ai_commerce.operations.manifest import strict_json
from ai_commerce.core.provenance import canonical_bytes, sha256_bytes

from .model import CapabilityError, provider_profiles, validate_request


class DistributionError(ValueError):
    """Invalid offline export or publication package."""


_SHA = re.compile(r"^[a-f0-9]{64}$")
_TAG = re.compile(r"^[\w-]+$", re.UNICODE)
_ALLOWED_KEYS = {"schema_version", "package_id", "source_run_id", "source_manifest_sha256",
                 "video_sha256", "platform", "surface", "account_ref", "market", "app_ref",
                 "title", "caption", "hashtags", "product_ref", "disclosure",
                 "capability", "files", "state", "publication_status", "package_sha256"}
_TEXT_KEYS = ("caption.txt", "hashtags.txt", "title.txt", "publish-instructions.md")


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise DistributionError(code)


def _hash(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _account(platform: str, account: str) -> dict[str, str]:
    _require(isinstance(account, str) and ":" in account, "ACCOUNT_EXPECTED_NAMESPACE_COLON_ID")
    namespace, name = account.split(":", 1)
    obj = {"namespace": namespace, "id": name}
    expected_namespaces = {"TIKTOK": "tiktok_open_id", "FACEBOOK": "facebook_page_id",
                           "INSTAGRAM": "instagram_user_id", "YOUTUBE": "youtube_channel_id"}
    _require(namespace in {expected_namespaces[platform], "internal_alias"} and bool(name.strip()),
             "ACCOUNT_NAMESPACE_PLATFORM_MISMATCH")
    validate_request({"schema_version": "ChannelCapabilityRequest.v1",
                      "platform": platform, "account_ref": obj, "app_ref": None,
                      "market": None, "surface": _first_surface(platform),
                      "action": "EXPORT_PACKAGE"})
    return obj


def _first_surface(platform: str) -> str:
    profile = provider_profiles()["platforms"].get(platform, {})
    _require(bool(profile), "UNKNOWN_PLATFORM")
    return profile["surfaces"][0]


def _instructions(platform: str, surface: str, account: dict[str, str]) -> str:
    return (
        "# Manual publishing handoff\n\n"
        "This is an OFFLINE export; no upload or publication has occurred.\n"
        f"Target: {platform} / {surface}\n"
        f"Account reference: {account['namespace']}:{account['id']}\n\n"
        "1. Open the official platform UI with the intended account.\n"
        "2. Confirm the selected account and current posting eligibility there.\n"
        "3. Upload video.mp4; review caption.txt, hashtags.txt and title.txt.\n"
        "4. Apply any required disclosure and platform settings yourself.\n"
        "5. Record the actual submission/result and any real receipt via A08 journal.\n"
        "An exported package is NOT SUBMITTED, PUBLISHED_CONFIRMED or PUBLIC.\n"
    )


def _check_fields(record: dict[str, Any]) -> None:
    _require(isinstance(record, dict) and set(record) == _ALLOWED_KEYS, "PACKAGE_FIELDS_INVALID")
    schema = strict_json(files("ai_commerce.distribution").joinpath(
        "schemas", "DistributionPackage.v1.schema.json").read_bytes())
    _require(Draft202012Validator(schema).is_valid(record), "PACKAGE_SCHEMA_INVALID")
    _require(record["schema_version"] == "DistributionPackage.v1" and
             record["state"] == "EXPORTED_FOR_MANUAL_HANDOFF" and
             record["publication_status"] == "NOT_ATTEMPTED", "PACKAGE_FALSE_PUBLICATION")
    for name in ("video_sha256", "source_manifest_sha256", "package_sha256"):
        _require(isinstance(record[name], str) and _SHA.fullmatch(record[name]) is not None,
                 "PACKAGE_SHA_INVALID:" + name)
    raw = {k: v for k, v in record.items() if k != "package_sha256"}
    _require(record["package_sha256"] == sha256_bytes(canonical_bytes(raw)), "PACKAGE_DIGEST_MISMATCH")
    _require(record["package_id"] == "a08-" + sha256_bytes(canonical_bytes({k:v for k,v in record.items() if k not in ("package_id","package_sha256")}))[:24], "PACKAGE_ID_MISMATCH")
    profile = provider_profiles()
    cfg = profile["platforms"].get(record["platform"], {})
    _require(record["surface"] in cfg.get("surfaces", []), "PACKAGE_TARGET_NOT_PROFILED")
    validate_request({"schema_version": "ChannelCapabilityRequest.v1",
                      "platform": record["platform"], "account_ref": record["account_ref"],
                      "app_ref": record["app_ref"], "market": record["market"],
                      "surface": record["surface"], "action": "EXPORT_PACKAGE"})
    _require(isinstance(record["title"], str) and isinstance(record["caption"], str)
             and isinstance(record["hashtags"], list)
             and all(isinstance(v, str) and _TAG.fullmatch(v) for v in record["hashtags"]),
             "PACKAGE_METADATA_INVALID")
    _require(record["product_ref"] is None or isinstance(record["product_ref"], str),
             "PACKAGE_PRODUCT_REF_INVALID")
    _require(record["disclosure"] is None or isinstance(record["disclosure"], str),
             "PACKAGE_DISCLOSURE_INVALID")
    _require(isinstance(record["files"], dict)
             and set(record["files"]) == {"video.mp4", *_TEXT_KEYS}
             and all(isinstance(v, str) and _SHA.fullmatch(v) for v in record["files"].values()),
             "PACKAGE_FILES_INVALID")
    _require(record["files"]["video.mp4"] == record["video_sha256"], "PACKAGE_VIDEO_INDEX_MISMATCH")
    c = record["capability"]
    _require(isinstance(c, dict) and set(c) == {"decision", "verification", "reason_codes"}
             and c["decision"] in {"ALLOW", "DENY", "UNKNOWN", "NEEDS_RECHECK"}
             and c["verification"] == "OFFLINE_RECORDED_NOT_LIVE_VERIFIED"
             and isinstance(c["reason_codes"], list), "PACKAGE_CAPABILITY_INVALID")


def inspect_package(root: str | Path) -> dict[str, Any]:
    """Reopen every exported byte; no mutable source paths are trusted."""
    root = Path(root).absolute()
    try:
        record = strict_json((root / "package.json").read_bytes())
        _check_fields(record)
        expected = {
            "caption.txt": record["caption"].encode("utf-8"),
            "hashtags.txt": ("\n".join("#" + v for v in record["hashtags"]) + "\n").encode("utf-8"),
            "title.txt": record["title"].encode("utf-8"),
            "publish-instructions.md": _instructions(
                record["platform"], record["surface"], record["account_ref"]).encode("utf-8"),
        }
        for name, data in expected.items():
            _require(sha256_bytes(data) == record["files"][name], "PACKAGE_METADATA_FILE_MISMATCH")
        for name, sha in record["files"].items():
            file = root / name
            _require(file.is_file() and not file.is_symlink(), "PACKAGE_FILE_MISSING:" + name)
            _require(_hash(file) == sha, "PACKAGE_FILE_DIGEST_MISMATCH:" + name)
        _require(set(p.name for p in root.iterdir()) == {*record["files"], "package.json"},
                 "PACKAGE_UNEXPECTED_FILES")
        return record
    except (OSError, ValueError, TypeError, KeyError, CapabilityError) as exc:
        if isinstance(exc, DistributionError):
            raise
        raise DistributionError("PACKAGE_READ_OR_VALIDATION_FAILED") from exc


def export_package(source: str | Path, output: str | Path, *,
                   platform: str, surface: str, account: str,
                   caption: str = "", title: str = "", hashtags: list[str] | None = None,
                   market: str | None = None, app_ref: dict | None = None,
                   product_ref: str | None = None, disclosure: str | None = None,
                   capability_db: str | Path | None = None) -> dict[str, Any]:
    """Create an immutable-on-disk portable package; export never posts."""
    source, output = Path(source).absolute(), Path(output).absolute()
    _require(not output.exists(), "OUTPUT_EXISTS")
    _require(not output.is_relative_to(source), "OUTPUT_INSIDE_SOURCE")
    _require(all(isinstance(v, str) for v in (caption, title)), "CAPTION_OR_TITLE_INVALID")
    tags = hashtags if hashtags is not None else []
    _require(isinstance(tags, list) and all(isinstance(v, str) and _TAG.fullmatch(v) for v in tags),
             "INVALID_HASHTAGS")
    profile = provider_profiles()["platforms"].get(platform, {})
    _require(surface in profile.get("surfaces", []), "TARGET_NOT_PROFILED")
    account_ref = _account(platform, account)
    request = {"schema_version": "ChannelCapabilityRequest.v1", "platform": platform,
               "account_ref": account_ref, "app_ref": app_ref, "market": market,
               "surface": surface, "action": "EXPORT_PACKAGE"}
    validate_request(request)
    capability = {"decision": "UNKNOWN", "verification": "OFFLINE_RECORDED_NOT_LIVE_VERIFIED",
                  "reason_codes": ["NOT_EVALUATED_NO_REGISTRY"]}
    if capability_db is not None:
        from .registry import CapabilityRegistry
        from .evaluator import evaluate
        decision = evaluate(request, CapabilityRegistry(capability_db))
        capability = {k: decision[k] for k in ("decision", "verification", "reason_codes")}
    # E01 verify full-decodes the source and reopens its custody/provenance chain.
    actual = verify_e01(source)
    _require(actual.get("state") == "DELIVERED", "SOURCE_E01_NOT_DELIVERED")
    manifest_raw = (source / "e01-manifest.json").read_bytes()
    manifest = strict_json(manifest_raw)
    _require(manifest["publication_status"] == "NOT_ATTEMPTED", "E01_PUBLICATION_CLAIM_INVALID")
    sha = _hash(source / "video.mp4")
    _require(sha == manifest["output_sha256"], "SOURCE_VIDEO_SHA_MISMATCH")
    files_bytes = {
        "caption.txt": caption.encode("utf-8"),
        "title.txt": title.encode("utf-8"),
        "hashtags.txt": ("\n".join("#" + v for v in tags) + "\n").encode("utf-8"),
        "publish-instructions.md": _instructions(platform, surface, account_ref).encode("utf-8"),
    }
    file_hashes = {"video.mp4": sha, **{k: sha256_bytes(v) for k, v in files_bytes.items()}}
    payload = {
        "schema_version": "DistributionPackage.v1",
        "source_run_id": manifest["run_id"],
        "source_manifest_sha256": sha256_bytes(manifest_raw),
        "video_sha256": sha,
        "platform": platform, "surface": surface, "account_ref": account_ref,
        "market": market, "app_ref": app_ref, "title": title, "caption": caption,
        "hashtags": tags, "product_ref": product_ref, "disclosure": disclosure,
        "capability": capability, "files": file_hashes,
        "state": "EXPORTED_FOR_MANUAL_HANDOFF",
        "publication_status": "NOT_ATTEMPTED",
    }
    digest = sha256_bytes(canonical_bytes(payload))
    record = {**payload, "package_id": "a08-" + digest[:24]}
    record["package_sha256"] = sha256_bytes(canonical_bytes(record))
    # package_id is an identity of the content hash excluding the ID to avoid a circular hash.
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".a08-export-", dir=output.parent))
    try:
        shutil.copyfile(source / "video.mp4", temp / "video.mp4")
        for name, data in files_bytes.items():
            (temp / name).write_bytes(data)
        (temp / "package.json").write_bytes(canonical_bytes(record))
        inspect_package(temp)
        _require(not output.exists(), "OUTPUT_EXISTS")
        temp.rename(output)
        return inspect_package(output)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
