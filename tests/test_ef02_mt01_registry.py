"""Trusted EF02 MT-01 registry: deterministic bounded pytest argv.

The registry alone does not prove affected-AC coverage. Runtime MT-01 must
still establish a full changed-path / consumer / contract closure or fallback.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / ".github" / "ef02" / "verify-profiles.json"
REQUIRED_COMPONENTS = {
    "mt01-a02-tests",
    "mt01-a03-tests",
    "mt01-a04-tests",
    "mt01-a05-tests",
    "mt01-a06-tests",
    "mt01-a07-tests",
    "mt01-a08-tests",
    "mt01-a09-tests",
    "mt01-a10-tests",
    "mt01-a10-m02-tests",
    "mt01-a11-tests",
    "mt01-a12-tests",
    "mt01-cli-tests",
    "mt01-ef01-tests",
    "mt01-repair-tests",
    "mt01-integrity-tests",
    "mt01-provider-tests",
    "mt01-mt01-tests",
    "mt01-ef01p-tests",
    "mt01-diagnostics-tests",
    "mt01-git-integrity-tests",
    "mt01-ef02-recovery-tests",
    "mt01-ef02-wake-tests",
}


def _profiles() -> dict[str, list[str]]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data
    assert all(isinstance(k, str) and k for k in data)
    return data


def test_mt01_registry_preserves_existing_authoritative_profiles() -> None:
    profiles = _profiles()
    assert profiles["project-tests"] == ["python", "-m", "pytest", "tests", "-q", "-n", "12"]
    assert profiles["ef02-tests"] == ["python", "-m", "pytest", "tests/test_ef02.py", "-q", "-n", "12"]
    assert profiles["foundation-tests"] == ["python", "-m", "pytest", "tests/test_foundation.py", "-q", "-n", "12"]
    assert profiles["project-tests-serial"] == ["python", "-m", "pytest", "tests", "-q"]


def test_mt01_component_profiles_are_registered_with_twelve_workers() -> None:
    profiles = _profiles()
    assert REQUIRED_COMPONENTS <= set(profiles)
    for name in sorted(REQUIRED_COMPONENTS):
        argv = profiles[name]
        assert argv[:3] == ["python", "-m", "pytest"], name
        assert argv[-3:] == ["-q", "-n", "12"], name
        paths = argv[3:-3]
        assert paths and len(paths) == len(set(paths)), name
        for path in paths:
            assert path.startswith("tests/") and path.endswith(".py"), (name, path)
            assert (ROOT / path).is_file(), (name, path)


def test_mt01_registry_has_no_unregistered_shell_or_arbitrary_pytest_options() -> None:
    for name, argv in _profiles().items():
        assert isinstance(argv, list) and all(isinstance(x, str) and x for x in argv), name
        assert argv[:3] == ["python", "-m", "pytest"], name
        if name == "project-tests-serial":
            assert argv[3:] == ["tests", "-q"]
            continue
        assert argv[-3:] == ["-q", "-n", "12"], name
        scopes = argv[3:-3]
        assert scopes and all(
            scope == "tests" or (scope.startswith("tests/") and scope.endswith(".py"))
            for scope in scopes
        ), name
        assert all((ROOT / scope).exists() for scope in scopes), name
