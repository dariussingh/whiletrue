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

### Interactive agents (a live tmux pane)

`runner: claude-code-headless` runs `claude -p` unattended. `runner: claude-code` runs the real interactive Claude Code in a tmux pane that you can watch and type into:

```markdown
---
agent: implement
runner: claude-code
permission_mode: acceptEdits
session: item          # share one conversation across this item's steps
limits: { budget_usd: 3, turns: 40, wall: 30m }
outputs: { result: Verdict }
---
Implement the next task, then submit your result.
```

- **Run it, then look.** `whiletrue run` creates a tmux session named `wt-<project dir>` (window 0 is a live status view, each agent gets its own pane). In another terminal run `whiletrue attach`. Detaching (`Ctrl-b d`) never stops the run.
- **How a step finishes.** The agent calls the `whiletrue_submit` tool with its result. It is checked against the step's output cap; an invalid result goes back to the agent with the errors so it can fix it. If the agent stops without submitting, whiletrue reminds it (up to 3 times) and then parks the step as `needs_human`. `whiletrue_ask(question)` parks the step on purpose, and `whiletrue_learn(note)` records a lesson in `.whiletrue/learnings.jsonl`.
- **First run in a directory.** Claude Code asks "Do you trust this folder?" in the pane. whiletrue never answers that for you; attach and answer it once (or run `claude` in the project once beforehand).
- **Steering.** Type into any agent pane. whiletrue records it as a *steer* and lifts that step's budget, turn and wall-time limits, because you are driving now. Cost is still tracked, split into agent-only and steered spend (an `agent.event` of kind `cost`). The step's gates still run before the item moves on, and a steered session is never nudged.
- **Limits.** Turns and wall time are counted from hooks; the budget is an *estimate* from the session transcript's token usage and list prices, so it is softer than headless `--max-budget-usd`. When a limit is hit, further tool calls are denied and the step parks as `needs_human` with a reason (`budget_exceeded`, `turns_exceeded`, `wall_exceeded`).
- **Permission prompts** don't hang the run: they show in the pane (its title gets a `!` prefix) for you to answer.
- `on_done: keep` leaves the pane on screen after the step; the default `close` closes it.

Sessions: `session: fresh` (default) starts a new conversation; `session: item` or `session: <name>` continues a shared one for the rest of the item; `fork: true` branches from it without changing it. End one explicitly with `- session.end: {name: item}`, or drop all with `- context.reset: {}`. Headless runs can resume but not fork.

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
| `whiletrue attach` | Attach to the project's tmux session (live agent panes) |

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
| `.whiletrue/sessions/` | Per-step files for interactive agents: prompt, system note, MCP config |
| `.whiletrue/learnings.jsonl` | Notes agents recorded with `whiletrue_learn` |

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
