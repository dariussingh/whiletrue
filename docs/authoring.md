# Authoring guide: pipelines, composites and agents

How to build a workflow out of whiletrue's parts. The [reference](reference.md) lists every option; this guide is about how the pieces fit.

## Think in three kinds of step

| Kind | Examples | What it is for |
| --- | --- | --- |
| **Agents** | `agent` | Judgement and creative work. Non-deterministic, costs tokens, bounded by `limits` |
| **Gates and tools** | `gate.shell`, `gate.tests`, `shell`, `git.commit`, `github.open_pr` | Facts. Deterministic checks and actions |
| **Control flow** | `loop`, `retry`, `switch`, `map`, `tap`, `human.approve` | Who runs next, how often, and when a person decides |

The rule that makes loops trustworthy: **an agent never grades itself.** A gate (a test run, a linter, a diff-size limit) decides whether the agent's work is acceptable, and `validate` refuses a `gate.*` that is not deterministic.

## A pipeline

```yaml
name: fix-the-tests
pipeline:
  loop:
    max: 5                  # every loop is bounded
    do:
      - agent: { uses: ./agents/fixer.md }
      - gate.tests: {}      # pytest; green ends the loop, red goes back to the agent
```

Each step's output feeds the next step's input. A failing gate's output becomes `{{iter.previous}}` in the next iteration, so the agent sees what went wrong. Run `whiletrue validate` and `whiletrue plan` before spending tokens: they check module names, config, port types, expressions and safety rules.

### Patterns worth knowing

- **Ralph loop**: agent, then a gate, in a `loop` (or `recipe:ralph`).
- **Retry with feedback**: `retry: { max: 3, feedback: true, do: [agent, gate] }`. Exhausted attempts park the item as `needs_human` instead of failing silently.
- **Review pass**: a read-only reviewer agent returns a `Verdict`; a failing verdict goes to a fixer and the review repeats (`recipe:review-pass`). Use a different backend for the reviewer if you want a second opinion.
- **Branching**: `switch` on an expression, or `wires` to send an outcome port to a sibling. A sibling that an earlier step wires forward to is a branch: it runs only when routed to.
- **Side branches that must not change the flow**: wrap them in `tap`, which runs its children and then passes its own input through.
- **Optional steps**: `try: { emit: skip }` turns a failure into a skip.
- **Fan-out**: `map: { over: "...", parallel: 4 }` for a list; `source:` + `parallel: N` for many items.

## Items, sources and workspaces

With no `source`, the pipeline runs once for one implicit item. With one, each item runs the whole pipeline:

```yaml
parallel: 4
source: { markdown_tasks: { path: TASKS.md } }       # or json, github_issues
workspace: { git-worktree: { branch: "wt/{{item.id}}" } }
```

Items wait for their `depends_on`. Each gets its own working directory, so parallel agents never edit the same files. Templates `{{item.id}}`, `{{item.title}}`, ... work in step config and agent prompts.

## Composites: reusable sub-pipelines

Put a repeated shape in its own file with `params`, then use it:

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
    do:
      - agent: { uses: "{{params.agent}}" }
      - gate.shell: { run: "{{params.gate}}" }
```

```yaml
- composite: { uses: ./composites/tested-change.yaml, params: { agent: ./agents/impl.md } }
```

Composites expand when the pipeline loads, so `plan` and `validate` see the real steps. They nest (cycles and depth over 10 are rejected). Built-in ones: `recipe:ralph`, `recipe:review-pass`, `recipe:review-respond`, `recipe:issue-loop`.

## Agents

An agent is a markdown file: a YAML header, then the prompt.

```markdown
---
agent: implement
runner: claude-code-headless      # or claude-code (interactive), opencode, codex
model: sonnet
permission_mode: acceptEdits
tools: [Read, Edit, Write, "Bash(git *)"]
limits: { budget_usd: 2, turns: 40, wall: 20m }    # required
outputs: { result: Verdict }                       # optional: structured, checked output
session: item                                      # optional: share a conversation across steps
---
Implement the next task. Previous failure (empty on the first try):
{{iter.previous}}
```

### Choosing a backend

| `runner` | Runs | Best for | Notes |
| --- | --- | --- | --- |
| `claude-code-headless` | `claude -p` | Unattended runs, CI, most steps | Hard turn and budget limits; structured output via `--json-schema` |
| `claude-code` | Interactive `claude` in a tmux pane | Work you may want to watch or steer | You can type into the pane; limits lift when you do; finishes via `whiletrue_submit`; asks to trust each new worktree once |
| `opencode` | `opencode run --format json` | Review passes, second opinions, any provider opencode supports | `model: provider/model`; structured output asked for in the prompt; no budget limit |
| `codex` | `codex exec --json` | OpenAI's agent as a worker or reviewer | `permission_mode` maps to Codex's sandbox; structured output via `--output-schema`; tokens, not dollars, so no budget limit |

`validate` rejects an agent asking for something its backend cannot do (a budget on a runner that cannot enforce one, a fork on one that cannot fork). Mix backends freely in one pipeline: implement with one, review with another.

### Writing good prompts for loops

- Say what "done" means in terms the gate can check.
- Put `{{iter.previous}}` in the prompt so a retry sees the failure.
- Keep each agent to one job; add steps instead of growing the prompt.
- Tell an agent that it must not edit the gate (the tests it will be judged by).

## People in the loop

Add `human.approve` where a person should look. It parks the item, answers comments through its `do:` steps, and fires `ok` or `rejected` (with the reviewer's note). See [getting-started.md](getting-started.md#people-in-the-loop) and the [reference](reference.md#human-gates).

## Reproducibility and safety

- `whiletrue lock` pins plugin versions; each run records hashes of the pipeline, composites and prompts.
- `whiletrue replay` re-runs the logic against a recording with no model calls, to confirm a refactor did not change behavior.
- Results of pure and deterministic steps are cached by input.
- Side effects that must not repeat (opening a PR, replying, merging) are journaled by an effect key, so a resume never redoes them.
- Loops need `max`, agents need `limits`, gates must be deterministic, and a failing step parks the item (`on_fail: park`) rather than carrying on.
