"""A01 targeted command and truthful project-state checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_commerce import __version__
from ai_commerce.cli import main
from ai_commerce.core.identity import ProjectIdentity

ROOT = Path(__file__).resolve().parents[1]


def _invoke_module(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "ai_commerce", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_module_help_outside_repository(tmp_path: Path) -> None:
    result = _invoke_module("--help", cwd=tmp_path)
    assert result.returncode == 0
    assert "hello" in result.stdout and "about" in result.stdout


def test_hello_via_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hello"]) == 0
    assert "foundation ready" in capsys.readouterr().out


def test_about_json_truthful_and_parseable(tmp_path: Path) -> None:
    result = _invoke_module("about", "--json", cwd=tmp_path)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data == ProjectIdentity().to_dict()
    assert data["version"] == __version__
    assert data["engineering_flow"] == "NOT_IMPLEMENTED"
    assert data["product_runtime"] == "NOT_IMPLEMENTED"


def test_missing_command_exits_nonzero(tmp_path: Path) -> None:
    result = _invoke_module(cwd=tmp_path)
    assert result.returncode == 2


def test_unknown_command_exits_nonzero(tmp_path: Path) -> None:
    result = _invoke_module("publish", cwd=tmp_path)
    assert result.returncode != 0
