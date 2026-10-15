# Conventions

- **Language/tooling:** TypeScript (strict), Node LTS, pnpm workspaces, zod for schemas.
- **Tests:** TDD. Tests never live next to source. Unit tests go in `packages/<name>/tests/<module>.test.ts` (mirroring `src/`, importing `../src/...`); cross-package and end-to-end tests go in `tests/integration/` and import packages by name (`@whiletrue-ai/core`). Tests are type-checked (`tsconfig.test.json`). Coverage floor enforced in CI and ratcheted up.
- **Docs:** user docs and `docs/architecture.md` updated in the same PR as the behavior change.
- **Commits:** small, conventional-style messages (`feat(core): ...`). One issue per branch/PR.
- **Plugin API parity:** built-in modules may only use public `@whiletrue-ai/core` APIs.
- **Accountability:** AI writes, the human verifies every line before merge.
- **Layout:** `packages/<name>` for each `@whiletrue-ai/*` package; `docs/` for design; `.whiletrue/` is git-ignored runtime state.
