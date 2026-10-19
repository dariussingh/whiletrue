# Conventions

- **Language/tooling:** Python 3.12+, `uv` for environments and locking, pydantic for schemas, asyncio for concurrency, `ruff` (lint + format), `pyright` (strict), `pytest` + `coverage`. Run everything through `uv run poe <task>` (see `pyproject.toml`) inside the devenv shell.
- **Tests:** TDD. Tests never live next to source. Unit tests go in `tests/unit/<package>/test_<module>.py` (mirroring `src/whiletrue/<package>/`); cross-package and end-to-end tests go in `tests/integration/`. Tests are type-checked by pyright. Coverage floor (`fail_under` in `pyproject.toml`) is enforced in CI and ratcheted up.
- **Docs:** user docs and `docs/architecture.md` updated in the same PR as the behavior change.
- **Commits:** small, conventional-style messages (`feat(core): ...`). One issue per branch/PR.
- **Plugin API parity:** built-in modules may only use the public `whiletrue.core` API; `whiletrue.core` imports no other `whiletrue.*` subpackage (`tests/unit/test_architecture.py`).
- **Accountability:** AI writes, the human verifies every line before merge.
- **Layout:** `src/whiletrue/<package>/` for each subpackage; `tests/` for tests; `docs/` for design; `.whiletrue/` is git-ignored runtime state.
- **Style:** snake_case everywhere (fields in events and the store too); modules declare configs as pydantic models; no `Any` leaks past a module boundary without a reason.
