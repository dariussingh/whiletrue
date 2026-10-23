# Implementation TODO (ordered)

> Tracking lives in this file, not GitHub issues, until whiletrue can run its own workflow (dogfooding starts at M1).

Work top to bottom. Items are numbered globally; `[ ]` = open. A milestone is done when its **Exit** line is demonstrably true. Each item: tests first, docs updated in the same change. See [plan.md](plan.md).

## Phase 0 · Repo & decisions

1. [x] Create GitHub repo `dariussingh/whiletrue`; push initial commit (docs).
2. [x] License decided: MIT (`LICENSE` added). (OQ-7)
3. [x] Package naming. Originally an npm org `whiletrue-ai`; superseded by the Python port (OQ-9): one PyPI distribution `whiletrue` (name free as of 2026-10-16; claim it at release, item 76). GitHub org dropped: repo stays at `dariussingh/whiletrue`. (OQ-8)
4. [x] Scaffold the project: `pyproject.toml` (uv, hatchling), `whiletrue/`, `tests/`. (Originally a pnpm workspace; ported to Python, see item 8.5.)
5. [x] Tooling: ruff, pyright (strict), pytest, coverage floor, `.editorconfig`.
6. [x] CI (GitHub Actions): install, format check, lint, typecheck, test+coverage, docs check.
7. [x] `docs-check` script (`scripts/docs_check.py`) (fails when public behavior changes without doc updates; start with link/example validation).
8. [x] `CLAUDE.md`, issue and PR templates added. GitHub milestones and issues deliberately **not** created: until whiletrue's own workflow exists, progress is tracked in `docs/todo.md` only.

       8.5. [x] **Port to Python (2026-10-16, owner decision, OQ-9).** The TypeScript implementation (M0, commits up to `8ba9bf7`) was re-implemented 1:1 in Python 3.12 (`whiletrue/`, 260 tests, ~99% coverage) and the TS tree removed. Naming: `@whiletrue-ai/<x>` became `whiletrue.<x>` (`store-sqlite` → `store_sqlite`, `claude-code` → `claude_code`); camelCase became snake_case (`runId` → `run_id`, `defineAction` → `define_action`, `idempotencyKey` → `idempotency_key`); zod → pydantic; vitest → pytest; devenv switched to the Python template; npm publishing (item 76) became PyPI. Deliberate behavior differences: YAML is read as YAML 1.2-ish (only `true`/`false` are booleans, timestamps stay strings); the built-in plugin names are `whiletrue.basics|control|claude_code`; third-party plugins are found via the `whiletrue.plugins` entry-point group or an importable module exposing `plugin`; `init` scaffolds `pytest` as the gate.

## Phase M0 · Core engine

> **M0 status: complete, awaiting owner review.** Deviations from the plan, all deliberate: items 16 and 17 landed before 15; `gate.shell` (item 29) and the `agent` action were built early in `whiletrue.basics`; a minimal bounded `sequence` and `loop` (only `max` and `stop_when`) live in `whiletrue.control` so the Ralph exit works, and item 25-26 will extend them; `MemoryStore` was added to core for tests. Manual real-Claude smoke: see [m0-smoke.md](m0-smoke.md).

9. [x] `whiletrue.core`: define `ModuleMeta`, `ActionModule`, `Strategy`, `Emit`, `PortSpec`, `PortValues`, effects enum.
       9.5. [x] devenv + direnv dev environment (`devenv.nix`, `devenv.yaml`, `.envrc`) following the `~/dotfiles/devenv` template format: Python 3.12 + uv (was Node 24 + pnpm), git, gh, tmux. Replaces the interim `flake.nix`.
10. [x] Caps: pydantic → JSON Schema; built-in caps `Issue, Diff, PullRequest, Verdict, ReviewFindings, Text, Any`; compatibility check.
11. [x] Outcome ports (`ok, fail, needs_human, skip, rejected`) and unwired-port fallback policy.
12. [x] Module/strategy/runner/provider registry + public plugin API.
13. [x] YAML config loader (`whiletrue.yaml`, `agents/*.md` frontmatter, `loops/*.yaml`) with schema errors that cite file/line.
14. [x] Validator: resolve modules, type-check wires, enforce `loop.max`, agent `limits`, gate determinism, side-effect idempotency keys.
15. [x] `StepContext` / `CompositeContext` (config, scoped KV, workspace, sessions, event bus, logger, cancellation); `ctx.run(child, input)` as the only child-execution path.
16. [x] Event bus (append-only typed events).
17. [x] `StateStore` interface.
18. [x] `whiletrue.store_sqlite`: migrations and tables `runs, items, steps, parks, sessions, effects, learnings, events, cache`.
19. [x] Sequential scheduler: run an item through a module tree, persist every step.
20. [x] `whiletrue.basics`: `shell`, `exec` (JSON over stdin/stdout).
21. [x] `whiletrue.claude_code` headless runner (`claude -p --output-format stream-json`, `--json-schema`, `--max-turns`, `--max-budget-usd`, `--permission-prompts none`); runner capability declaration.
22. [x] `whiletrue.cli`: `init`, `validate`, `plan` (expanded tree, no tokens), `run`, `status`.
23. [x] **M0 exit:** a bounded Ralph loop runs on the headless runner against a toy repo (integration test with a fake runner + one real smoke run).

## Phase M1 · Control flow & composites

24. [x] Spike Python CEL libraries (`cel-python`, `common-expression-language`: schema type-check, restricted functions, timeout/cost limit); pick one or fall back to a small whiletrue-owned subset + own cap checker. Implement the `Expr` interface (`parse`, `check`, `eval`) and sandboxed evaluator for `until:`/templates. (OQ-6, decided: CEL)
25. [x] `whiletrue.control`: `sequence` with automatic `out → in` wiring and explicit `wires:`.
26. [x] `loop` (`max`/`max: unbounded`, `until`, `over`, `collect`).
27. [x] `map` (sequential first), `parallel`, `switch`.
28. [x] `retry` (`max`, `backoff`, `feedback`) and `try`; default park-as-`needs_human` on exhausted retries.
29. [x] Gates: `gate.shell`, `gate.tests`, `gate.diff_size`; validator enforces `deterministic`.
30. [x] Result cache for `pure`/`deterministic` modules keyed by input hash.
31. [x] Composites: declared ports/params, `uses:` import, nesting, param templating.
32. [x] `whiletrue.lock` (plugin version pins) and per-run hashes of pipeline/composite/prompt files.
33. [x] Record agent outputs; `whiletrue replay <run>` re-runs logic with no model calls.
34. [ ] `whiletrue.recipes`: `ralph`, `review-pass`.
35. [ ] **M1 exit:** `ralph` and `review-pass` recipes run; replay reproduces a recorded run.

## Phase M2 · Interactive Claude Code _(parallel with M3)_

36. [ ] **Spike:** throwaway `--plugin-dir` plugin with hooks + MCP server in an interactive session, including `Stop` nudge loop. Record findings in `docs/`. (OQ-1)
37. [ ] `whiletrue.tmux`: create session, windows, panes, titles, grid layout (4 panes/window), `on_done: close|keep`, `whiletrue attach`.
38. [ ] Local event endpoint in the whiletrue process (receives hook posts).
39. [ ] Bundled Claude Code plugin: MCP tools `whiletrue_submit` (validated against output caps, returns schema errors), `whiletrue_learn`, `whiletrue_ask`, `whiletrue_context`.
40. [ ] Plugin hooks: `SessionStart` (bind to step), `PostToolUse` (events/cost/turns), `UserPromptSubmit` (steer), `Notification` (flag pane), `Stop` (nudge up to N then park), `SessionEnd`.
41. [ ] Interactive runner: launch `claude "<prompt>"` with flag mapping (`--session-id`, `--resume`, `--fork-session`, `--model`, tools/permissions, `--append-system-prompt-file`, `--add-dir`, `--mcp-config`, `--plugin-dir`, `--bare`).
42. [ ] Session memory options: `fresh`, `item`, named, `fork`; `session.end` and `context.reset` modules.
43. [ ] Limits without headless flags: turns & wall time from hooks; budget estimate from transcript with `PreToolUse` deny → park `budget_exceeded`. (OQ-2)
44. [ ] Steering lifts limits; cost split agent vs. steered; gates still run.
45. [ ] Validator rejects steps requesting features their runner lacks.
46. [ ] **M2 exit:** one issue completed in a live tmux pane with typed result submitted.

## Phase M3 · Parallel items & DAG _(parallel with M2)_

47. [ ] Item model + lifecycle state machine (`pending, ready, running, parked, done, failed, cancelled`).
48. [ ] Dependency graph (`depends_on`), cycle rejection at plan time, status view explains blocked dependents.
49. [ ] Item-token scheduler with free-slot accounting; `map.parallel: N`; global `max_agents`.
50. [ ] Providers: sources `json`, `markdown_tasks`; source-wins re-sync at start, resume, and before `ready`.
51. [ ] Scoped resources (workspaces, sessions, panes) released on scope exit.
52. [ ] `whiletrue.git`: `git-worktree` workspace, `git.commit`, `git.commit_push`, diff; `dir-copy` and `none` workspaces.
53. [ ] Resource locks (e.g. `repo:main`).
54. [ ] Side-effect journal with idempotency keys (skip-and-reuse / look-up-first).
55. [ ] Crash recovery: re-sync, recover `running` steps; decide re-attach vs `--resume`. (OQ-3, OQ-4)
56. [ ] CLI: `resume`, `rerun <item> --from <step>`, `cancel`, `logs [--follow]`, `cost`.
57. [ ] **M3 exit:** 4 JSON tasks run in parallel in separate worktrees; killing the process mid-run and resuming completes without duplicate effects.

## Phase M4 · GitHub, human gates, review _(needs M2 + M3)_

58. [ ] `whiletrue.github`: `github_issues` source (milestone filter, blocked-by → `depends_on`), spec-link parsing. (OQ-5)
59. [ ] `github.open_pr` (journaled), `github.sync_base` (rebase, conflict → park), `github.merge` (with lock).
60. [ ] Wake conditions: `cli`, `github.pr_review`, `github.pr_merged`, `file_exists`, `timer`, plugin checks.
61. [ ] `human.approve` with park/wake; CLI `approve`, `reject --note`, `on_idle: exit|watch`; audit trail in `parks`/`events`.
62. [ ] Comments provider interface; GitHub PR thread provider; local `whiletrue comment`.
63. [ ] `review.respond` composite: collect → classify → act → reply in thread (`[whiletrue]` prefix, journaled, never resolve human threads).
64. [ ] `gate.comments_answered`.
65. [ ] History store (`comments`, `steers`, `parks`) + `context.history` scopes + `whiletrue history <item>`.
66. [ ] `history.distill` proposing learnings with provenance; approval before injection.
67. [ ] `whiletrue.opencode` runner (read-only live pane).
68. [ ] tmux control panel (window 0): item table, state/step/park reason/cost/age, key bindings approve/reject/focus/rerun/cancel.
69. [ ] `issue-loop` and `review-respond` recipes.
70. [ ] **M4 exit:** one issue goes open → PR → human review comments answered → merged.

## Phase v0.1 · Release

71. [ ] Write the reference `whiletrue.yaml` (issue loop) and run it on a real milestone, 4 items in parallel.
72. [ ] Exercise a `rejected` → fix route and a crash/resume on that run; fix what breaks.
73. [ ] Verify every CLI command in the v0.1 surface exists and is documented: `init validate plan run resume status attach approve reject comment history rerun cancel logs cost replay`.
74. [ ] User docs: quickstart, pipeline/composite/agent authoring guides, plugin authoring guide, CLI reference.
75. [ ] Raise coverage floor to target; resolve remaining open questions; final `docs:check`.
76. [ ] Publish `whiletrue` to PyPI; tag `v0.1.0`; release notes.
