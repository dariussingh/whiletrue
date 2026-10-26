# Getting started (using whiletrue)

Prefer a step-by-step walkthrough that ends with your own pipeline? See the [tutorial](tutorial.md).

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

## Backends: Claude Code, opencode, Codex

Pick per agent with `runner:`; mix them in one pipeline. `claude-code-headless` (default) and `claude-code` (a live tmux pane you can type into) run Claude Code; `opencode` runs `opencode run`; `codex` runs `codex exec`. Each needs its own CLI installed and logged in; set `WHILETRUE_CLAUDE_BIN`, `WHILETRUE_OPENCODE_BIN` or `WHILETRUE_CODEX_BIN` if it is not on `PATH`. Structured `outputs:` work on all of them (checked against the cap). Budgets are enforced by the two Claude Code runners only. See [authoring.md](authoring.md#choosing-a-backend).

## People in the loop

A **human gate** parks an item until a person decides, without stopping other work:

```yaml
- id: review
  human.approve:
    what: pr                                   # shown in `status` and the panel
    wake: { any: [cli, { github.pr_review: any }] }   # besides `whiletrue approve|reject`
  do:                                          # runs whenever new comments arrive
    - composite: { uses: "recipe:review-respond", params: { responder: ./agents/respond.md } }
  wires: { rejected: fix }
```

- **Park and resume.** With the default `on_idle: exit` the run stops (exit code 2) and `whiletrue status` lists what is waiting. Decide with `whiletrue approve <item>` or `whiletrue reject <item> --note "what to change"`, then `whiletrue resume`: finished steps are replayed from the record (never redone) and the item continues past the gate. With `on_idle: watch` the run stays up and picks decisions up by itself.
- **Approve and reject.** Approval lets the item through. A rejection fires the gate's `rejected` port with your note under `rejection`; wire it to a fix step. A step that is wired to from an earlier step is a *branch*: it runs only when routed to, so put the fix step after the gate and end it with `wires: { ok: review }` to ask again. Use `tap:` instead of `sequence:` for a fix branch so the item's data (the PR) flows on unchanged.
- **Every comment gets an answer.** A comment (`whiletrue comment <item> "..."`, or a GitHub review comment) wakes the gate's `do:` steps. `recipe:review-respond` has your responder agent classify each comment, make changes, return one reply per comment (`outputs: { replies: CommentReplies }`), runs your tests, posts each reply in its own thread prefixed `[whiletrue]` (once only), and fails while any comment is unanswered. Agents never resolve a human's thread.
- **Wake conditions.** `cli` always works. Others: `file_exists: path`, `timer: 10m`, `github.pr_review: approved | changes_requested | any`, `github.pr_merged`, or one your plugin registers. `any` fires on the first, `all` needs every one.
- **Audit trail.** `whiletrue history <item>` prints one timeline of parks, decisions (who, when, note), comments, replies and steers. `context.history` feeds the same history to an agent; `history.distill` proposes lessons from rejections and steers, and nothing reaches an agent until you approve it with `whiletrue learnings approve <id>`.
- **The control panel.** In tmux, window 0 is `whiletrue panel`: every item with its state, step, what it waits for, cost and age; `j/k` move, `a` approve, `r` reject (asks for a note), `c` cancel, `f` jumps to the item's pane.

## Many items at once

`source:` makes one item per task; `parallel: N` runs up to N at once and `max_agents: M` caps live agent sessions across all of them. An item waits for the items in its `depends_on` (a failed or parked blocker keeps its dependents `pending`; `status` says what blocks them). Dependency cycles are rejected by `plan` and `run`.

```yaml
parallel: 4
max_agents: 4
source: { markdown_tasks: { path: TASKS.md } }   # or json: { path }, or github_issues
workspace: { git-worktree: { branch: "wt/{{item.id}}" } }   # each item gets its own checkout
pipeline: [ ... ]
```

- **Sources.** `markdown_tasks` turns unchecked `- [ ] Title {#id after=a,b}` lines into items (checked lines are done). On `resume` the source wins: tasks removed or ticked off are dropped, new ones are added.
- **Workspaces.** `git-worktree` (a worktree and branch per item under `.whiletrue/worktrees/`; kept while the item is parked, removed when it is done, the branch stays), `dir-copy` (a copy of the project) or `none` (share the project). Steps run inside the item's workspace.
- **Git steps.** `git.commit` (stages and commits if anything changed), `git.commit_push` (also pushes), `git.diff` (changed files, optionally the patch).
- **A watching gate keeps its slot.** With `on_idle: watch` an item waiting at a gate still counts against `parallel`; set it higher than the number of items you expect to wait, or use `on_idle: exit` and `resume`.

## When a run dies

Kill the process (or lose the machine) mid-run and the next `whiletrue resume` notices the run was left `running` with nobody holding `.whiletrue/run.lock`, marks what was in flight as interrupted, replays every step that finished (nothing runs twice) and runs the interrupted steps again. An agent step restarts from scratch (its pane is gone). `whiletrue rerun <item> [--from <step>]` runs one item again as a new run, replaying its steps before `--from`; `whiletrue cancel <item>` stops an item (a running one stops before its next step; a parked one is closed and recorded as cancelled). `whiletrue logs <item> [--follow]` prints its events and `whiletrue cost [run]` breaks spend down by item, step and runner. Only one run changes a project at a time.

## GitHub issues, one by one

`source: { github_issues: { milestone: M3 } }` makes each open issue an item (id = issue number, blocked-by relations become `depends_on`, `spec: path#L1-L9` lines become `item.spec_refs`). The `gh` CLI must be installed and logged in. `recipe:issue-loop` runs one issue from open to merged:

```yaml
source: { github_issues: { repo: owner/name, milestone: M3 } }
pipeline:
  - id: issue
    composite:
      uses: "recipe:issue-loop"
      params: { implementer: ./agents/implement.md, fixer: ./agents/fix.md, responder: ./agents/respond.md }
```

It branches `wt/<n>`, implements with retries behind your `gate`, opens (or reuses) the PR, parks at a review gate that answers comments and routes rejections to the fixer, rebases onto `main` (a conflict parks the item), asks for a final approval, and merges under the lock `repo:main`. Opening the PR, replying to a comment and merging are recorded in an effect journal, so a resume never repeats them. Set `parallel` to work several issues at once, each in its own worktree.

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
| `whiletrue resume [run]` | Continue a parked run, or recover one whose process died: recorded steps are replayed, decided gates let their item through, the source is re-read |
| `whiletrue rerun <item> [--from step]` | Run one item again as a new run |
| `whiletrue cancel <item>` | Cancel an item |
| `whiletrue logs <item> [--follow]` | The item's event log |
| `whiletrue cost [run]` | Spend by item, step and runner |
| `whiletrue approve <item> [--note] [--step id]` | Approve the gate the item is parked at |
| `whiletrue reject <item> --note "..."` | Reject it; the note goes to the gate's `rejected` route |
| `whiletrue comment <item> "text"` | Leave a comment the pipeline must answer |
| `whiletrue history <item>` | The item's timeline of parks, decisions, comments, replies and steers |
| `whiletrue learnings [approve\|reject <id>]` | Review lessons proposed by `history.distill` |
| `whiletrue panel` | The control panel (what tmux window 0 runs) |

Use `-f/--file <path>` to point at a different pipeline file.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done |
| 1 | Failed, or validation/config error |
| 2 | Parked: an item needs a human (a gate is waiting, or a loop ran out of iterations) |
| 64 | Usage error |
| 130 | Cancelled |

## Files whiletrue creates

| Path | What |
| --- | --- |
| `.whiletrue/state.db` | SQLite record of runs, steps, outputs, events and the result cache (git-ignore it) |
| `whiletrue.lock` | Plugin version pins (commit it) |
| `.whiletrue/sessions/` | Per-step files for interactive agents: prompt, system note, MCP config |
| `.whiletrue/learnings.jsonl` | Notes agents recorded with `whiletrue_learn` |
| `.whiletrue/state.db` also holds | parks and decisions, comments and replies, steers, the effect journal and proposed learnings |

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
