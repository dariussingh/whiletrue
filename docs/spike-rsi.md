# Spike: Harness² (RSI) (2026-10-26)

Read the pinned upstream commit (`1cfe254`, Apache-2.0) from a scratch clone. The upstream demo needs no credentials; here the method was traced from the source instead, module by module, to decide what to port. Status: done (todo item 79).

## The method in one task

1. **Reference rollouts:** run the base harness `k` times on the task. No answer key, no score.
2. **Propose:** an improver session reads an evidence tree (`context/`: task, full trajectories, deliverables, the current harness, an experience library) and edits `context/harness/` in place. It runs once on a general dimension (INV: how the agent works) and once on a domain dimension (DOM: what a practitioner knows). In parallel mode that is `k` of each.
3. **Probe:** each candidate harness is rolled out once on the same task.
4. **Compose:** `k` composer sessions merge candidates using the contrasts between their rollouts (parallel), or one checkpoint per round (sequential).
5. **Finals:** each composed harness is rolled out; a blind selector picks one output among anonymous finals.
6. **Reflect:** after adoption, a reflector writes what each edit did and at most one transferable lesson into a per-domain library.

Budget identity: `R = 4k` rollouts (k base, 2k candidates, k finals) and `P = 3k` improvement sessions (k INV, k DOM, k compositions). Invalid proposals get one retry, then inherit the current harness.

## What upstream contains

| Area | Size | Decision |
| --- | --- | --- |
| `core/` method modules (`tree`, `spec`, `ledger`, `library`, `runmark`, `selection`, `context`, `prompts`) | about 2,500 lines | Ported and refactored into `whiletrue.rsi.harness2` |
| `core/recursion.py`, `proposer.py` | about 1,100 lines | Not copied. The stage order is rebuilt on whiletrue's runtime (rollouts are pipeline scopes, sessions are agent calls) |
| `core/schedule.py`, `judge.py`, `report.py` | about 1,200 lines | Not ported. A benchmark sweep driver, grader and scorer |
| `substrate/` (OpenCode, Codex runners) | about 900 lines | Not ported. whiletrue's runners replace them |
| `benchmarks/`, upstream `third_party/` | about 4,000 lines | Not ported. Benchmark code and runners with their own licenses (WorkBuddy-derived code has a territorial limitation) |

Upstream has no runtime dependencies and targets Python 3.12, so porting adds nothing for users to install.

## Concept mapping

| Harness² | whiletrue |
| --- | --- |
| Task | An item |
| Domain | `rsi.domain` (default: the item's first label, else the project) |
| Task stream | Item order within a domain |
| Base harness | The project's agent files in the item's workspace (`CLAUDE.md`, `.claude/`) |
| Rollout | The strategy's child steps run in an isolated snapshot of the item's workspace |
| Executor | An existing runner |
| Improver, composer, selector, reflector | An agent call with limits through a whiletrue runner |
| `c1..c8` component tree | `SurfaceSpec` per framework |
| Deliverable | The rollout's final message and the files it changed (a diff) |

## Decisions

- **D1 Package:** `whiletrue.rsi.harness2` (decided by the owner), other methods beside it later.
- **D2 Surfaces:** Claude Code (new) and Codex (ported). OpenCode is not ported: upstream materialises a custom harness layout into OpenCode's config, and whiletrue has no such step.
- **D3 Unsafe paths:** scripts, skill scripts and (for Codex) hooks are marked `restricted`: any change needs a person's approval, whatever the adopt policy. Claude Code hooks, permission settings and MCP servers are not on the surface at all.
- **D4 Blind by default:** evidence never contains a gate result (a tripwire refuses it). A gate-aware mode is later (OQ-12).
- **D5 Applying a result:** the selected rollout's file changes are applied back to the item's workspace, so the pipeline's later steps (gates, commit, PR) run on them. The harness changes are a separate diff for the adoption gate.
- **D6 Isolation:** each rollout runs in a snapshot of the item's workspace (a detached git worktree plus uncommitted changes, or a directory copy outside git), never in the item's own directory.

## Surface for Claude Code

| Component | Path | Always on |
| --- | --- | --- |
| System prompt, memory | `CLAUDE.md` | yes |
| Guardrails | `.claude/rules/guardrails.md` | yes |
| Tool guidance | `.claude/rules/tool-guidance.md` | yes |
| Skills | `.claude/skills/<name>/SKILL.md` (+ `scripts/`) | on demand |
| Subagents | `.claude/agents/<name>.md` | own context |
| Scripts | `.claude/scripts/<name>.py` | on demand |

To verify against a real Claude Code before the live test: that `.claude/rules/*.md` is loaded as always-on context, and the frontmatter rules for skills and agents.

## Open

OQ-12 (gate-aware evidence), OQ-13 (domain), OQ-14 (adoption), OQ-15 (cost) and OQ-17 (does it help) are decided when their items are built; OQ-16 is answered by the table above for Claude Code and Codex.
