from __future__ import annotations

import asyncio
import json
import os
import signal as signal_module
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from whiletrue.abort import AbortSignal
from whiletrue.cli.registry import create_default_registry, load_plugins
from whiletrue.cli.templates import RALPH_AGENT, RALPH_PIPELINE
from whiletrue.core import (
    LOCK_FILE,
    ConfigError,
    LockError,
    PipelineConfig,
    PipelineNode,
    Registry,
    ReplayError,
    check_lock,
    format_diagnostic,
    has_errors,
    hash_pipeline,
    load_pipeline,
    load_recording,
    read_lock,
    run_pipeline,
    sha256_file,
    validate_pipeline,
    write_lock,
)
from whiletrue.store_sqlite import SqliteStore
from whiletrue.tmux import TmuxController, session_name


@dataclass
class Io:
    cwd: str
    out: Callable[[str], object]
    err: Callable[[str], object]
    signal: AbortSignal | None = None
    #: The project's tmux controller; defaults to the real one (tests inject a fake).
    tmux: TmuxController | None = None
    #: Replaces this process with the given argv (tmux attach); defaults to `os.execvp`.
    attach: Callable[[list[str]], object] | None = None


USAGE = """whiletrue — run configurable loops around coding agents

Usage: whiletrue <command> [options]

Commands:
  init              Scaffold whiletrue.yaml and agents/ralph.md
  validate          Check config, wiring and safety rules (no tokens spent)
  plan              Print the expanded pipeline tree (no tokens spent)
  run               Run the pipeline
  status [run]      Show the latest (or given) run
  replay [run]      Re-run a recorded run's logic without calling agents or tools
  lock              Pin the plugin versions in use to whiletrue.lock
  attach            Attach to the project's tmux session (live agent panes)

Options:
  -f, --file <path> Pipeline file (default: whiletrue.yaml)
  --force           init: overwrite existing files
  -h, --help        Show this help
"""


class Exit:
    ok = 0
    failed = 1
    parked = 2
    usage = 64
    cancelled = 130


EXIT = Exit()


@dataclass
class _Loaded:
    config: PipelineConfig
    registry: Registry
    cwd: str


def _load(io: Io, file: str, *, check_pins: bool = True) -> _Loaded | int:
    path = (Path(io.cwd) / file).resolve()
    try:
        config = load_pipeline(path)
    except ConfigError as error:
        io.err(f"{error}\n")
        return EXIT.failed
    except OSError as error:
        io.err(f"Cannot load {file}: {error}\n")
        return EXIT.failed
    registry = create_default_registry()
    try:
        load_plugins(registry, config.uses, str(path.parent))
    except Exception as error:
        io.err(f"{error}\n")
        return EXIT.failed
    cwd = str(path.parent)
    if not check_pins:
        return _Loaded(config, registry, cwd)
    try:
        pinned = read_lock(Path(cwd) / LOCK_FILE)
    except LockError as error:
        io.err(f"{error}\n")
        return EXIT.failed
    if pinned is not None:
        problems = check_lock(pinned, registry.plugin_versions())
        if problems:
            io.err("".join(f"{p}\n" for p in problems) + "Run `whiletrue lock` to update it.\n")
            return EXIT.failed
    return _Loaded(config, registry, cwd)


def _validated(io: Io, loaded: _Loaded) -> bool:
    diagnostics = validate_pipeline(loaded.config, loaded.registry, cwd=loaded.cwd)
    for d in diagnostics:
        io.err(f"{format_diagnostic(d)}\n")
    return not has_errors(diagnostics)


def _render_tree(
    node: PipelineNode, prefix: str = "", last: bool = True, root: bool = True
) -> list[str]:
    summary = f" {json.dumps(node.config, separators=(',', ':'))}" if node.config else ""
    connector = "" if root else prefix + ("└─ " if last else "├─ ")
    line = f"{connector}{node.use}{summary}  [{node.id}]"
    child_prefix = "" if root else prefix + ("   " if last else "│  ")
    lines = [line]
    for i, child in enumerate(node.children):
        lines += _render_tree(child, child_prefix, i == len(node.children) - 1, False)
    return lines


def _init(io: Io, force: bool) -> int:
    files = [("whiletrue.yaml", RALPH_PIPELINE), ("agents/ralph.md", RALPH_AGENT)]
    existing = [name for name, _ in files if (Path(io.cwd) / name).exists()]
    if existing and not force:
        io.err(f"Refusing to overwrite {', '.join(existing)} (use --force)\n")
        return EXIT.failed
    for name, content in files:
        target = Path(io.cwd) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        io.out(f"created {name}\n")
    io.out("Next: whiletrue validate && whiletrue plan && whiletrue run\n")
    return EXIT.ok


async def _run(io: Io, file: str) -> int:
    loaded = _load(io, file)
    if isinstance(loaded, int):
        return loaded
    if not _validated(io, loaded):
        return EXIT.failed

    store = SqliteStore(Path(loaded.cwd) / ".whiletrue" / "state.db")

    def log(level: str, message: str, data: object = None) -> None:
        if level != "debug":
            io.out(f"[{level}] {message}\n")

    try:
        result = await run_pipeline(
            registry=loaded.registry,
            config=loaded.config,
            store=store,
            cwd=loaded.cwd,
            signal=io.signal,
            log=log,
            lockfile=_lock_hash(loaded.cwd),
        )
        io.out(f"run {result.run_id}: {result.status}\n")
        for item in result.items:
            io.out(f"  {item.item_id}: {item.state} ({item.port})\n")
        return {
            "done": EXIT.ok,
            "parked": EXIT.parked,
            "cancelled": EXIT.cancelled,
        }.get(result.status, EXIT.failed)
    finally:
        store.close()


async def _replay(io: Io, file: str, run_id: str | None) -> int:
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    loaded = _load(io, file)
    if isinstance(loaded, int):
        return loaded
    if not _validated(io, loaded):
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        if run_id is None:
            latest = store.latest_run()
            run_id = latest.id if latest else ""
        try:
            recording = load_recording(store, run_id)
        except ReplayError as error:
            io.err(f"{error}\n")
            return EXIT.failed
        if recording.pipeline_hash != hash_pipeline(loaded.config):
            io.out("note: the pipeline changed since the recorded run\n")
        result = await run_pipeline(
            registry=loaded.registry,
            config=loaded.config,
            store=store,
            cwd=loaded.cwd,
            signal=io.signal,
            items=recording.items,
            replay=recording.steps,
        )
        io.out(f"replay of {recording.run_id}: run {result.run_id}: {result.status}\n")
        differences = [
            f"  {o.item_id}: recorded {recording.item_states.get(o.item_id)}, replayed {o.state}"
            for o in result.items
            if recording.item_states.get(o.item_id) != o.state
        ]
        if result.status != recording.status or differences:
            io.err(
                f"replay differs: recorded {recording.status}, replayed {result.status}\n"
                + "".join(f"{d}\n" for d in differences)
            )
            return EXIT.failed
        io.out(f"replay matches the recorded run ({recording.status})\n")
        return EXIT.ok
    finally:
        store.close()


async def _attach(io: Io) -> int:
    tmux = io.tmux or TmuxController(
        session_name(io.cwd), binary=os.environ.get("WHILETRUE_TMUX_BIN", "tmux")
    )
    if not await tmux.has_session():
        io.err(
            f"No tmux session {tmux.session!r}: it exists while a run uses an interactive agent\n"
        )
        return EXIT.failed
    argv = tmux.attach_argv()
    if io.attach is not None:
        io.attach(argv)
    else:  # pragma: no cover (replaces this process)
        os.execvp(argv[0], argv)
    return EXIT.ok


def _lock_hash(cwd: str) -> str | None:
    path = Path(cwd) / LOCK_FILE
    return sha256_file(path) if path.is_file() else None


def _lock(io: Io, file: str) -> int:
    loaded = _load(io, file, check_pins=False)
    if isinstance(loaded, int):
        return loaded
    write_lock(Path(loaded.cwd) / LOCK_FILE, loaded.registry.plugin_versions())
    io.out(f"wrote {LOCK_FILE}\n")
    return EXIT.ok


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _status(io: Io, run_id: str | None) -> int:
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        run = store.get_run(run_id) if run_id else store.latest_run()
        if run is None:
            io.err(f'No run "{run_id}"\n' if run_id else "No runs yet\n")
            return EXIT.failed
        io.out(f"run {run.id}  {run.status}  started {_iso(run.started_at)}\n")
        steps = store.list_steps(run.id)
        for item in store.list_items(run.id):
            item_steps = [s for s in steps if s.item_id == item.id]
            cost = sum(s.cost_usd for s in item_steps)
            io.out(f"  {item.id}  {item.state}  steps={len(item_steps)}  cost=${cost:.4f}\n")
            for s in item_steps:
                port = f"  {s.port}" if s.port else ""
                error = f"  — {s.error}" if s.error else ""
                io.out(f"    {s.node_path}#{s.attempt}  {s.status}{port}{error}\n")
        return EXIT.ok
    finally:
        store.close()


@dataclass
class _Args:
    positionals: list[str]
    file: str = "whiletrue.yaml"
    force: bool = False
    help: bool = False


def _parse_args(argv: list[str]) -> _Args:
    args = _Args(positionals=[])
    it = iter(argv)
    for arg in it:
        if arg in ("-f", "--file"):
            value = next(it, None)
            if value is None:
                raise ValueError(f"Option '{arg}' argument missing")
            args.file = value
        elif arg.startswith("--file="):
            args.file = arg.split("=", 1)[1]
        elif arg == "--force":
            args.force = True
        elif arg in ("-h", "--help"):
            args.help = True
        elif arg.startswith("-") and arg != "-":
            raise ValueError(f"Unknown option '{arg}'")
        else:
            args.positionals.append(arg)
    return args


async def main(argv: list[str], io: Io) -> int:
    try:
        args = _parse_args(argv)
    except ValueError as error:
        io.err(f"{error}\n{USAGE}")
        return EXIT.usage
    if args.help:
        io.out(USAGE)
        return EXIT.ok
    if not args.positionals:
        io.err(USAGE)
        return EXIT.usage
    command, *rest = args.positionals

    match command:
        case "init":
            return _init(io, args.force)
        case "validate":
            loaded = _load(io, args.file)
            if isinstance(loaded, int):
                return loaded
            if not _validated(io, loaded):
                return EXIT.failed
            io.out(f"{loaded.config.file}: ok\n")
            return EXIT.ok
        case "plan":
            loaded = _load(io, args.file)
            if isinstance(loaded, int):
                return loaded
            if not _validated(io, loaded):
                return EXIT.failed
            io.out(f"{loaded.config.name}\n{chr(10).join(_render_tree(loaded.config.root))}\n")
            return EXIT.ok
        case "run":
            return await _run(io, args.file)
        case "replay":
            return await _replay(io, args.file, rest[0] if rest else None)
        case "attach":
            return await _attach(io)
        case "lock":
            return _lock(io, args.file)
        case "status":
            return _status(io, rest[0] if rest else None)
        case _:
            io.err(f'Unknown command "{command}"\n{USAGE}')
            return EXIT.usage


async def _amain(argv: list[str]) -> int:
    abort = AbortSignal()
    loop = asyncio.get_running_loop()
    for sig in (signal_module.SIGINT, signal_module.SIGTERM):
        loop.add_signal_handler(sig, abort.abort)
    return await main(
        argv,
        Io(
            cwd=str(Path.cwd()),
            out=sys.stdout.write,
            err=sys.stderr.write,
            signal=abort,
        ),
    )


def entrypoint() -> None:
    """Console-script entry point (`whiletrue`)."""
    sys.exit(asyncio.run(_amain(sys.argv[1:])))
