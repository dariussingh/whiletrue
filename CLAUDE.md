# whiletrue — agent guide

Local TypeScript orchestrator that runs pipelines around coding agents. Design: `docs/architecture.md`. Plan: `docs/plan.md`. Work order: `docs/todo.md` (numbered; do items in order, tick when done).

## Commands

Use pnpm inside the devenv shell (`devenv.nix`, same format as `~/dotfiles/devenv`): `devenv shell -- pnpm <cmd>`, or `direnv allow` once and run `pnpm` directly. Corepack is broken on NixOS; don't use it.

- `pnpm check` — format, lint, typecheck, tests with coverage, docs check (run before every commit)
- `pnpm test` / `pnpm coverage` / `pnpm typecheck` / `pnpm lint` / `pnpm format` / `pnpm docs:check`

## Rules (see `docs/conventions.md`)

- TDD; coverage floor lives in `vitest.config.ts`, ratchet it up, never down.
- Update user docs and `docs/architecture.md` in the same change as behavior.
- `@whiletrue-ai/core` must not import git, GitHub, tmux, or any agent. Built-ins use only the public plugin API.
- Track work in `docs/todo.md` (no GitHub issues for now). One todo item (or small group) per commit/PR. The human reviews every line before merge.
- Never commit or push without showing the user the diff and getting explicit approval.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
