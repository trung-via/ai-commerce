"""A10 signal intake foundation: importer, profiles and append-only ledger."""

from .files import MAX_FILE_BYTES, MAX_ROW_BYTES, MAX_ROWS, MAX_SNAPSHOT_BYTES, ledger_path, read_local
from .importer import (
    PreparedSignalImport,
    load_mapping,
    load_source_snapshot,
    prepare_bytes,
)
from .ledger import SignalLedger
from .profiles import mapping_profile, validate_mapping

__all__ = [
    "MAX_FILE_BYTES",
    "MAX_ROW_BYTES",
    "MAX_ROWS",
    "MAX_SNAPSHOT_BYTES",
    "ledger_path",
    "read_local",
    "PreparedSignalImport",
    "load_mapping",
    "load_source_snapshot",
    "prepare_bytes",
    "SignalLedger",
    "mapping_profile",
    "validate_mapping",
]
