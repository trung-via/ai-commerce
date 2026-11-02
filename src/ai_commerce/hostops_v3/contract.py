"""Closed wire contract. Canonical JSON permits integers, never floats or NaN."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

MAX_WIRE = 131072
MAX_CHUNK = 65536
MAX_FILE = 64 * 1024 * 1024
REPOSITORY = "trung-via/ai-commerce"
OPS = frozenset("volumes stat list find read hash create replace patch append copy move rename quarantine powershell disclose".split())
MUTATIONS = frozenset("create replace patch append copy move rename quarantine".split())
# These operations can lose a noncooperating writer's update between the final
# hash check and a namespace mutation. A signed lease cannot supply atomicity.
BLOCKED_CONDITIONAL_WRITES = frozenset("replace patch append move rename quarantine".split())
STATUSES = frozenset("OBSERVED APPLIED PREVIEW BLOCKED UNAVAILABLE BUSY PARTIAL UNKNOWN DISCLOSED".split())


class Denied(Exception):
    """Only fixed codes may cross the disclosure boundary."""


def deny(code):
    raise Denied(code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    def walk(v):
        if v is None or type(v) in (str, bool):
            return
        if type(v) is int and abs(v) <= 2**53 - 1:
            return
        if type(v) is list:
            for item in v:
                walk(item)
            return
        if type(v) is dict and all(type(k) is str for k in v):
            for item in v.values():
                walk(item)
            return
        deny("NON_CANONICAL_TYPE")
    walk(value)
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def decode(raw: bytes, limit=MAX_WIRE):
    if not 0 < len(raw) <= limit:
        deny("WIRE_LIMIT")
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                deny("DUPLICATE_KEY")
            result[k] = v
        return result
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        canonical(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        deny("BAD_JSON")


def shape(v, keys):
    if type(v) is not dict or set(v) != set(keys.split()):
        deny("BAD_SHAPE")


def text(v, limit=4096):
    if type(v) is not str or not 0 < len(v) <= limit or any(ord(c) < 32 for c in v):
        deny("BAD_STRING")
    # Reject surrogate escapes before a platform path encoder sees them.
    try:
        v.encode("utf-8")
    except UnicodeError:
        deny("BAD_STRING")
    return v


def ident(v):
    if type(v) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", v):
        deny("BAD_ID")
    return v


def sha(v, size=64):
    if type(v) is not str or not re.fullmatch("[0-9a-f]{" + str(size) + "}", v):
        deny("BAD_HASH")
    return v


def integer(v, low, high):
    if type(v) is not int or not low <= v <= high:
        deny("BAD_INTEGER")
    return v


def blob(v):
    if type(v) is not str or len(v) > MAX_CHUNK * 2:
        deny("CHUNK_LIMIT")
    try:
        b = base64.b64decode(v, validate=True)
    except (ValueError, TypeError):
        deny("BAD_BASE64")
    if len(b) > MAX_CHUNK or base64.b64encode(b).decode("ascii") != v:
        deny("CHUNK_LIMIT")
    return b


PARAMETERS = {
    "volumes": "", "stat": "path", "list": "path limit", "find": "path needle limit",
    "read": "path offset length expected_sha256", "hash": "path",
    "create": "path data_b64 data_sha256", "replace": "path data_b64 data_sha256 expected_sha256",
    "patch": "path data_b64 data_sha256 expected_sha256 offset remove_bytes",
    "append": "path data_b64 data_sha256 expected_sha256",
    "copy": "path destination expected_sha256", "move": "path destination expected_sha256",
    "rename": "path destination expected_sha256", "quarantine": "path expected_sha256 preview_sha256",
    "powershell": "script_id script_sha256", "disclose": "selection_id content_sha256 offset length classification recipient",
}


@dataclass(frozen=True)
class Request:
    value: dict
    fingerprint: str

    @classmethod
    def parse(cls, value):
        shape(value, "schema request_id repository issuer host_id release_id source_sha created_at expires_at timeout_seconds operation parameters preview")
        if value["schema"] != "hostops.request.v3" or value["repository"] != REPOSITORY:
            deny("IDENTITY_MISMATCH")
        for k in ("request_id", "issuer", "host_id"):
            ident(value[k])
        sha(value["source_sha"], 40)
        sha(value["release_id"])
        integer(value["created_at"], 0, 2**53 - 1)
        integer(value["expires_at"], value["created_at"] + 1, value["created_at"] + 300)
        integer(value["timeout_seconds"], 1, 30)
        if type(value["preview"]) is not bool:
            deny("BAD_BOOLEAN")
        op = value["operation"]
        if type(op) is not str or op not in OPS:
            deny("OPERATION_DENIED")
        p = value["parameters"]
        shape(p, PARAMETERS[op])
        for k in ("path", "destination", "needle", "recipient"):
            if k in p:
                text(p[k], 256 if k == "needle" else 4096)
        for k in ("expected_sha256", "data_sha256", "content_sha256", "script_sha256", "preview_sha256"):
            if k in p:
                sha(p[k])
        for k in ("offset", "remove_bytes"):
            if k in p:
                integer(p[k], 0, MAX_FILE)
        if "length" in p:
            integer(p["length"], 1, MAX_CHUNK)
        if "limit" in p:
            integer(p["limit"], 1, 256)
        if "selection_id" in p:
            sha(p["selection_id"])
        if "data_b64" in p and digest(blob(p["data_b64"])) != p["data_sha256"]:
            deny("PAYLOAD_HASH_MISMATCH")
        if op == "powershell" and p["script_id"] != "token_inventory_v1":
            deny("SCRIPT_DENIED")
        if op == "disclose" and p["classification"] not in ("ordinary", "sensitive"):
            deny("BAD_CLASSIFICATION")
        # Copy so callers cannot mutate an authenticated object after checking it.
        copy = decode(canonical(value))
        return cls(copy, digest(canonical(copy)))


def envelope(raw):
    e = decode(raw)
    shape(e, "schema request issuer_signature_b64 lease lease_signature_b64 action_signature_b64")
    if e["schema"] != "hostops.envelope.v3":
        deny("BAD_VERSION")
    for k in ("issuer_signature_b64", "lease_signature_b64", "action_signature_b64"):
        if type(e[k]) is not str or len(e[k]) > 16384:
            deny("BAD_SIGNATURE")
    return e, Request.parse(e["request"])


RECEIPT_KEYS = "schema request_ref request_sha256 release_id source_sha operation status error_code result"


def receipt(request: Request | None, status, code="NONE", result=None):
    r = request.value if request else None
    return {"schema": "hostops.receipt.v3", "request_ref": digest(r["request_id"].encode()) if r else "0" * 64,
            "request_sha256": request.fingerprint if r else "0" * 64,
            "release_id": r["release_id"] if r else "0" * 64,
            "source_sha": r["source_sha"] if r else "0" * 40,
            "operation": r["operation"] if r else "none", "status": status,
            "error_code": code, "result": result or {}}


PUBLIC_FIELDS = {"sha256", "bytes", "count", "truncated", "selection_id", "chunk_sha256", "offset",
                 "total_bytes", "preview_sha256", "reconciled", "exists", "is_directory", "pwsh_major",
                 "non_elevated", "volume_count", "cross_volume", "local_only"}


def public_receipt(value):
    shape(value, RECEIPT_KEYS)
    if value["schema"] != "hostops.receipt.v3" or value["status"] not in STATUSES:
        deny("BAD_RECEIPT")
    for k in ("request_ref", "request_sha256", "release_id"):
        sha(value[k])
    sha(value["source_sha"], 40)
    if value["operation"] not in OPS | {"none"}:
        deny("BAD_RECEIPT")
    if type(value["error_code"]) is not str or not re.fullmatch("[A-Z_]{2,64}", value["error_code"]):
        deny("BAD_RECEIPT")
    d = value["result"]
    if type(d) is not dict or not set(d) <= PUBLIC_FIELDS:
        deny("PRIVATE_RECEIPT_FIELD")
    for k, v in d.items():
        if k.endswith("sha256") or k == "selection_id":
            sha(v)
        elif k in ("truncated", "reconciled", "exists", "is_directory", "non_elevated", "cross_volume", "local_only"):
            if type(v) is not bool:
                deny("BAD_RECEIPT")
        else:
            integer(v, 0, MAX_FILE)
    return value
