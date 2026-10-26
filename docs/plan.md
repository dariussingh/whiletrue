# Implementation plan

Goal: **v0.1** ships when the reference pipeline (the issue loop, see [architecture.md](architecture.md#reference-pipeline-the-issue-loop)) takes a real milestone from open issues to merged PRs, with **four items running in parallel**.

From M1 onward, whiletrue is built using its own workflow (dogfooding). Each milestone ends with something runnable.

## Status

M0 to M4 are built and v0.1.0 and v0.1.1 are published to PyPI. Next is **M5 · recursive harness improvement (`rsi`)**, then v0.2. Open work is in [todo.md](todo.md).

## Milestone graph

```
M0 Core engine
 └─ M1 Control flow & composites
     ├─ M2 Interactive Claude Code  ─┐   (M2 and M3 are independent: parallelizable)
     └─ M3 Parallel items & DAG     ─┤
                                     └─ M4 GitHub, human gates, review
                                          └─ v0.1 release gate (shipped)
                                               └─ M5 Recursive harness improvement (rsi)
                                                    └─ v0.2 release
                                                         └─ M6 Gemini CLI backend
```

| Milestone                        | Scope                                                                                                                 | Exit criterion                           |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| M0 · Core engine                 | Interfaces, YAML loader, validator, `plan`, SQLite store, event bus, sequential scheduler, headless runner            | A Ralph loop runs on the headless runner |
| M1 · Control flow & composites   | `sequence`, `loop`, `map`, `switch`, `retry`, `try`; gates; composites via `uses:`; record and replay                 | `ralph` and `review-pass` recipes run    |
| M2 · Interactive Claude Code     | tmux panes, interactive runner, bundled whiletrue plugin (MCP `whiletrue_submit` etc. + hooks)                        | One issue done in a live pane            |
| M3 · Parallel items & DAG        | Item graph, parallel `map`, git worktrees, resource locks, side-effect journal, crash recovery                        | 4 JSON tasks run in parallel             |
| M4 · GitHub, human gates, review | `github_issues` source, PR/review/merge modules, park/approve/reject, comment replies, opencode runner, control panel | One issue goes open → merged             |
| v0.1 release                     | Reference pipeline merges a real milestone, 4 items in parallel                                                       | Release gate (see below)                 |
| M5 · Recursive harness improvement | Optional `rsi` setting (off by default): Propose, Probe, Compose over the agent harness, per-domain experience library, blind selection, human-approved adoption, built on ported Harness² code (Apache-2.0) | A 6-item backlog improves its harness item by item; `rsi: false` is unchanged |
| v0.2 release                     | `rsi` documented, evaluated and released                                                                              | See M5 exit                              |
| M6 · Gemini CLI backend          | `runner: gemini` (headless stream-json), tests, docs, plus a Gemini surface for RSI                                    | Gemini runs a pipeline step; v0.3        |

## Cross-cutting rules (apply in every milestone)

1. **Tests first.** TDD; coverage threshold enforced in CI from M0 (target: maximal; start with a hard floor and ratchet up).
2. **Docs in the same PR.** Any behavior change updates user docs and `architecture.md`; a `docs:check` script gates this.
3. **Core stays small.** Built-ins use the public plugin API. `whiletrue.core` never imports git, GitHub, tmux or an agent (enforced by `tests/unit/test_architecture.py`).
4. **Bounded by default.** Loops require `max`; agent steps require `limits`. Enforced by `validate`.
5. **Validate before spending.** Wiring/caps/config errors must be caught by `validate`/`plan`, never at run time.
6. **Deterministic gates.** Gate modules must declare `effects: deterministic`; enforced by the validator.
7. **Side effects journaled** with idempotency keys; resume never duplicates.

## Workstreams by milestone

### M0 · Core engine

- Project scaffold (uv, Python 3.12, ruff, pyright strict, pytest + coverage, CI).
- `whiletrue.core`: module/strategy/runner/provider interfaces, registry, pydantic→JSON Schema caps, config loader (YAML), validator, scheduler (sequential), `StateStore` interface, event bus.
- `whiletrue.store_sqlite`: schema + migrations for `runs, items, steps, parks, sessions, effects, learnings, events, cache`.
- `whiletrue.basics` (subset): `shell`, `exec`.
- `whiletrue.claude_code` (headless only): `claude -p --output-format stream-json`.
- `whiletrue.cli` (subset): `init`, `validate`, `plan`, `run`, `status`.
- Acceptance: a Ralph loop (fresh session each iteration, bounded) runs against a toy repo.

### M1 · Control flow & composites

- `whiletrue.control`: `sequence` (auto-wiring), `loop` (`max`, `until`, `over`, `collect`), `map`, `parallel`, `switch`, `retry` (with `feedback`), `try`.
- Expression language for `until:`/templates (sandboxed, no arbitrary Python — resolve open question first).
- Gates: `gate.shell`, `gate.tests`, `gate.diff_size`.
- Composites: ports/params, `uses:` import, nesting.
- Lockfile (`whiletrue.lock`) and run hashes; record/replay (`replay` CLI).
- `whiletrue.recipes`: `ralph`, `review-pass`.

### M2 · Interactive Claude Code

- **Spike first** (see risks): `--plugin-dir` plugin with hooks + MCP in interactive sessions, incl. `Stop` nudge loop.
- `whiletrue.tmux`: session, window/pane management, grid layout, `on_done`, `attach`.
- Interactive runner: `claude "<prompt>"` in pane, flag mapping, `--session-id`/`--resume`/`--fork-session`.
- Bundled plugin: MCP tools (`whiletrue_submit/learn/ask/context`), hooks (`SessionStart, PostToolUse, UserPromptSubmit, Notification, Stop, SessionEnd`), local event endpoint.
- Limits without headless flags: turns, wall time, budget estimate (`PreToolUse` deny); steer lifts limits; cost split agent vs. steered.
- Runner capability declarations; `validate` rejects unsupported asks.

### M3 · Parallel items & DAG

- Item lifecycle state machine (`pending/ready/running/parked/done/failed/cancelled`).
- Item-token scheduler, `depends_on` graph, cycle rejection at plan time, `map.parallel`, global `max_agents`.
- Sources: `json`, `markdown_tasks`. Source-wins re-sync.
- `whiletrue.git`: `git-worktree` workspace, commit, push, diff. `dir-copy`/`none` workspaces.
- Scope resources (workspaces, sessions, panes released on scope exit); resource locks.
- Side-effect journal + idempotency; crash recovery (resume, re-attach or re-run).
- `resume`, `rerun --from`, `cancel`.

### M4 · GitHub, human gates, review

- `whiletrue.github`: `github_issues` source (blocked-by), open PR, review, sync_base, merge, wake conditions.
- Human gates: `human.approve`, park/wake, `approve`/`reject`/`comment`, `on_idle: exit|watch`, audit trail in `parks`/`events`.
- `review.respond` composite + `gate.comments_answered`; `[whiletrue]` reply prefix; never resolve human threads.
- Comment/steer/park history store, `context.history`, `history` CLI, `history.distill` with approval.
- `whiletrue.opencode` runner; tmux control panel; `logs`, `cost`.
- `issue-loop` recipe.

### M5 · Recursive harness improvement (RSI)

Goal: an optional `rsi: true | false` setting (default `false`) that lets whiletrue improve the harness around its agents as items flow through a pipeline. The method is Harness² (Xu et al., Google Research, 2026, [arXiv:2610.14224](https://arxiv.org/abs/2610.14224)); we port and refactor its method code into this repo as `whiletrue.rsi.harness2` (Apache-2.0 headers kept, provenance recorded, no extra dependency for users), so other RSI methods can later sit beside it as `whiletrue.rsi.<method>`. Design: [architecture.md](architecture.md#recursive-harness-improvement-rsi).

- **Provenance first:** third-party notices, license copy, pinned commit, citation, a provenance check in CI. No benchmark runners or datasets are ported.
- **Setting:** `rsi` at the top level and per agent, `--rsi/--no-rsi` on `run`; `validate` requires a budget, limits and an adoption policy; `plan` prints the stages and the worst-case cost multiplier (R = 4k rollouts, P = 3k improvement stages).
- **Task-level recursion:** reference rollouts of the base harness, general (INV) and domain (DOM) proposals, probes in per-candidate git worktrees, `k` compositions, blind answer-free selection, in parallel and sequential modes.
- **Harnessing-level recursion:** a per-domain experience library (adopted harness plus evidence-supported lessons), read before each item and appended after, connected to `history` and `learnings`.
- **Human accountable:** an adopted harness is a diff shown at a human gate before it enters the repo; hooks and scripts always need approval.
- **Surfaces:** Claude Code (new), opencode and Codex (ported from Harness²).
Step-by-step execution plan, with stages, dependencies, tests and decisions for you: [rsi-plan.md](rsi-plan.md).

**Exit:** with `rsi: true`, a 6-item backlog improves its agent harness item by item and a person reviews every adopted change; with `rsi: false`, behavior is unchanged (regression-tested). An evaluation against the `history.distill` baseline is published with honest numbers.

## v0.1 release gate

- Reference pipeline merges a real milestone, 4 items parallel, with at least one `rejected` → fix route and one crash/resume exercised.
- All CLI commands in the v0.1 surface exist and are documented.
- Coverage floor met; docs check green; license chosen; `whiletrue` published to PyPI.

## Risks and spikes

| Risk                                                        | Mitigation                                           | When        |
| ----------------------------------------------------------- | ---------------------------------------------------- | ----------- |
| Plugin hooks/MCP behave differently in interactive sessions | Spike with throwaway plugin before building M2       | Start of M2 |
| Soft budget estimate from transcripts inaccurate            | Document as estimate; measure error vs. real billing | M2          |
| Crash recovery of tmux panes (re-attach vs. `--resume`)     | Decide via experiment; make policy configurable      | M3          |
| `session: item` resume with changed cwd                     | Test explicitly; pin worktree per item               | M3          |
| Expression language scope creep                             | Pick small sandboxed lib early; no Python eval           | M1          |
| RSI costs about 7x an agent run per item at k=1             | Required `rsi.budget_usd`, `when:` guard, worst-case estimate in `plan` | M5          |
| Self-edited hooks and scripts are a code-execution surface  | Allow-list; always human-approved; never auto-adopted    | M5          |
| Ported code loses its attribution or license                | Pinned commit, header and provenance check in CI, notices, no benchmark code | M5          |
| RSI may not help where a gate already exists                | Opt-in; evaluate against `history.distill` before recommending | M5          |
