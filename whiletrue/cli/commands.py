"""The commands that run, inspect and set up a project (init, plan, run, replay, status, ...)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from whiletrue.cli import gates, ops
from whiletrue.cli.common import EXIT, Io
from whiletrue.cli.project import Loaded, load, lock_hash, validated
from whiletrue.cli.templates import RALPH_AGENT, RALPH_PIPELINE
from whiletrue.core import (
    LOCK_FILE,
    PipelineNode,
    ReplayError,
    hash_pipeline,
    load_recording,
    run_pipeline,
    write_lock,
)
from whiletrue.store_sqlite import SqliteStore
from whiletrue.tmux import TmuxController, session_name


def render_tree(
    node: PipelineNode, prefix: str = "", last: bool = True, root: bool = True
) -> list[str]:
    summary = f" {json.dumps(node.config, separators=(',', ':'))}" if node.config else ""
    connector = "" if root else prefix + ("└─ " if last else "├─ ")
    line = f"{connector}{node.use}{summary}  [{node.id}]"
    child_prefix = "" if root else prefix + ("   " if last else "│  ")
    lines = [line]
    for i, child in enumerate(node.children):
        lines += render_tree(child, child_prefix, i == len(node.children) - 1, False)
    return lines


def init(io: Io, force: bool) -> int:
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


async def run_loaded(io: Io, loaded: Loaded) -> int:
    store = SqliteStore(Path(loaded.cwd) / ".whiletrue" / "state.db")

    def log(level: str, message: str, data: object = None) -> None:
        if level != "debug":
            io.out(f"[{level}] {message}\n")

    try:
        try:
            items = await ops.source_items(loaded)
        except Exception as error:
            io.err(f"Cannot fetch items: {error}\n")
            return EXIT.failed
        problem = ops.cycle_error(items)
        if problem is not None:
            io.err(f"{problem}\n")
            return EXIT.failed
        result = await run_pipeline(
            registry=loaded.registry,
            config=loaded.config,
            store=store,
            cwd=loaded.cwd,
            signal=io.signal,
            log=log,
            lockfile=lock_hash(loaded.cwd),
            items=items,
        )
        io.out(f"run {result.run_id}: {result.status}\n")
        for item in result.items:
            io.out(ops.item_line(item))
        return {
            "done": EXIT.ok,
            "parked": EXIT.parked,
            "cancelled": EXIT.cancelled,
        }.get(result.status, EXIT.failed)
    finally:
        store.close()


async def replay(io: Io, file: str, run_id: str | None) -> int:
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    loaded = load(io, file)
    if isinstance(loaded, int):
        return loaded
    if not validated(io, loaded):
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


async def attach(io: Io) -> int:
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


async def plan_items(io: Io, loaded: Loaded) -> int:
    """With a source, `plan` also lists the items and rejects dependency cycles."""
    if "source" not in loaded.config.extra:
        return EXIT.ok
    try:
        items = await ops.source_items(loaded)
    except Exception as error:
        io.err(f"Cannot fetch items: {error}\n")
        return EXIT.failed
    items = items or []
    io.out(f"items ({len(items)}, up to {loaded.config.parallel} at once):\n")
    for item in items:
        waits = f"  after {', '.join(item.depends_on)}" if item.depends_on else ""
        io.out(f"  {item.id}{waits}\n")
    problem = ops.cycle_error(items)
    if problem is not None:
        io.err(f"{problem}\n")
        return EXIT.failed
    return EXIT.ok


async def panel(io: Io) -> int:  # pragma: no cover (interactive terminal loop)
    from whiletrue.cli.panel import run_panel  # noqa: PLC0415

    store = gates.open_store(io)
    if store is None:
        return EXIT.failed
    tmux = io.tmux or TmuxController(
        session_name(io.cwd), binary=os.environ.get("WHILETRUE_TMUX_BIN", "tmux")
    )
    try:
        return await run_panel(io, store, tmux)
    finally:
        store.close()


def lock(io: Io, file: str) -> int:
    loaded = load(io, file, check_pins=False)
    if isinstance(loaded, int):
        return loaded
    write_lock(Path(loaded.cwd) / LOCK_FILE, loaded.registry.plugin_versions())
    io.out(f"wrote {LOCK_FILE}\n")
    return EXIT.ok


def iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def status(io: Io, run_id: str | None) -> int:
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
        io.out(f"run {run.id}  {run.status}  started {iso(run.started_at)}\n")
        steps = store.list_steps(run.id)
        for item in store.list_items(run.id):
            item_steps = [s for s in steps if s.item_id == item.id]
            cost = sum(s.cost_usd for s in item_steps)
            blocked = ""
            if item.state == "pending":
                states = store.list_events(run.id, item_id=item.id, type="item.state")
                by = states[-1].data.get("blocked_by") if states else None
                blocked = f"  — blocked by {', '.join(by)}" if by else ""
            io.out(
                f"  {item.id}  {item.state}  steps={len(item_steps)}  cost=${cost:.4f}{blocked}\n"
            )
            for s in item_steps:
                port = f"  {s.port}" if s.port else ""
                error = f"  — {s.error}" if s.error else ""
                io.out(f"    {s.node_path}#{s.attempt}  {s.status}{port}{error}\n")
        waiting = store.list_parks(undecided_only=True)
        if waiting:
            io.out("waiting for a person:\n")
            for park in waiting:
                io.out(
                    f"  {park.item_id}  {park.what} at {park.node_path}  "
                    f"(whiletrue approve {park.item_id} | reject {park.item_id} --note ...)\n"
                )
        return EXIT.ok
    finally:
        store.close()
