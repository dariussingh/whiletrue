# Getting started (using whiletrue)

whiletrue runs a pipeline of steps (agents, shell commands, gates) around coding agents such as Claude Code. You describe the loop in `whiletrue.yaml`; whiletrue validates it before spending anything, runs it, and records every step so you can inspect or replay it.

## Install

```bash
uv tool install whiletrue        # once published; for now: clone the repo and `direnv allow`
whiletrue --help
```

Running agent steps needs the `claude` CLI on `PATH` (or `WHILETRUE_CLAUDE_BIN=/path/to/claude`).

## Your first loop (Ralph)

```bash
cd my-project
whiletrue init        # writes whiletrue.yaml and agents/ralph.md
whiletrue validate    # config, wiring, limits and safety rules; no tokens spent
whiletrue plan        # prints the expanded step tree; no tokens spent
whiletrue run         # runs it
whiletrue status      # steps, outcomes and cost of the latest run
```

The scaffold runs the agent in a fresh context, then a deterministic `pytest` gate, and repeats (at most 5 times) until the gate is green. The agent sees the previous failure as `{{iter.previous}}`.

## The pieces

**`whiletrue.yaml`** is the pipeline: a list of steps, or one strategy. See the [reference](reference.md) for every module and strategy.

```yaml
name: fix-tests
pipeline:
  loop:
    max: 5
    do:
      - agent: { uses: ./agents/fixer.md }
      - gate.tests: {}
```

**Agent files** (`agents/fixer.md`) are markdown with a YAML header, and every agent needs `limits`:

```markdown
---
agent: fixer
runner: claude-code-headless
model: sonnet
permission_mode: acceptEdits
tools: [Read, Edit, Write, "Bash(pytest*)"]
limits: { budget_usd: 2, turns: 40, wall: 20m }
---
Make the failing tests pass. Previous failure (empty on the first try):
{{iter.previous}}
```

An agent may declare `outputs: { out: Verdict }` to return structured output that is checked against a cap.

**Recipes** are ready-made loops: `composite: { uses: "recipe:ralph", params: { agent: ./agents/fixer.md } }`. See [reference.md](reference.md#composites-and-recipes).

## Commands

| Command | What it does |
| --- | --- |
| `whiletrue init [--force]` | Scaffold `whiletrue.yaml` and `agents/ralph.md` |
| `whiletrue validate` | Check everything that can be checked without running |
| `whiletrue plan` | Print the expanded step tree |
| `whiletrue run` | Run the pipeline (Ctrl-C cancels cleanly) |
| `whiletrue status [run]` | Show a run's steps, outcomes and cost |
| `whiletrue replay [run]` | Re-run a recorded run's logic without calling agents or tools |
| `whiletrue lock` | Pin the plugin versions in use to `whiletrue.lock` |

Use `-f/--file <path>` to point at a different pipeline file.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done |
| 1 | Failed, or validation/config error |
| 2 | Parked: an item needs a human (for example a loop ran out of iterations) |
| 64 | Usage error |
| 130 | Cancelled |

## Files whiletrue creates

| Path | What |
| --- | --- |
| `.whiletrue/state.db` | SQLite record of runs, steps, outputs, events and the result cache (git-ignore it) |
| `whiletrue.lock` | Plugin version pins (commit it) |

## Safety defaults

- Loops need `max` (or an explicit `max: unbounded`); agents need `limits`.
- Gates must be deterministic: an agent can never grade itself.
- `validate` catches unknown modules, bad config, mismatched wiring and bad expressions before any tokens are spent.
- A failing step parks the item rather than silently continuing (`on_fail: park`).

## Troubleshooting

- *"Unknown module"*: check the spelling, or that a plugin listed under `uses:` is installed.
- *"Run `whiletrue lock` to update it"*: a plugin version differs from `whiletrue.lock`; run the command to accept the change.
- *Replay says "Replay diverged"*: you changed the pipeline so a step ran that the recording never had; re-run it for real.
- *Agent steps fail to start*: `claude` is not on `PATH`; set `WHILETRUE_CLAUDE_BIN`.
