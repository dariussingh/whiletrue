"""Import boundaries from docs/conventions.md."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "whiletrue"
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


#: Which sibling packages each package may import. Everything else is a layering violation.
#: Read it as a dependency graph: `core` at the bottom, the CLI at the top, nothing circular.
ALLOWED: dict[str, set[str]] = {
    "abort": set(),
    "core": {"abort"},
    "control": {"core"},
    "basics": {"abort", "core"},
    "store_sqlite": {"core"},
    "tmux": {"basics", "core"},
    "git": {"basics", "core"},
    "github": {"basics", "core"},
    "claude_code": {"basics", "core", "tmux"},
    "opencode": {"basics", "core", "tmux"},
    "codex": {"basics", "core", "tmux"},
    "rsi": {"abort", "control", "core"},
    "recipes": set(),  # data only (YAML files)
    "cli": {
        "abort", "basics", "claude_code", "codex", "control", "core", "git", "github",
        "opencode", "rsi", "store_sqlite", "tmux",
    },
}  # fmt: skip


def package_edges() -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        parts = path.relative_to(SRC).with_suffix("").parts
        if parts[0] in {"__init__", "__main__"}:
            continue
        mine = parts[0]
        for module in imported_modules(path):
            pieces = module.split(".")
            if pieces[0] == "whiletrue" and len(pieces) > 1 and pieces[1] != mine:
                edges.setdefault(mine, set()).add(pieces[1])
    return edges


def test_every_package_is_listed_and_only_imports_what_it_may() -> None:
    edges = package_edges()
    on_disk = {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
    on_disk |= {"abort"}
    assert on_disk <= set(ALLOWED), f"add the new package to ALLOWED: {on_disk - set(ALLOWED)}"
    violations = {
        f"{pkg} -> {dep}"
        for pkg, deps in edges.items()
        for dep in deps
        if dep not in ALLOWED.get(pkg, set())
    }
    assert violations == set()


def test_the_package_graph_has_no_cycles() -> None:
    edges = package_edges()
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        state[node] = 1
        for dep in sorted(edges.get(node, ())):
            assert state.get(dep) != 1, f"import cycle: {' -> '.join([*trail, node, dep])}"
            if dep not in state:
                visit(dep, [*trail, node])
        state[node] = 2

    for package in sorted(edges):
        if package not in state:
            visit(package, [])


def test_runners_do_not_depend_on_each_other() -> None:
    runners = {"claude_code", "opencode", "codex"}
    for runner in runners:
        assert not (package_edges().get(runner, set()) & (runners - {runner})), runner


#: The method layer of `rsi.harness2` (ported from Harness²) knows nothing about whiletrue's
#: runtime: it imports only the standard library and itself. The runtime layer builds on it.
METHOD_LAYER = {
    "tree", "surfaces", "ledger", "library", "runmark", "selection", "context", "prompts",
    "snapshot", "trajectory", "proposal", "config",
}  # fmt: skip


def test_the_rsi_method_layer_does_not_depend_on_the_runtime() -> None:
    offenders: list[str] = []
    for name in sorted(METHOD_LAYER):
        path = SRC / "rsi" / "harness2" / f"{name}.py"
        for module in imported_modules(path):
            if module.startswith("whiletrue.") and not module.startswith("whiletrue.rsi.harness2"):
                offenders.append(f"{name}: {module}")
    assert offenders == []
