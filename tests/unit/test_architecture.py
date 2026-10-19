"""Import boundaries from docs/conventions.md."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "whiletrue"
FORBIDDEN_IN_CORE = {"git", "gh", "github", "tmux", "claude_code", "opencode", "basics", "cli"}


def imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_core_imports_no_agent_vcs_or_sibling_package() -> None:
    offenders: list[str] = []
    for path in (SRC / "core").rglob("*.py"):
        for module in imported_modules(path):
            parts = module.split(".")
            if parts[0] in FORBIDDEN_IN_CORE or (
                parts[0] == "whiletrue" and len(parts) > 1 and parts[1] not in {"core", "abort"}
            ):
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_builtin_packages_do_not_reach_into_each_others_internals() -> None:
    """Built-ins use only the public plugin API: control never imports basics or the CLI."""
    for path in (SRC / "control").rglob("*.py"):
        for module in imported_modules(path):
            assert not module.startswith(("whiletrue.basics", "whiletrue.cli")), module
