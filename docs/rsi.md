# Recursive self-improvement (`rsi`)

`rsi` is an optional mode, off by default, in which whiletrue improves the harness around your agent while a backlog runs. The harness is the set of files an agent framework loads: always-on instructions (`CLAUDE.md`), skills, subagents and scripts. The method is Harness² (Xu et al., Google Research, 2026, [arXiv:2610.14224](https://arxiv.org/abs/2610.14224)); see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the citation and license. The code is `whiletrue.rsi.harness2`; other methods can be added beside it.

With `rsi` off nothing changes: no plugin is loaded and no file is written.

## Turn it on

```yaml
rsi: true                  # or false (the default)
pipeline:
  - agent: { uses: ./agents/implement.md }
  - gate.tests: {}
  - github.open_pr: {}
```

`whiletrue plan` shows what that expands to. The steps up to the last agent, with no side effects, are wrapped in `rsi.harness2`; a review gate, an adoption step and a discard branch follow. Gates and side effects after the agent stay where they are and run on the chosen result.

```yaml
rsi:
  k: 1                     # recursion budget; see "What it costs"
  mode: parallel           # parallel | sequential
  budget_usd: 8            # hard cap per item, rollouts and sessions together
  adopt: approve           # approve (default) | auto | never
  domain: "{{item.milestone}}"   # what "the same kind of work" means; default: the project name
  scope: [implement]       # step ids to wrap; default: the leading safe steps around the first agent
  surface: claude-code     # claude-code | codex: whose files are the harness
  improver:                # how the improvement sessions run
    runner: claude-code-headless
    model: sonnet
    limits: { budget_usd: 1.5, turns: 40, wall: 20m }
  selector: { ... }        # optional; defaults to the improver
  reflector: { ... }       # optional; defaults to the improver
  concurrency: 2           # rollouts at once inside one item
  when: "item.number > 1"  # CEL; when false the steps run once, plainly
```

`rsi: true` uses a default budget of $10 per item; `validate` warns that it is a default. You can also use the strategy directly: `rsi.harness2: { ... }` with `do:` steps (list `whiletrue.rsi` under `uses:` if you do).

## What it does for one item

1. **Reference rollouts.** The wrapped steps run `k` times on a copy of the item's workspace with the harness as it is.
2. **Propose.** An improver session reads the evidence (the task, full trajectories, what each rollout produced, the current harness, earlier lessons from this domain) and edits a copy of the harness. One session works on how the agent works (INV), one on what a practitioner of this domain knows (DOM).
3. **Probe.** Each edited harness is rolled out on the same item.
4. **Compose.** Composer sessions merge the candidates using what the rollouts show.
5. **Finals and selection.** Each composed harness is rolled out, then a selector, which sees anonymous change sets and no scores, picks one.
6. **Apply.** The chosen rollout's file changes are written into the item's workspace, so your gates, commit and PR run on them.
7. **Reflect.** A reflector writes what each edit did and at most one lesson that should transfer.

Parallel mode makes `k` general and `k` domain candidates from the same evidence, then `k` independent compositions. Sequential mode runs `k` rounds, each editing the previous checkpoint.

The evidence is blind: it never contains a gate result, and a tripwire refuses to build it if one slips in. The selector never sees a score.

## What it costs

One step at `k` is **R = 4k rollouts and P = 3k improvement sessions**. At `k: 1` that is 4 runs of your agent steps and 3 improvement sessions, about 7 times one plain run. `validate` says so, and `budget_usd` caps it: when the cap is reached the item parks as `needs_human` with the reason `rsi_budget_exceeded`. Use `when:` to run `rsi` only on some items.

Finished rollouts and sessions are stored in `.whiletrue/rsi/items/<item>/`, so `whiletrue resume` and re-runs reuse them instead of paying again.

## Adopting the result

The chosen harness is a *proposal*. What happens next is `adopt`:

| `adopt` | What happens |
| --- | --- |
| `approve` | The item parks at a `human.approve` gate (`what: harness`) when the harness changed. `whiletrue approve <item>` and `resume` write it into the item's workspace (so it is in the item's commit and PR) and store the lesson. `reject` leaves the harness alone; the work is kept. |
| `auto` | Adopted without asking, unless it changes files that execute code, which park the item. |
| `never` | Recorded only. Adopt it later by hand. |

Files that execute code (scripts, hooks) are never adopted without a person: `whiletrue rsi adopt <item> --yes` after reading the diff.

```bash
whiletrue rsi status            # every item's proposal, chosen candidate and spend
whiletrue rsi diff <item>       # the harness change
whiletrue rsi library           # earlier lessons, by domain
whiletrue rsi adopt <item>      # write a proposed harness into this project
whiletrue rsi reset <item>      # forget an item's rollouts and proposal
```

## Where the files go

| Path | Holds |
| --- | --- |
| `.whiletrue/rsi/items/<item>/rollouts/<label>/` | Each rollout's copy of the workspace, trajectory, result and file changes |
| `.whiletrue/rsi/items/<item>/stages/<label>/` | Each session's evidence tree (`context/`), mission and result |
| `.whiletrue/rsi/items/<item>/proposal.json` | The proposal: base and chosen harness, diff, lesson, ledger, status |
| `.whiletrue/rsi/library/<domain>/` | Adopted harnesses and lessons that later items in the domain read |

## What the harness is

| Framework | Files |
| --- | --- |
| Claude Code | `CLAUDE.md`, `.claude/rules/guardrails.md`, `.claude/rules/tool-guidance.md`, `.claude/skills/<name>/SKILL.md`, `.claude/agents/<name>.md`, `.claude/scripts/<name>.py` |
| Codex | `AGENTS.md`, `.agents/skills/<name>/SKILL.md`, `scripts/<name>.py`, `.codex/hooks.json` |

Claude Code hooks, permission settings and MCP servers are not on the surface: `rsi` cannot change them. A harness is validated before any rollout (skills need a trigger line and matching frontmatter, scripts must exist and compile, the always-on text has a size cap). A proposal that fails gets one retry with the problems listed, then keeps the current harness.

## A first pilot (2026-10-26)

A small run with real Claude Code, to see the machinery work and what it costs. It is not evidence that `rsi` helps.

Setup: three items that each add a feature module to a tiny repo. A test enforces a convention the task never mentions (every module must be listed in `features.ALL`). Each item had to pass its own tests plus a registry test. One run without `rsi`, one with `rsi: { k: 1, budget_usd: 1.8, adopt: auto }`, items in sequence.

| | Without `rsi` | With `rsi` |
| --- | --- | --- |
| All three items passed | yes | yes |
| Cost | $0.46 | $4.06 (about 9 times) |
| Agent runs per item (item 1, 2, 3) | 2, 1, 1 | 1, 2, 1 (in the base rollouts) |
| Harness changed | n/a | items 2 and 3: a "Adding a feature" section with the registry rule |

What it shows:

- The whole cycle works end to end on a real agent, including resume-safe stages, the budget cap and adoption.
- On item 1 the candidate harnesses did worse than the base: their rollouts hit sandbox permission denials and gave up, so selection fell back to the base output, as designed.
- The adopted harness wrote down a true repository fact. But the baseline already solved every item cheaply (after item 1 the convention is visible in the repo), so there was nothing for the harness to improve. The extra spend bought no measurable gain here.

Treat `rsi` as an experiment: try it on work where the agent repeatedly trips over something it could be told, and compare cost and retries with it off.

## Limits today

- Rollouts are copies of the workspace without `.git` (set `copy_git: true` to include it), so a step that needs git history should say so.
- Full trajectories need a runner that gives raw messages; today that is `claude-code-headless`. Other runners produce the final answer only.
- Gate results are not used as evidence or for selection (`evidence: gates` and gate-first selection are not built).
- OpenCode and Gemini CLI surfaces are not built. Items in one domain that run at the same time may not see each other's lessons.
- `rsi` is new. Whether it helps on coding backlogs where a gate already exists has not been measured yet.
