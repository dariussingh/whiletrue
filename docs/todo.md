# Implementation TODO (ordered)

> Tracking lives in this file, not GitHub issues, until whiletrue can run its own workflow (dogfooding starts at M1).

Work top to bottom. Items are numbered globally; `[ ]` = open. A milestone is done when its **Exit** line is demonstrably true. Each item: tests first, docs updated in the same change. See [plan.md](plan.md).

## Phase 0 · Repo & decisions

1. [x] Create GitHub repo `dariussingh/whiletrue`; push initial commit (docs).
2. [x] License decided: MIT (`LICENSE` added). (OQ-7)
3. [x] npm org `whiletrue-ai` created (scope `@whiletrue-ai/*`; `@whiletrue` was taken). GitHub org dropped: repo stays at `dariussingh/whiletrue`. Optional later: reserve unscoped `whiletrue-cli` as a thin wrapper around `@whiletrue-ai/cli`. (OQ-8)
4. [x] Scaffold pnpm workspace: `pnpm-workspace.yaml`, root `package.json`, `tsconfig.base.json` (strict), `packages/`.
5. [x] Tooling: ESLint, Prettier, Vitest, coverage thresholds, `.editorconfig`, `.nvmrc`.
6. [x] CI (GitHub Actions): install, lint, typecheck, test+coverage, `docs:check`.
7. [x] `docs:check` script (fails when public behavior changes without doc updates; start with link/example validation).
8. [x] `CLAUDE.md`, issue and PR templates added. GitHub milestones and issues deliberately **not** created: until whiletrue's own workflow exists, progress is tracked in `docs/todo.md` only.

## Phase M0 · Core engine

9. [ ] `@whiletrue-ai/core`: define `ModuleMeta`, `ActionModule`, `Strategy`, `Emit`, `PortSpec`, `PortValues`, effects enum.
10. [ ] Caps: zod → JSON Schema; built-in caps `Issue, Diff, PullRequest, Verdict, ReviewFindings, Text, Any`; compatibility check.
11. [ ] Outcome ports (`ok, fail, needs_human, skip, rejected`) and unwired-port fallback policy.
12. [ ] Module/strategy/runner/provider registry + public plugin API.
13. [ ] YAML config loader (`whiletrue.yaml`, `agents/*.md` frontmatter, `loops/*.yaml`) with schema errors that cite file/line.
14. [ ] Validator: resolve modules, type-check wires, enforce `loop.max`, agent `limits`, gate determinism, side-effect idempotency keys.
15. [ ] `StepContext` / `CompositeContext` (config, scoped KV, workspace, sessions, event bus, logger, cancellation); `ctx.run(child, input)` as the only child-execution path.
16. [ ] Event bus (append-only typed events).
17. [ ] `StateStore` interface.
18. [ ] `@whiletrue-ai/store-sqlite`: migrations and tables `runs, items, steps, parks, sessions, effects, learnings, events, cache`.
19. [ ] Sequential scheduler: run an item through a module tree, persist every step.
20. [ ] `@whiletrue-ai/basics`: `shell`, `exec` (JSON over stdin/stdout).
21. [ ] `@whiletrue-ai/claude-code` headless runner (`claude -p --output-format stream-json`, `--json-schema`, `--max-turns`, `--max-budget-usd`, `--permission-prompts none`); runner capability declaration.
22. [ ] `@whiletrue-ai/cli`: `init`, `validate`, `plan` (expanded tree, no tokens), `run`, `status`.
23. [ ] **M0 exit:** a bounded Ralph loop runs on the headless runner against a toy repo (integration test with a fake runner + one real smoke run).

## Phase M1 · Control flow & composites

24. [ ] Spike CEL libraries for JS (schema type-check, restricted functions, timeout/cost limit); pick one or fall back to Jexl + own cap checker. Implement the `Expr` interface (`parse`, `check`, `eval`) and sandboxed evaluator for `until:`/templates. (OQ-6, decided: CEL)
25. [ ] `@whiletrue-ai/control`: `sequence` with automatic `out → in` wiring and explicit `wires:`.
26. [ ] `loop` (`max`/`max: unbounded`, `until`, `over`, `collect`).
27. [ ] `map` (sequential first), `parallel`, `switch`.
28. [ ] `retry` (`max`, `backoff`, `feedback`) and `try`; default park-as-`needs_human` on exhausted retries.
29. [ ] Gates: `gate.shell`, `gate.tests`, `gate.diff_size`; validator enforces `deterministic`.
30. [ ] Result cache for `pure`/`deterministic` modules keyed by input hash.
31. [ ] Composites: declared ports/params, `uses:` import, nesting, param templating.
32. [ ] `whiletrue.lock` (plugin version pins) and per-run hashes of pipeline/composite/prompt files.
33. [ ] Record agent outputs; `whiletrue replay <run>` re-runs logic with no model calls.
34. [ ] `@whiletrue-ai/recipes`: `ralph`, `review-pass`.
35. [ ] **M1 exit:** `ralph` and `review-pass` recipes run; replay reproduces a recorded run.

## Phase M2 · Interactive Claude Code _(parallel with M3)_

36. [ ] **Spike:** throwaway `--plugin-dir` plugin with hooks + MCP server in an interactive session, including `Stop` nudge loop. Record findings in `docs/`. (OQ-1)
37. [ ] `@whiletrue-ai/tmux`: create session, windows, panes, titles, grid layout (4 panes/window), `on_done: close|keep`, `whiletrue attach`.
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
52. [ ] `@whiletrue-ai/git`: `git-worktree` workspace, `git.commit`, `git.commit_push`, diff; `dir-copy` and `none` workspaces.
53. [ ] Resource locks (e.g. `repo:main`).
54. [ ] Side-effect journal with idempotency keys (skip-and-reuse / look-up-first).
55. [ ] Crash recovery: re-sync, recover `running` steps; decide re-attach vs `--resume`. (OQ-3, OQ-4)
56. [ ] CLI: `resume`, `rerun <item> --from <step>`, `cancel`, `logs [--follow]`, `cost`.
57. [ ] **M3 exit:** 4 JSON tasks run in parallel in separate worktrees; killing the process mid-run and resuming completes without duplicate effects.

## Phase M4 · GitHub, human gates, review _(needs M2 + M3)_

58. [ ] `@whiletrue-ai/github`: `github_issues` source (milestone filter, blocked-by → `depends_on`), spec-link parsing. (OQ-5)
59. [ ] `github.open_pr` (journaled), `github.sync_base` (rebase, conflict → park), `github.merge` (with lock).
60. [ ] Wake conditions: `cli`, `github.pr_review`, `github.pr_merged`, `file_exists`, `timer`, plugin checks.
61. [ ] `human.approve` with park/wake; CLI `approve`, `reject --note`, `on_idle: exit|watch`; audit trail in `parks`/`events`.
62. [ ] Comments provider interface; GitHub PR thread provider; local `whiletrue comment`.
63. [ ] `review.respond` composite: collect → classify → act → reply in thread (`[whiletrue]` prefix, journaled, never resolve human threads).
64. [ ] `gate.comments_answered`.
65. [ ] History store (`comments`, `steers`, `parks`) + `context.history` scopes + `whiletrue history <item>`.
66. [ ] `history.distill` proposing learnings with provenance; approval before injection.
67. [ ] `@whiletrue-ai/opencode` runner (read-only live pane).
68. [ ] tmux control panel (window 0): item table, state/step/park reason/cost/age, key bindings approve/reject/focus/rerun/cancel.
69. [ ] `issue-loop` and `review-respond` recipes.
70. [ ] **M4 exit:** one issue goes open → PR → human review comments answered → merged.

## Phase v0.1 · Release

71. [ ] Write the reference `whiletrue.yaml` (issue loop) and run it on a real milestone, 4 items in parallel.
72. [ ] Exercise a `rejected` → fix route and a crash/resume on that run; fix what breaks.
73. [ ] Verify every CLI command in the v0.1 surface exists and is documented: `init validate plan run resume status attach approve reject comment history rerun cancel logs cost replay`.
74. [ ] User docs: quickstart, pipeline/composite/agent authoring guides, plugin authoring guide, CLI reference.
75. [ ] Raise coverage floor to target; resolve remaining open questions; final `docs:check`.
76. [ ] Publish `@whiletrue-ai/*` packages to npm; tag `v0.1.0`; release notes.
