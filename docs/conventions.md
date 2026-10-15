# Conventions

- **Language/tooling:** TypeScript (strict), Node LTS, pnpm workspaces, zod for schemas.
- **Tests:** TDD. Every package has unit tests; integration tests run pipelines against fake runners. Coverage floor enforced in CI and ratcheted up.
- **Docs:** user docs and `docs/architecture.md` updated in the same PR as the behavior change.
- **Commits:** small, conventional-style messages (`feat(core): ...`). One issue per branch/PR.
- **Plugin API parity:** built-in modules may only use public `@whiletrue-ai/core` APIs.
- **Accountability:** AI writes, the human verifies every line before merge.
- **Layout:** `packages/<name>` for each `@whiletrue-ai/*` package; `docs/` for design; `.whiletrue/` is git-ignored runtime state.
