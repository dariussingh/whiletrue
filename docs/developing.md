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
    validate.py                                             static validation
    runtime.py context.py events.py                         scheduler, scopes, event bus
    store.py memory_store.py lock.py replay.py              state protocol, lock file, replay
  control/        strategies: sequence, loop, map, parallel, switch, retry, try
  basics/         modules: shell, exec, agent, set, session.end, context.reset, gate.*
  claude_code/    Claude Code runners: headless.py (claude -p) and interactive.py (live pane)
    endpoint.py   loopback HTTP endpoint the plugin posts to        session.py  per-pane outcome logic
    panes.py      PaneHost: tmux or plain subprocess                transcript.py  usage/cost estimate
    plugin/       the bundled Claude Code plugin: hooks.json, hook.py, mcp_server.py (stdlib only)
  tmux/           TmuxController: session, windows, tiled pane grid, attach
  store_sqlite/   SQLite StateStore and migrations
  recipes/        ralph.yaml, review-pass.yaml (loaded by `composite: recipe:<name>`)
  cli/            argument parsing, commands, default registry, init templates
tests/unit/<package>/    one test module per source module (never beside the source)
tests/integration/       CLI, recipe and interactive-run tests against a fake `claude`
tests/fixtures/          stand-in `claude` scripts (excluded from lint and typecheck)
docs/                    design, plan, todo, reference, guides
```

`tests/unit/test_architecture.py` fails the build if `whiletrue.core` imports another `whiletrue` package. Built-ins register through `PluginApi`, the same way third parties do.

## How a run flows

1. `load_pipeline` parses YAML (errors carry file:line:col), builds the `PipelineNode` tree and expands `composite:` steps.
2. `validate_pipeline` resolves modules, validates configs and CEL expressions, checks wiring caps and the safety rules.
3. `run_pipeline` runs each item through the tree. Every module execution goes through one `execute` function that renders templates, validates config, checks the cache or replay data, runs the module, validates the output cap, and persists the step and events.
4. Strategies run children only via `ctx.run`, so every child is recorded.

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
