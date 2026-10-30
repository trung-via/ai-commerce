"""Reject personal/contact data and secrets before A12 persistence, never redact.

Only UTF-8 JSON/CSV evidence is archived; binary media and arbitrary documents
are deliberately excluded. Use non-personal aliases for holders/reviewers and
source references. Public credential-free HTTP(S) URLs without query/fragment
are inert metadata; hashes, timestamps, GTIN/SKU and typed product identifiers
are narrow scanner exceptions. This bounded denylist is not a semantic PII
classifier: callers remain responsible for supplying non-personal evidence.
Unknown raw fields are scanned too, including fields omitted by A12 mappings.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .core import ProductError, canonical_bytes, parse_json, parse_timestamp, require
from .schemas import MAX_RAW_BYTES

_SECRET_KEY = re.compile(
    r"^(?:password|passwd|secret|client_secret|api_key|apikey|access_token|refresh_token|"
    r"token|authorization|cookie|set_cookie|private_key|credentials)$", re.I)
_PERSONAL_KEY = re.compile(
    r"^(?:e_?mail|phone|telephone|mobile|full_name|first_name|last_name|customer_name|"
    r"address|street_address|date_of_birth|dob|ssn|passport|national_id|ip_address)$", re.I)
_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:Bearer|Basic)\s+[A-Za-z0-9+/=_-]+|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|"
    r"github_pat_[A-Za-z0-9_]{16,})|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"\b(?:password|api[_-]?key|access[_-]?token|secret)\s*[:=]", re.I)
_CONTACT = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+|(?<!\w)@[\w.]{2,}|\b(?:mailto|tel):|"
                      r"(?<!\w)\+?(?:\d[ ().-]*){9,15}(?!\w)", re.I)
_HASH = re.compile(r"(?:[A-Za-z][A-Za-z0-9._:-]*[-:])?[a-f0-9]{64}")
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_PRODUCT_KEYS = {"sku", "gtin", "asserted_sku", "asserted_gtin", "seller_key", "listing_key",
                 "product_key", "variant_key", "seller", "listing", "product"}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Cf")
    return unquote(unquote(value))


def _text(value: str, key: str = "") -> None:
    scanned = _normalized(value)
    require(not _SECRET.search(scanned), "EVIDENCE_SECRET_NOT_ALLOWED")
    require(not any(ord(c) < 32 and c not in "\n\r\t" for c in scanned)
            and "\x7f" not in scanned, "EVIDENCE_CONTROL_CHARACTER")
    if _HASH.fullmatch(scanned):
        return
    require(not re.search(r"(?i)\b(?!https?://)[a-z][a-z0-9+.-]*://", scanned),
            "EVIDENCE_UNSAFE_URL")
    # Typed timestamps are safe; unknown timestamp-like text is still scanned.
    try:
        parse_timestamp(scanned)
        return
    except ProductError:
        pass
    for match in _URL.finditer(scanned):
        try:
            parts = urlsplit(match.group())
            require(parts.hostname is not None and parts.username is None and parts.password is None
                    and not parts.query and not parts.fragment, "EVIDENCE_UNSAFE_URL")
            parts.port
        except ValueError:
            raise ProductError("EVIDENCE_UNSAFE_URL") from None
    without_urls = _URL.sub("", scanned)
    if key in _PRODUCT_KEYS and re.fullmatch(r"[0-9]{1,32}", without_urls):
        return
    require(not _CONTACT.search(without_urls), "EVIDENCE_PERSONAL_DATA_NOT_ALLOWED")
    require(not re.search(r"(?i)(?:[A-Z]:[\\/]|/(?:Users|home)/|\\\\)", without_urls),
            "EVIDENCE_PERSONAL_PATH_NOT_ALLOWED")


def check_document(value) -> None:
    canonical_bytes(value)
    pending = [(value, "")]
    while pending:
        item, key = pending.pop()
        if type(item) is dict:
            for child_key, child in item.items():
                normalized_key = _normalized(child_key).replace("-", "_")
                require(not _SECRET_KEY.fullmatch(normalized_key), "EVIDENCE_SECRET_NOT_ALLOWED")
                require(not _PERSONAL_KEY.fullmatch(normalized_key), "EVIDENCE_PERSONAL_DATA_NOT_ALLOWED")
                _text(child_key)
                pending.append((child, child_key))
        elif type(item) is list:
            pending.extend((child, key) for child in item)
        elif type(item) is str:
            _text(item, key)


def check_raw(raw: bytes, *, file_format: str = "json") -> None:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RAW_BYTES, "SOURCE_EMPTY_OR_TOO_LARGE")
    if file_format == "json":
        check_document(parse_json(raw, limit=MAX_RAW_BYTES))
        return
    require(file_format == "csv", "REGISTRY_RAW_FORMAT_NOT_ALLOWED")
    try:
        rows = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(rows)
        for key in header:
            require(not _SECRET_KEY.fullmatch(_normalized(key).replace("-", "_")),
                    "EVIDENCE_SECRET_NOT_ALLOWED")
            require(not _PERSONAL_KEY.fullmatch(_normalized(key).replace("-", "_")),
                    "EVIDENCE_PERSONAL_DATA_NOT_ALLOWED")
            _text(key)
        for row in rows:
            require(len(row) == len(header), "CSV_ROW_WIDTH")
            for key, value in zip(header, row):
                _text(value, key)
    except (UnicodeError, csv.Error, StopIteration):
        raise ProductError("REGISTRY_RAW_FORMAT_NOT_ALLOWED") from None
