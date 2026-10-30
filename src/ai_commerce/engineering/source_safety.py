"""Shared fail-closed path checks for verification and candidate transport."""
from __future__ import annotations

from pathlib import Path


def is_link(path: Path) -> bool:
    try:
        # Python 3.11 has no Path.is_junction(); Windows reparse attributes
        # cover junctions and other indirections even on that supported version.
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def protected_change(paths) -> bool:
    for value in paths:
        if not isinstance(value, str):
            return True
        parts = value.lower().split("/")
        if (not value or "\\" in value or ":" in value or "\x00" in value or
                any(p in ("", ".", "..") or p.rstrip(". ") != p for p in parts) or
                any(p in (".git", ".github", ".local", ".agents", ".codex", ".aws", ".ssh",
                          ".gitattributes", ".gitignore", ".gitmodules", "source_integrity_sha256.json",
                          "secrets", "credentials") or p == ".env" or p.startswith(".env.")
                    for p in parts) or
                parts[-1] in ("id_rsa", "id_ed25519", ".netrc", "_netrc") or
                Path(parts[-1]).suffix in (".pem", ".key", ".p12", ".pfx")):
            return True
    return False


def ordinary_scope_drift(paths) -> bool:
    """Finite source/test/document families, never arbitrary binary publication."""
    suffixes = {".py", ".pyi", ".md", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml",
                ".ini", ".cfg", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css",
                ".sql", ".ps1", ".sh", ".csv", ".tsv"}
    return not protected_change(paths) and all(
        Path(p).suffix.lower() in suffixes or Path(p).name.upper() in ("LICENSE", "NOTICE", "AUTHORS")
        for p in paths)


def checked_path(workspace: Path, relative: str) -> Path:
    """Reject unsafe names and every symlink/junction ancestor before reading."""
    if protected_change([relative]):
        raise ValueError("PROTECTED_CHANGED_PATH")
    root = workspace.resolve()
    path = root
    for part in relative.split("/"):
        path = path / part
        if is_link(path):
            raise ValueError("CHANGED_SYMLINK_REQUIRES_REVIEW")
    if root not in path.resolve().parents:
        raise ValueError("CANDIDATE_PATH_OUTSIDE_WORKSPACE")
    return path
