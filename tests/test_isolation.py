"""A01 negative checks against silently importing another project's runtime."""

import ast
from pathlib import Path

from ai_commerce.core.identity import ProjectIdentity

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "ai_commerce"
FOREIGN_PACKAGES = ("aios_renew", "python_complete_agent")
FOREIGN_RUNTIME_PATHS = (".ai/tasks/", ".ai/runs/", "AIOS_REPO_ROOT", "python_complete_agent/")


def _module_names(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            found.extend(alias.name for alias in item.names)
        elif isinstance(item, ast.ImportFrom) and item.level == 0 and item.module:
            found.append(item.module)
    return found


def test_no_foreign_runtime_imports() -> None:
    sources = list(SOURCE.rglob("*.py"))
    assert sources, "package source must exist"
    for path in sources:
        imports = _module_names(ast.parse(path.read_text(encoding="utf-8")))
        assert not any(name == foreign or name.startswith(foreign + ".")
                       for foreign in FOREIGN_PACKAGES for name in imports), path


def test_no_foreign_implicit_runtime_paths() -> None:
    for path in SOURCE.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert not any(key in content for key in FOREIGN_RUNTIME_PATHS), path


def test_independent_identity() -> None:
    info = ProjectIdentity()
    assert info.independent is True
    assert info.engineering_flow == "EF01_BUILD_VERIFIED_FOR_SCOPE"
