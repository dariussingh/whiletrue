# Open questions

| #   | Question                                                                                                                                                                        | Decide by   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| 1   | ~~Do `--plugin-dir` hooks and an MCP server behave as designed in interactive sessions?~~ **Resolved (2026-10-23): yes**, including the `Stop` nudge loop. See [spike-interactive.md](spike-interactive.md). | Done        |
| 2   | How accurate is a budget estimate from transcript usage; is a soft cap acceptable for interactive steps? **Deferred past v0.1:** the estimate is built and documented as an estimate; comparing it with real billing needs a longer measurement. Headless Claude Code, opencode and Codex are unaffected (Codex and opencode have no budget). | After v0.1   |
| 3   | ~~After a crash: re-attach to the pane, or restart?~~ **Resolved (M3): restart.** `resume` re-runs the interrupted step (its pane is gone) and replays everything that finished; side effects are protected by the journal. See architecture.md. | Done        |
| 4   | ~~`session: item` resume with a changed working directory~~ **Moot for now:** a recovered step starts a fresh session (the item's session ids live in memory); worktrees are reused at the same path, so a normal resume keeps its cwd. | Done        |
| 5   | ~~Convention for spec links in issue bodies~~ **Resolved (M4):** a line `spec: path[#Lstart-Lend]` (also `specs:`, several links per line). Parsed into the item's `spec_refs`. See `whiletrue/github/specs.py`. | Done        |
| 6   | ~~Expression language for `until:` and templates~~ **Resolved: CEL** behind an `Expr` interface (`parse`, `check(schema)`, `eval(scope)`), pending a half-day spike. See below. | M1          |
| 7   | ~~License: MIT or Apache-2.0~~ **Resolved: MIT**                                                                                                                                | Done        |
| 8   | ~~Claim npm org / GitHub org~~ **Superseded by OQ-9:** no npm packages. The PyPI name `whiletrue` is free (checked 2026-10-16); CLI binary stays `whiletrue`. No GitHub org needed. | Done        |
| 9   | ~~Implementation language~~ **Resolved: Python 3.12+** (2026-10-16), not TypeScript. Nothing in the design needs Node: agents are driven as subprocesses. One `whiletrue` distribution; pydantic, stdlib `sqlite3`, asyncio, pytest, ruff, pyright. | Done        |
| 11  | ~~Vendor, depend on, or re-implement Harness²?~~ **Resolved (2026-10-26, owner): port and refactor** the method code into this repo as `whiletrue.rsi.harness2` so users install nothing extra and other RSI methods can sit beside it (`whiletrue.rsi.<method>`). Apache-2.0 headers kept, modifications noted, license copy in `third_party/harness2/`. Not ported: benchmark runners, WorkBuddy-derived code (territorial license), datasets. | Done |
| 12  | Harness² is deliberately answer-free (no ground truth, no grader verdicts in evidence). whiletrue has deterministic gates. Do gate results enter the evidence and selection? Default **blind** (faithful to the paper); opt-in `rsi.evidence: gates` and `selection: gate-first`. | M5 (items 84, 86) |
| 13  | What are "task", "domain" and the "task stream" for coding work? Proposal: task = item, domain = `rsi.domain` expression (default the source's label or milestone, else the project), stream = item order, library persists across items and runs. | M5 (item 88) |
| 14  | Where do harness edits live, and who approves them? Candidates run in per-candidate worktrees. Adopting a harness into the repo (`CLAUDE.md`, `.claude/`, `agents/`) is a human decision: `rsi.adopt: approve` (default) shows the diff at a `human.approve` gate; `auto` and `never` exist. Edits to hooks and scripts always need approval. | M5 (item 90) |
| 15  | Cost. One RSI step at k=1 is 4 rollouts and 3 improvement sessions, about 7x one agent run. How do we keep it affordable: required `rsi.budget_usd`, a `when:` guard (for example only after a gate failure, or every Nth item), worst-case estimate in `plan`? | M5 (items 81, 87) |
| 16  | The editable surface for Claude Code: which of the eight components map to which files, and which are too dangerous to edit unattended (hooks, scripts, permission rules, MCP). Opencode and Codex surfaces come from Harness²'s `SurfaceSpec`s. | M5 spike (items 79, 82) |
| 17  | Does `rsi` measurably help on coding backlogs where a gate already exists? Needs an honest evaluation against the existing rule-based `history.distill` baseline before it is recommended. | M5 (item 94) |
| 18  | Gemini CLI as a backend. Its docs show `-p`, `-o stream-json`, `--approval-mode`, exit codes `0/1/42/53` and `GEMINI.md`, but no budget, turn or structured-output flag, and the layout of skills, extensions and agents is not specified. The README also says unpaid-tier and Google One users moved to Antigravity CLI on 2026-06-18, so access depends on the account type. Verify against the real binary first; treat budget and turn limits as unsupported (like Codex). | M6 spike (item 98) |

## Decision: expression language (OQ-6)

**Choice:** CEL (Common Expression Language), wrapped behind an `Expr` interface in `whiletrue.core` so the engine can be swapped.

**Why:** not Turing-complete, no I/O or side effects (safe without a custom sandbox); statically typed, so expressions are checked against port caps at `validate`/`plan` time ("validate before spending"); pure and deterministic, so safe for replay and cache hashing; familiar syntax.

**Rejected:** JSONata (more than needed, weaker static checks); Jexl / expr-eval (fine fallback, no static typing); Python `eval` / `exec` (not a real sandbox).

**Risk:** Python CEL libraries (`cel-python`, pure Python; `common-expression-language`, Rust-backed) are less proven than the Go/Java ones. A spike must confirm: type-checking against a schema, a restricted function set, and a timeout/cost limit. If none pass, fall back to a small whiletrue-owned expression subset plus its own cap checker.

### Spike result (2026-10-22, todo 24)

**Picked `common-expression-language` (Rust-backed, 0.10).** `whiletrue.core.expr` wraps it as `parse_expr` → `Expr` (`check`, `eval`, `eval_bool`).

| | `common-expression-language` | `cel-python` |
|---|---|---|
| Install | wheels, no native deps | needs `google-re2` (native); fails to import on NixOS without `libstdc++` on `LD_LIBRARY_PATH` |
| Speed | 1000 nested iterations in ~0.5 ms | ~120 ms |
| Static analysis | `compile().references()` lists variables and functions | none |
| Schema type-check | no | no |
| Timeout / cost limit | none (native call, not interruptible) | none |
| Reserved word `loop` | rejected | accepted |

What `Expr.check` therefore does: unknown-variable and function-allow-list checks, not full cap type-checking. Cap-level checking of expressions is deferred to the validator, using port caps for the variables. Mitigation for the missing limits: a 4096-character cap on the source, and CEL being non-Turing-complete.

**Consequence:** `loop` is a reserved CEL word, so the loop scope is named `iter` everywhere (templates and expressions): `iter.index` (1-based), `iter.previous`, and `iter.value` inside `over`/`map`. Decided OQ-10 (2026-10-23): one name, `iter`.
