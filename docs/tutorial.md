# Tutorial: get started, then build your own loop

This walks from an empty folder to a pipeline with a test gate, a review pass, parallel tasks and a human approval. Every step runs `whiletrue validate` and `whiletrue plan` first, which cost nothing, so you can follow along without an agent installed until [step 3](#3-run-it). The [authoring guide](authoring.md) explains the ideas; the [reference](reference.md) lists every option.

## 1. Install and set up a toy project

```bash
uv tool install whiletrue          # or: pip install whiletrue
mkdir demo && cd demo && git init
```

Give it something to fix: a function that is wrong, and a test that says so.

```python
# calc.py
def add(a, b):
    return a - b
```

```python
# test_calc.py
from calc import add

def test_add():
    assert add(2, 3) == 5
```

```bash
git add -A && git commit -m "start"
```

You need one agent CLI on your `PATH`: `claude`, `opencode` or `codex` (set `WHILETRUE_CLAUDE_BIN`, `WHILETRUE_OPENCODE_BIN` or `WHILETRUE_CODEX_BIN` if it lives elsewhere).

## 2. Scaffold and read the loop

```bash
whiletrue init        # writes whiletrue.yaml and agents/ralph.md
whiletrue validate
whiletrue plan
```

`whiletrue.yaml` is a Ralph loop: run the agent in a fresh context, run a deterministic gate, repeat until the gate is green.

```yaml
name: ralph
pipeline:
  loop:
    max: 5
    do:
      - agent: { uses: ./agents/ralph.md }
      - gate.shell: { run: "pytest" }
```

Two rules are already at work. Every loop has a `max`, and every agent declares `limits` (open `agents/ralph.md` to see them). `validate` refuses a config that breaks either. Edit the prompt in `agents/ralph.md` to match this project:

```markdown
---
agent: ralph
runner: claude-code-headless
permission_mode: acceptEdits
tools: [Read, Edit, "Bash(pytest*)", "Bash(git *)"]
limits: { budget_usd: 1, turns: 20, wall: 10m }
---
Make the failing test pass by fixing the code, not the test.

Failure output from the previous attempt (empty on the first try):
{{iter.previous}}
```

The last line is what makes the loop learn: when the gate fails, its output becomes `{{iter.previous}}` in the next round.

## 3. Run it

```bash
whiletrue run
whiletrue status      # steps, outcomes and cost
whiletrue logs        # what happened, in order
```

The agent edits `calc.py`, the gate runs `pytest`, and the loop ends when it passes. If five rounds are not enough, the item parks as `needs_human` with the last failure attached instead of failing silently. Exit code `0` means every item finished, `2` means something is waiting on you.

## 4. Build your own loop

A loop is a few pieces you choose. Change them one at a time and run `validate` and `plan` after each change.

### Add a second gate

A gate is any deterministic check. Put them in order, cheapest first:

```yaml
name: fix-and-lint
pipeline:
  loop:
    max: 5
    do:
      - agent: { uses: ./agents/ralph.md }
      - gate.shell: { run: "ruff check ." }
      - gate.tests: {}                         # pytest by default
      - gate.diff_size: { max_lines: 80 }      # keep the change reviewable
```

The first failing gate sends its output back to the agent. The agent never grades itself.

### Retry instead of loop

When the work is one step plus a check, `retry` states it directly and parks the item when attempts run out:

```yaml
pipeline:
  retry:
    max: 3
    feedback: true
    do:
      - agent: { uses: ./agents/ralph.md }
      - gate.tests: {}
```

### Add a reviewer

A second agent, ideally on a different backend, reads the change and returns a verdict. `recipe:review-pass` runs the reviewer, sends a failing verdict to a fixer, and repeats.

```yaml
name: fix-then-review
pipeline:
  - composite: { uses: "recipe:ralph", params: { agent: ./agents/ralph.md } }
  - composite:
      uses: "recipe:review-pass"
      params: { reviewer: ./agents/reviewer.md, fixer: ./agents/ralph.md, max_rounds: 2 }
```

```markdown
---
agent: reviewer
runner: opencode                  # a second opinion from another backend
model: anthropic/claude-sonnet-5-5
permission_mode: plan
tools: [Read, Grep]
limits: { turns: 10, wall: 5m }
outputs: { out: Verdict }         # {pass, reason}, checked against the cap
---
Review the change in the working tree. Pass it only if it is correct and minimal.
```

### Write your own composite

When a shape repeats, name it. A composite is a file with `params` that expands when the pipeline loads, so `plan` shows the real steps:

```yaml
# composites/tested-change.yaml
composite: tested-change
params:
  agent: { required: true }
  gate: { default: pytest }
ports: { in: Any, out: Any }
pipeline:
  retry:
    max: 3
    feedback: true
    do:
      - agent: { uses: "{{params.agent}}" }
      - gate.shell: { run: "{{params.gate}}" }
```

```yaml
pipeline:
  composite: { uses: ./composites/tested-change.yaml, params: { agent: ./agents/ralph.md } }
```

## 5. Run many tasks at once

Replace the single implicit item with a backlog. Each task is an item that runs the whole pipeline in its own git worktree, so parallel agents never touch the same files.

```markdown
<!-- TASKS.md -->
- [ ] Add subtract {#sub}
- [ ] Add multiply {#mul}
- [ ] Add a CLI that uses both {#cli after=sub,mul}
```

```yaml
name: backlog
parallel: 3                 # items at once
max_agents: 3               # live agent sessions across all items
source: { markdown_tasks: { path: TASKS.md } }
workspace: { git-worktree: { branch: "wt/{{item.id}}" } }
pipeline:
  retry:
    max: 3
    feedback: true
    do:
      - agent: { uses: ./agents/task.md }      # use {{item.title}} in its prompt
      - gate.tests: {}
```

`after=sub,mul` makes `cli` wait for both. `whiletrue plan` prints the items and their order, and rejects dependency cycles.

## 6. Put a person in the loop

Add `human.approve` where you want to look. It parks only that item; the others keep running.

```yaml
pipeline:
  - id: work
    retry:
      max: 3
      feedback: true
      do:
        - agent: { uses: ./agents/task.md }
        - gate.tests: {}
  - id: review
    human.approve:
      what: change                  # shown in `status` and the panel
      wake: { any: [cli] }
    wires: { rejected: fix }
  - id: fix                         # runs only when you reject
    tap:
      do:
        - agent: { uses: ./agents/task.md }
        - gate.tests: {}
    wires: { ok: review }           # then ask again
```

```bash
whiletrue run                       # parks at the gate, exits with code 2
whiletrue status                    # lists what waits for you
whiletrue approve sub               # or: whiletrue reject sub --note "handle negatives"
whiletrue resume                    # finished steps replay, nothing runs twice
```

Your rejection note reaches the fix step as `{{input}}`. For GitHub pull requests, comments and merges, copy [`examples/issue-loop`](../examples/issue-loop/README.md).

## 7. Inspect, replay, recover

| Command | Use it to |
| --- | --- |
| `whiletrue status` | See every item, its step and what it waits for |
| `whiletrue logs <item>` | Read the ordered events of one item |
| `whiletrue cost` | Add up spend across runs and resumes |
| `whiletrue history <item>` | See parks, decisions, comments and steers in one timeline |
| `whiletrue replay` | Re-run the logic against a recording with no model calls |
| `whiletrue rerun <item>` / `cancel <item>` | Redo or stop one item |

If the process dies, run `whiletrue resume`. Steps that already finished are replayed from the record, and side effects such as opening a PR are never repeated.

## Where next

- Watch and steer an agent live: set `runner: claude-code` and run `whiletrue attach` ([getting started](getting-started.md#interactive-agents-a-live-tmux-pane)).
- Every module, option and expression: [reference](reference.md). Every command: [CLI](cli.md).
- Add your own module or runner: [plugins](plugins.md).
