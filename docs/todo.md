# Implementation TODO (ordered)

> Tracking lives in this file, not GitHub issues, until whiletrue can run its own workflow (dogfooding starts at M1).

Work top to bottom. Items are numbered globally; `[ ]` = open, `[~]` = partly done. A milestone is done when its **Exit** line is demonstrably true. Each item: tests first, docs updated in the same change. See [plan.md](plan.md).

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
34. [x] `whiletrue.recipes`: `ralph`, `review-pass`.
35. [x] **M1 exit:** `ralph` and `review-pass` recipes run; replay reproduces a recorded run.

## Phase M2 · Interactive Claude Code

36. [x] **Spike:** throwaway `--plugin-dir` plugin with hooks + MCP server in an interactive session, including `Stop` nudge loop. Record findings in `docs/`. (OQ-1)
37. [x] `whiletrue.tmux`: create session, windows, panes, titles, grid layout (4 panes/window), `on_done: close|keep`, `whiletrue attach`.
38. [x] Local event endpoint in the whiletrue process (receives hook posts).
39. [x] Bundled Claude Code plugin: MCP tools `whiletrue_submit` (validated against output caps, returns schema errors), `whiletrue_learn`, `whiletrue_ask`, `whiletrue_context`.
40. [x] Plugin hooks: `SessionStart` (bind to step), `PostToolUse` (events/cost/turns), `UserPromptSubmit` (steer), `Notification` (flag pane), `Stop` (nudge up to N then park), `SessionEnd`.
41. [x] Interactive runner: launch `claude "<prompt>"` with flag mapping (`--session-id`, `--resume`, `--fork-session`, `--model`, tools/permissions, `--append-system-prompt-file`, `--add-dir`, `--mcp-config`, `--plugin-dir`, `--bare`).
42. [x] Session memory options: `fresh`, `item`, named, `fork`; `session.end` and `context.reset` modules.
43. [x] Limits without headless flags: turns & wall time from hooks; budget estimate from transcript with `PreToolUse` deny → park `budget_exceeded`. (OQ-2)
44. [x] Steering lifts limits; cost split agent vs. steered; gates still run.
45. [x] Validator rejects steps requesting features their runner lacks.
46. [x] **M2 exit:** one issue completed in a live tmux pane with typed result submitted.

## Phase M3 · Parallel items & DAG

47. [x] Item model + lifecycle state machine (`pending, ready, running, parked, done, failed, cancelled`).
48. [x] Dependency graph (`depends_on`), cycle rejection at plan time, status view explains blocked dependents.
49. [x] Item-token scheduler with free-slot accounting; `map.parallel: N`; global `max_agents`.
50. [x] Providers: sources `json`, `markdown_tasks`; source-wins re-sync at start, resume, and before `ready`.
51. [x] Scoped resources (workspaces, sessions, panes) released on scope exit.
52. [x] `whiletrue.git`: `git-worktree` workspace, `git.commit`, `git.commit_push`, diff; `dir-copy` and `none` workspaces.
53. [x] Resource locks (e.g. `repo:main`).
54. [x] Side-effect journal with idempotency keys (skip-and-reuse / look-up-first).
55. [x] Crash recovery: re-sync, recover `running` steps; decide re-attach vs `--resume`. (OQ-3, OQ-4)
56. [x] CLI: `resume`, `rerun <item> --from <step>`, `cancel`, `logs [--follow]`, `cost`.
57. [x] **M3 exit:** 4 JSON tasks run in parallel in separate worktrees; killing the process mid-run and resuming completes without duplicate effects.

## Phase M4 · GitHub, human gates, review


58. [x] `whiletrue.github`: `github_issues` source (milestone filter, blocked-by → `depends_on`), spec-link parsing. (OQ-5)
59. [x] `github.open_pr` (journaled), `github.sync_base` (rebase, conflict → park), `github.merge` (with lock).
60. [x] Wake conditions: `cli`, `github.pr_review`, `github.pr_merged`, `file_exists`, `timer`, plugin checks.
61. [x] `human.approve` with park/wake; CLI `approve`, `reject --note`, `on_idle: exit|watch`; audit trail in `parks`/`events`.
62. [x] Comments provider interface; GitHub PR thread provider; local `whiletrue comment`.
63. [x] `review.respond` composite: collect → classify → act → reply in thread (`[whiletrue]` prefix, journaled, never resolve human threads).
64. [x] `gate.comments_answered`.
65. [x] History store (`comments`, `steers`, `parks`) + `context.history` scopes + `whiletrue history <item>`.
66. [x] `history.distill` proposing learnings with provenance; approval before injection.
67. [x] `whiletrue.opencode` runner (read-only live pane).
68. [x] tmux control panel (window 0): item table, state/step/park reason/cost/age, key bindings approve/reject/focus/rerun/cancel.
69. [x] `issue-loop` and `review-respond` recipes.
70. [x] **M4 exit:** one issue goes open → PR → human review comments answered → merged.

## Phase v0.1 · Release

71. [~] Write the reference `whiletrue.yaml` (issue loop) and run it on a real milestone, 4 items in parallel. _(reference pipeline is in `examples/issue-loop/`; run end to end with 4 issues, one dependency, real Claude Code agents, a local `origin` and a file-backed fake GitHub. Not yet run against a real GitHub milestone: that creates real PRs and needs your repo.)_
72. [x] Exercise a `rejected` → fix route and a crash/resume on that run; fix what breaks.
73. [x] Verify every CLI command in the v0.1 surface exists and is documented: `init validate plan run resume status attach approve reject comment history rerun cancel logs cost replay`.
74. [x] User docs: quickstart, pipeline/composite/agent authoring guides, plugin authoring guide, CLI reference.
75. [x] Raise coverage floor to target; resolve remaining open questions; final `docs:check`. _(OQ-2, budget estimate accuracy, is deferred past v0.1 and listed as a known limit.)_
76. [x] Publish `whiletrue` to PyPI; tag `v0.1.0`; release notes. _(Published: `v0.1.0`, then `v0.1.1` with the tmux parallel-start fix and the new README and tutorial. Releases go through `.github/workflows/release.yml` on a `v*` tag with PyPI trusted publishing.)_

## Phase M5 · Recursive harness improvement (RSI)

Source: Harness² ([arXiv:2610.14224](https://arxiv.org/abs/2610.14224), [code](https://github.com/google-research/harness2), Apache-2.0, pinned `1cfe254`). The method code is ported and refactored into this repo as `whiletrue.rsi.harness2` (no extra dependency for users; other RSI methods can sit beside it as `whiletrue.rsi.<method>`). Design: [architecture.md](architecture.md#recursive-harness-improvement-rsi). Open questions OQ-11 to OQ-17. Execution plan: [rsi-plan.md](rsi-plan.md). Gemini CLI support follows in M6.

_Provenance and design_

77. [x] Third-party provenance and citation: `THIRD_PARTY_NOTICES.md`, `third_party/harness2/{LICENSE,PROVENANCE.md}`, README citation, acknowledgements and the license note ("third-party code under `third_party/`, benchmarks and datasets retain their upstream licenses").
78. [x] Provenance check `scripts/check_provenance.py` (run by `poe docs-check` and CI): every ported file under `whiletrue/rsi/harness2/` carries the upstream Apache header plus a modification notice and is listed in `PROVENANCE.md` with its upstream path.
79. [x] Spike notes `docs/spike-rsi.md`: run the upstream demo from a scratch clone, trace one task end to end, record the concept mapping and the decisions (OQ-13, OQ-16).
80. [x] Port and refactor the method modules into `whiletrue/rsi/harness2/` (component tree and surfaces, ledger, library, run marker, selection, evidence context, prompts), typed to pyright strict, no global state, our logging and error types, Apache headers kept. Benchmark runners, judge, report, sweep scheduler and datasets are not ported.

_Setting and surface_

81. [x] `rsi` configuration: top level and per agent (`rsi: true | false | { method: harness2, k, mode, domain, adopt, evidence, budget_usd, scope, when }`), default `false`; `--rsi/--no-rsi` on `run`. `validate` rules: needs a budget, limits on improver and selector agents, `workspace: git-worktree`, an adoption policy, and no side-effect modules inside the scope; `rsi: false` leaves every existing config valid and unchanged. `plan` prints the RSI stages and the worst-case cost multiplier. _(Top level and the `rsi.harness2` node; a per-agent `rsi:` header is not built.)_
82. [x] Editable surface (`SurfaceSpec`) for Claude Code mapping the components (system prompt, guardrails, memory, tool guidance, subagents, skills, scripts) to files in the worktree; Codex surface ported from Harness². _(Both are in `surfaces.py` with tree read, validate, write and diff. OpenCode is not ported: upstream materialises a custom layout that whiletrue has no step for. The "always-approve" flag is `SurfaceSpec.restricted`.)_ Read, validate, write and diff a tree; hooks, scripts, permission rules and MCP flagged as always-approve. (OQ-16) _(Claude Code and Codex surfaces in `surfaces.py`; OpenCode is not ported.)_

_Task-level recursion_

83. [x] Rollout adapter: run a pipeline scope under a candidate harness in an isolated git worktree; extract trajectory and deliverable from the run record; reuse a finished rollout through the completion marker and harness fingerprint. _(Rollouts are workspace copies run through the scope's redirected `cwd`; finished ones are reused via `runmark`.)_
84. [x] Improver, composer and selector as ordinary whiletrue agent steps (limits, any backend) and the evidence-tree builder; answer-free tripwires (no gate verdicts unless `rsi.evidence: gates`). (OQ-12) _(Blind evidence with the verdict tripwire; `evidence: gates` is not built.)_
85. [x] `rsi.harness2` strategy: k reference rollouts, general and domain proposals, probes, compositions, final rollouts. Parallel mode uses the scheduler's semaphores; sequential mode carries harness and evidence forward. Journaled and resumable. _(Parallel and sequential, journaled on disk and resumable.)_
86. [x] Blind selection: anonymized finals, selector failure falls back to the base output, optional `selection: gate-first`. _(Blind selection with a base fallback; `selection: gate-first` is not built.)_
87. [x] Budget ledger: R = 4k and P = 3k checked at run time, spend per stage in `cost`, hard `rsi.budget_usd` that parks the item with a reason; `when:` guard. (OQ-15) _(Identity checked and logged, hard `budget_usd`, `when:` guard. `cost` shows spend by step, not yet split by rsi stage.)_

_Harnessing-level recursion and humans_

88. [x] Per-domain experience library under `.whiletrue/rsi/` (adopted harness plus lessons), read before each item and appended after; domain from `rsi.domain`. (OQ-13) _(Per-domain library under `.whiletrue/rsi/library`; same-domain items that run at once may not see each other.)_
89. [~] Experience reflector as an agent step proposing evidence-supported lessons into `history` and `learnings` (approval before any lesson reaches an agent). _(The reflector feeds the experience library, which improvers read; it does not write to `history`/`learnings`.)_
90. [x] Adoption: `rsi.adopt: approve | auto | never`; the adopted harness is a diff at a `human.approve` gate (`what: harness`), committed on approval, discarded on `rejected`. Hooks and scripts always need approval. (OQ-14) _(`approve | auto | never`; executable paths always need a person.)_

_CLI, tests, evaluation, docs_

91. [~] CLI `whiletrue rsi status | diff | library | adopt | reset`; control panel shows the RSI stage; `logs` and `cost` split by stage. _(`rsi status|diff|library|adopt|reset` exist; the panel stage and the `logs`/`cost` split by stage do not.)_
92. [~] Tests: deterministic fake improver and executor; the upstream demo scenario as an integration test; regression test that `rsi: false` produces identical runs; coverage floor held. _(Fake-runner unit tests, a fake headless `claude` integration suite and the `rsi: false` regression exist; the upstream demo scenario was not ported.)_
93. [x] Opt-in live test (`WHILETRUE_LIVE=1`): real Claude Code, k=1, one small item; record cost and wall time. _(`tests/live/test_live_rsi.py`. Run on 2026-10-26 with real Claude Code: one tiny bug-fix item, k=1, 4 rollouts and 3 sessions in about 90 seconds for $0.91, bug fixed, the improver kept the base harness (sensible for that task). It shows the machinery works, not that the harness improves.)_
94. [~] Evaluation on a small coding backlog: gate pass rate, retries and cost with `rsi` on versus off, and against the `history.distill` baseline. Publish honest numbers, including a negative result. (OQ-17) _(A first pilot is in `docs/rsi.md`: 3 items, with and without `rsi`; about 9 times the cost, same pass rate, no measurable gain because the baseline already solved every item. A proper comparison against `history.distill` on a harder backlog is still open.)_
95. [x] Docs: `docs/rsi.md` user guide, tutorial section, reference, architecture, README feature, CHANGELOG; `docs:check` green. _(`docs/rsi.md`, reference, CLI, architecture, README and CHANGELOG. A tutorial section is not written.)_
96. [ ] **M5 exit:** with `rsi: true`, a 6-item backlog improves its agent harness item by item and a person reviews every adopted change; with `rsi: false`, behavior is unchanged.
97. [ ] Release `v0.2.0` (version bump, CHANGELOG, tag, PyPI). _(Owner step: pushing a tag publishes.)_

## Phase M6 · Gemini CLI backend

Follows M5. Independent of RSI except that a Gemini surface is added to it at the end. Open question OQ-18.

98. [ ] Spike `docs/spike-gemini.md`: install and log in to `gemini`, then verify against the real binary what the docs promise: `-p`, `-o stream-json` event and `result`/`stats` field names, `--approval-mode` values, exit codes `0/1/42/53`, `--skip-trust`, `-r` resume, where `GEMINI.md` and `.gemini/settings.json` are read, how skills, extensions, custom commands and `/agents` are laid out on disk.
99. [ ] `whiletrue.gemini` runner: `gemini -p ... -o stream-json`, `permission_mode` to `--approval-mode`, model aliases, exit `53` to a `turns_exceeded` park, exit `42` to a clear failure, token usage from `result.stats`, structured `outputs:` through the prompt, `-r` resume, live pane through `StreamMirror`. No budget or turn flag exists, so `validate` rejects `budget_usd` and `turns` on this runner (as for Codex). `WHILETRUE_GEMINI_BIN`.
100. [ ] Gemini tests and docs: a fake `gemini` replaying the recorded stream, unit and integration tests, an opt-in live test, backend tables updated (README, authoring, getting-started, CLI env vars, CHANGELOG).
101. [ ] Gemini surface for RSI (`GEMINI.md`, skills, extensions, custom commands, agents, settings and MCP) and a Gemini executor in the RSI live test.
102. [ ] Release `v0.3.0`.
