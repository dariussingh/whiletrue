"""The `whiletrue` command: usage text, dispatch and the process entry point."""

from __future__ import annotations

import asyncio
import signal as signal_module
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from whiletrue.abort import AbortSignal
from whiletrue.cli import commands, gates, ops, rsi_cmd
from whiletrue.cli import project as project_module
from whiletrue.cli.args import Args, parse_args
from whiletrue.cli.common import EXIT, Io
from whiletrue.cli.project import Loaded, lock_hash, locked, validated, with_loaded

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
  panel             The control panel (tmux window 0): items, state, who they wait for
  resume [run]      Continue a parked run (a decided gate lets its item through)
  approve <item>    Approve the gate an item is parked at   [--note, --step]
  reject <item>     Reject it with a reason                 --note "what to change"
  comment <item> "text"   Leave a comment the pipeline must answer
  rerun <item>      Run one item again as a new run                [--from <step>]
  cancel <item>     Cancel an item (a running one stops before its next step)
  logs <item>       The item's event log                            [--follow]
  cost [run]        Spend by item, step and runner
  history <item>    The item's timeline: parks, decisions, comments, replies, steers
  learnings [approve|reject <id>]   Review lessons proposed by history.distill
  rsi <status|diff|library|adopt|reset>   What recursive self-improvement proposed

Options:
  -f, --file <path> Pipeline file (default: whiletrue.yaml)
  --force           init: overwrite existing files
  --yes             rsi adopt: confirm a harness that changes executable files
  -m, --note <text> approve/reject: the reason
  --step <id>       approve/reject: which gate, when an item waits at several
  -h, --help        Show this help
"""


Handler = Callable[[Io, Args, list[str]], Awaitable[int]]


def _arg(rest: list[str], index: int = 0) -> str | None:
    return rest[index] if len(rest) > index else None


async def _validate(io: Io, args: Args, rest: list[str]) -> int:
    loaded = project_module.load(io, args.file)
    if isinstance(loaded, int):
        return loaded
    if not validated(io, loaded):
        return EXIT.failed
    io.out(f"{loaded.config.file}: ok\n")
    return EXIT.ok


async def _plan(io: Io, args: Args, rest: list[str]) -> int:
    loaded = project_module.load(io, args.file)
    if isinstance(loaded, int):
        return loaded
    if not validated(io, loaded):
        return EXIT.failed
    io.out(f"{loaded.config.name}\n{chr(10).join(commands.render_tree(loaded.config.root))}\n")
    return await commands.plan_items(io, loaded)


async def _run(io: Io, args: Args, rest: list[str]) -> int:
    async def action(loaded: Loaded) -> int:
        return await locked(io, loaded, lambda: commands.run_loaded(io, loaded))

    return await with_loaded(io, args.file, action)


async def _resume(io: Io, args: Args, rest: list[str]) -> int:
    async def action(loaded: Loaded) -> int:
        return await locked(
            io, loaded, lambda: ops.resume(io, loaded, _arg(rest), lock_hash(loaded.cwd))
        )

    return await with_loaded(io, args.file, action)


async def _rerun(io: Io, args: Args, rest: list[str]) -> int:
    item = _arg(rest)
    if item is None:
        io.err("Usage: whiletrue rerun <item> [--from <step>]\n")
        return EXIT.usage

    async def action(loaded: Loaded) -> int:
        return await locked(
            io,
            loaded,
            lambda: ops.rerun(io, loaded, item, args.from_step, lock_hash(loaded.cwd)),
        )

    return await with_loaded(io, args.file, action)


async def _comment(io: Io, args: Args, rest: list[str]) -> int:
    return gates.comment(io, _arg(rest), " ".join(rest[1:]) if len(rest) > 1 else None)


def _sync(fn: Callable[[Io, Args, list[str]], int]) -> Handler:
    """Wrap a command that does not await so every command has one shape."""

    async def handler(io: Io, args: Args, rest: list[str]) -> int:
        return fn(io, args, rest)

    return handler


#: Every command: its name to what runs it. Adding a command is adding a line here.
COMMANDS: dict[str, Handler] = {
    "init": _sync(lambda io, args, rest: commands.init(io, args.force)),
    "validate": _validate,
    "plan": _plan,
    "run": _run,
    "replay": lambda io, args, rest: commands.replay(io, args.file, _arg(rest)),
    "attach": lambda io, args, rest: commands.attach(io),
    "panel": lambda io, args, rest: commands.panel(io),
    "resume": _resume,
    "rerun": _rerun,
    "cancel": _sync(lambda io, args, rest: ops.cancel(io, _arg(rest))),
    "logs": lambda io, args, rest: ops.logs(io, _arg(rest), args.follow),
    "cost": _sync(lambda io, args, rest: ops.cost(io, _arg(rest))),
    "approve": _sync(
        lambda io, args, rest: gates.decide(io, "approved", _arg(rest), args.note, args.step)
    ),
    "reject": _sync(
        lambda io, args, rest: gates.decide(io, "rejected", _arg(rest), args.note, args.step)
    ),
    "comment": _comment,
    "history": _sync(lambda io, args, rest: gates.history(io, _arg(rest))),
    "learnings": _sync(lambda io, args, rest: gates.learnings(io, _arg(rest), _arg(rest, 1))),
    "rsi": _sync(lambda io, args, rest: rsi_cmd.run(io, _arg(rest), _arg(rest, 1), yes=args.yes)),
    "lock": _sync(lambda io, args, rest: commands.lock(io, args.file)),
    "status": _sync(lambda io, args, rest: commands.status(io, _arg(rest))),
}


async def main(argv: list[str], io: Io) -> int:
    try:
        args = parse_args(argv)
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
    handler = COMMANDS.get(command)
    if handler is None:
        io.err(f'Unknown command "{command}"\n{USAGE}')
        return EXIT.usage
    return await handler(io, args, rest)


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
