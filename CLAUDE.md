# whiletrue — agent guide

Local Python orchestrator that runs pipelines around coding agents. Design: `docs/architecture.md`. Plan: `docs/plan.md`. Work order: `docs/todo.md` (numbered; do items in order, tick when done).

## Commands

Use uv inside the devenv shell (`devenv.nix`, same format as `~/dotfiles/devenv/python`): `devenv shell -- uv run poe <task>`, or `direnv allow` once and run `poe <task>` / `pytest` directly (the venv is `.devenv/state/venv`).

- `poe check` — format check, lint, typecheck, tests with coverage, docs check (run before every commit)
- `poe test` / `poe coverage` / `poe typecheck` / `poe lint` / `poe format` / `poe docs-check`

## Rules (see `docs/conventions.md`)

- TDD; unit tests in `tests/unit/<pkg>/`, cross-package tests in `tests/integration/` (never beside source); coverage floor is `fail_under` in `pyproject.toml`, ratchet it up, never down.
- Update user docs and `docs/architecture.md` in the same change as behavior.
- `whiletrue.core` must not import git, GitHub, tmux, or any agent. Built-ins use only the public plugin API.
- Track work in `docs/todo.md` (no GitHub issues for now). One todo item (or small group) per commit/PR. The human reviews every line before merge.
- Never commit or push without showing the user the diff and getting explicit approval.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
