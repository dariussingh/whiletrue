# Developing whiletrue

For contributors. User-facing docs: [getting-started.md](getting-started.md), [reference.md](reference.md). Extending: [plugins.md](plugins.md). Design: [architecture.md](architecture.md). Engineering rules: [conventions.md](conventions.md).

## Setup

```bash
git clone git@github.com:dariussingh/whiletrue.git && cd whiletrue
direnv allow          # devenv: Python 3.12, uv, venv at .devenv/state/venv (or: devenv shell)
poe check             # format check, lint, pyright, tests + coverage, docs check
```

Without devenv: Python 3.12 and `uv sync --all-groups --locked`, then `uv run poe check`.

| Task | Command |
| --- | --- |
| Everything CI runs | `poe check` |
| Tests / with coverage | `poe test` / `poe coverage` |
| Format / lint / types | `poe format` / `poe lint` / `poe typecheck` |
| Docs links | `poe docs-check` |

## Layout

```
whiletrue/
  abort.py        AbortSignal (cancellation, timeouts)
  core/           the engine; imports no agent, git, GitHub or sibling package
    module.py caps.py outcomes.py registry.py runner.py     the model and plugin API
    config.py composite.py template.py expr.py duration.py  YAML, composites, templates, CEL
    validate.py                                             static validation, one method per rule
    history.py wake.py comments.py source.py workspace.py    records, wake, providers
    runtime/                                                running a pipeline
      model.py      RunItem, ItemOutcome, RunResult, the pipeline hash, the dependency graph
      scheduler.py  run_pipeline: items wait for dependencies and slots
      items.py      ItemRunner: one item's scope, workspace, steps and final state
      steps.py      StepExecutor: one node (record, resolve config, run, record the result)
      journal.py    the result cache and the side-effect journal      env.py  what a run shares
    context.py events.py                                    scopes, event bus
    store.py memory_store.py lock.py replay.py              state protocol, lock file, replay
  control/        strategies: sequence, loop, map, parallel, switch, retry, try
  basics/         modules: shell, exec, agent, set, session.end, context.reset, gate.*
                  and shared helpers: process.py (run a command), stream.py (read a process's
                  stdout line by line with a wall limit), json_extract.py
  claude_code/    Claude Code runners: headless.py (claude -p) and interactive.py (live pane)
    endpoint.py   loopback HTTP endpoint the plugin posts to        session.py  per-pane outcome logic
    transcript.py usage/cost estimate
    plugin/       the bundled Claude Code plugin: hooks.json, hook.py, mcp_server.py (stdlib only)
  opencode/ codex/   the opencode and Codex runners (built on basics/stream.py)
  git/            workspaces (git-worktree, dir-copy, none) and git.commit/commit_push/diff
  github/         client.py (gh CLI), refs.py, providers.py (source, comments, wake),
                  modules.py (open_pr, sync_base, merge), plugin.py (registration)
  tmux/           TmuxController (session, windows, panes) and panes.py: PaneHost (tmux or plain
                  subprocess) and StreamMirror, where agents run and are shown
  store_sqlite/   SQLite StateStore and migrations
  rsi/harness2/   recursive self-improvement (optional); see docs/rsi.md
                  method layer (ported, stdlib only): tree surfaces ledger library runmark
                  selection context prompts, plus snapshot trajectory proposal config
                  runtime layer: env rollout stagecache sessions judging reflection modes
                  strategy checks adopt apply
  recipes/        ralph, review-pass, review-respond, issue-loop (`composite: recipe:<name>`)
  cli/            app.py (usage, one dispatch table), args.py, project.py (load, validate, lock),
                  commands.py (init, plan, run, replay, status, ...), gates.py, ops.py, rsi_cmd.py,
                  panel.py (tmux control panel), registry.py, common.py, init templates
tests/unit/<package>/    one test module per source module (never beside the source)
tests/integration/       CLI, recipe and interactive-run tests against a fake `claude`
tests/fixtures/          stand-in `claude` scripts (excluded from lint and typecheck)
docs/                    design, plan, todo, reference, guides
```

## Architecture rules (enforced)

`tests/unit/test_architecture.py` fails the build when any of these break:

- **Layering.** Each package may import only the packages listed for it (`ALLOWED`): `core` imports only `abort`; `control`, `basics`, `store_sqlite` import `core`; runners, `git`, `github` and `tmux` import `basics`, `core` (and `tmux` for panes); `rsi` imports `core` and `control`; only `cli` sees everything. A new package must be added to that table.
- **No cycles** between packages, and **runners never import each other** (shared code lives in `basics` or `tmux`).
- **The `rsi.harness2` method layer** imports only the standard library and itself, so the ported Harness² logic stays independent of the runtime.
- **Built-ins use only the public plugin API**, the same way third parties do.

## Code quality rules (enforced by `poe check`)

- Functions stay small: McCabe complexity at most 12, and the branch and statement limits of ruff's `PL` rules. Break a long function into named steps or a small class (see `core/runtime/` and `basics/agent.py` for examples), do not add exceptions.
- pyright strict for source and tests; ruff format and lint (`E F I B UP SIM ASYNC RUF PT C4 C90 PL TID`).
- One responsibility per module, one test module per source module, coverage floor in `pyproject.toml` that only goes up.
- Prefer a table to a branch ladder (the CLI dispatches through `COMMANDS`; options through `VALUE_OPTIONS` and `FLAG_OPTIONS`).
- Repeated mechanics get one home (every agent runner reads its process through `basics/stream.py`).

## How a run flows

1. `load_pipeline` parses YAML (errors carry file:line:col), builds the `PipelineNode` tree and expands `composite:` steps.
2. `validate_pipeline` resolves modules, validates configs and CEL expressions, checks wiring caps and the safety rules.
3. `run_pipeline` schedules items (up to `parallel` at once, each after its dependencies; cycles and failed blockers leave items `pending`), creates each item's workspace, and runs it through the tree. Every module execution goes through one `execute` function that renders templates, validates config, checks the cache or replay data, runs the module, validates the output cap, and persists the step and events.
4. Strategies run children only via `ctx.run`, so every child is recorded.
5. A run that parks can be continued: `resume` loads the run's recording, replays every recorded non-`pure` step (except those that parked) and runs the rest for real in a new run of the same *lineage*. Gates find their park by (item, node, lineage, Nth execution), side effects with `journal=True` consult the effect journal, and the replay keys `(item, node, attempt)` stay valid because attempt counters restart in each run.

## Workflow

- **TDD.** Write the failing test first. Unit tests mirror the source path; use `MemoryStore` and scripted modules (see `tests/unit/control/test_control.py`). CLI behaviour goes in `tests/integration/` with a fake `claude` shell script (`WHILETRUE_CLAUDE_BIN`).
- **Coverage floor** is `fail_under` in `pyproject.toml`; ratchet it up, never down.
- **Docs in the same change.** Behaviour change means: `docs/reference.md` and `getting-started.md` for users, `plugins.md` for the extension API, `architecture.md` for design.
- **Track work** in `docs/todo.md` (numbered, tick when done); one item or small group per commit; conventional messages (`feat(control): ...`).
- **Typing.** `pyright` strict for the package, looser for tests. Prefer pydantic models for config, `Literal` unions for closed sets.
- **Adding a built-in module/strategy:** write it under `basics/` or `control/`, register it in that package's `__init__.py` plugin function, add the unit test, update `reference.md`. Remember `cacheable=False` for anything reading the workspace.
- **Adding a store method:** extend the `StateStore` protocol, both stores, and add a migration (never edit a shipped one) if the schema changes.

## Manual testing

`docs/m0-smoke.md` runs a real Claude Code loop (a few tenths of a cent). For everything else use a fake agent: see `fake_claude` in `tests/integration/test_cli.py`.
