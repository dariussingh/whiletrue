# whiletrue Architecture

> Source of truth: the "whiletrue Architecture" Claude doc (rev 18, 2026-10-15). Diagrams live there; this file is the repo copy. Keep both in sync.

## Overview

whiletrue (`@whiletrue/cli`) is a local, single-machine TypeScript orchestrator that runs configurable pipelines of agentic and deterministic steps around coding agents like Claude Code and opencode. It runs outside Claude Code and drives it as a subprocess.

Positioning: **LangGraph builds the agent; whiletrue runs the loop around agents you already have.** Fixed-pipeline tools such as ralphex, and persona tools such as ralph-orchestrator, let you tune their steps. whiletrue lets you change the shape of the loop itself.

### What sets it apart

- **Accountable human gates.** Human review is a first-class, durable step with an audit trail. Every comment you leave gets an answer from the pipeline, and all of it becomes project memory.
- **Composable loops.** Every module is a GStreamer-style element with typed ports. A loop is just a composite module, and composites nest.
- **Live, interactive agents.** Each running agent is a real Claude Code session in a tmux pane that you can type into at any time.
- **Shareable, testable loop recipes.** Pipelines and composites are files you can version, import, validate and replay.

### Non-negotiables (from the author's workflow)

1. The human keeps both high-level and low-level context of the project.
2. Code has maximal test coverage.
3. User docs and architecture docs stay current.
4. The human is accountable for every line shipped: AI writes the code, the human verifies it.

### Design principles

1. **State on disk, fresh context by choice.** Run state lives in SQLite, and how much context an agent carries is a per-step setting.
2. **Gates are deterministic.** A gate is code with an exit code. An agent can never grade itself.
3. **Bounded by default.** Every loop has a maximum, and every agent step has limits. Unbounded `while true` must be opted into explicitly.
4. **Validate before spending.** Wiring, types and config are checked before any tokens are spent.
5. **Core stays small.** Built-in modules use the same public plugin API as third-party ones.
6. **No hard dependency on git or GitHub.** Sources, sinks and workspaces are plugins.

## Core model

Everything is a **module**. A module is either an **action**, which does one thing, or a **composite**, which holds child modules and a **strategy** for running them. A loop is a composite whose strategy is `loop`, so loops nest to any depth.

> *[Diagram in the source doc: how the parts fit together · runtime, pipeline, runners, tmux]*

The runtime pulls items from sources, runs each one through the module tree, and records everything in SQLite. Agent steps go through a runner into a tmux pane, and live Claude sessions report back through the whiletrue plugin.

There are four extension points, each a plugin interface:

| Extension point | Answers | Examples |
|---|---|---|
| Action module | What does this step do? | `shell`, `gate.tests`, `gate.diff_size`, `git.commit`, `github.open_pr`, `agent`, `classify`, `human.approve`, `context.reset` |
| Strategy | How does a composite run its children? | `sequence`, `loop`, `map`, `parallel`, `switch`, `retry`, `try` |
| Runner | How is an agent invoked? | `claude-code` (interactive, in tmux), `claude-code-headless`, `opencode` |
| Provider | Where do items, state and workspaces come from? | sources (`github_issues`, `json`, `markdown_tasks`), stores (`sqlite`), workspaces (`git-worktree`, `dir-copy`, `none`), knowledge (`spec-dir`) |

### Ports and caps

Modules connect through **typed ports**, as GStreamer elements connect through pads.

- Every module has a default `in` and `out` data port, and may declare more.
- Every module has **outcome ports**: `ok`, `fail`, `needs_human`, `skip`, and optionally `rejected`. A port that isn't wired falls back to the composite's policy, for example "park on `fail`".
- **Caps** are schemas on ports: `Issue`, `Diff`, `PullRequest`, `Verdict`, `ReviewFindings`, `Text`, `Any`. They are JSON Schema, authored with zod in TypeScript.
- Inside a `sequence`, `out → in` is wired automatically. Explicit `wires:` are needed only for fan-out, routing or skipping.
- `whiletrue validate` checks that every wire connects compatible caps. At runtime, agent outputs are validated against their port's caps before they move on.

### Module interface (sketch)

```ts
interface ModuleMeta<Cfg> {
  id: string                       // 'gate.tests'
  kind: 'action' | 'composite'
  config: ZodType<Cfg>
  ports: { in: PortSpec[]; out: PortSpec[] }   // caps per port
  effects: 'pure' | 'deterministic' | 'agentic' | 'side-effect'
  capabilities?: string[]          // e.g. 'needs-workspace', 'needs-session'
}

interface ActionModule<Cfg> extends ModuleMeta<Cfg> {
  run(input: PortValues, ctx: StepContext<Cfg>): Promise<Emit>
}

interface Strategy<Cfg> {
  id: string                       // 'loop'
  config: ZodType<Cfg>             // until, max, over, parallel ...
  run(children: Node[], input: PortValues, ctx: CompositeContext<Cfg>): Promise<Emit>
}

type Emit = { port: string; value?: unknown }   // which port fires, with what
```

- `effects` drives the safety rules. Gates must be `deterministic`, `side-effect` modules must journal their work, and `pure` and `deterministic` results can be cached by input hash.
- `StepContext` provides config, scoped key-value context, the workspace, sessions, the event bus, the logger and a cancellation signal.
- A strategy runs children only through `ctx.run(child, input)`, so the runtime sees and records every execution.

### Scoped context and resources

Alongside data on ports, each composite has a **scope**. It holds key-value context inherited by its children (`item`, `repo`, `milestone`, `steps.<id>.output`) and owns **resources**: workspaces, agent sessions and tmux panes. A resource is released when its scope exits. That makes "one worktree per issue" or "one Claude session per issue" a matter of where you declare it.

## Pipeline config and authoring

There are four authoring layers, each a step deeper than the last. Most users stay in the first two.

| Layer | Format | Use it for |
|---|---|---|
| Pipelines | YAML | Composing modules into a loop you run |
| Composites | YAML with declared ports and params | Reusable, shareable sub-loops such as `review-pass` or `ralph`, imported with `uses:` |
| Agent modules | Markdown with frontmatter | A prompt-defined agent step: runner, model, tools, permissions, and input and output caps |
| Plugins | TypeScript (npm) | New actions, strategies, runners, sources, stores and workspaces |

The `exec` module is an escape hatch: it runs any executable that speaks JSON over stdin and stdout, so a module can be written in any language.

### Composite example

```yaml
# loops/review-pass.yaml
composite: review-pass
params: { runner: opencode, kinds: [architecture, code, tests] }
ports:
  in:  { pr: PullRequest }
  out: { findings: ReviewFindings }
strategy: { loop: { over: "{{params.kinds}}", collect: findings } }
do:
  - agent: { uses: ./agents/review.md, runner: "{{params.runner}}", vars: { kind: "{{loop.value}}" } }
```

### Agent module example

```markdown
---
agent: implement
runner: claude-code
model: opus
session: item            # fresh | item | <named>
tools: [Read, Edit, Write, "Bash(npm test*)", "Bash(git *)"]
permission_mode: acceptEdits
inputs:  { issue: Issue }
outputs: { result: ImplementResult }   # enforced through whiletrue_submit
context: { knowledge: "{{issue.spec_refs}}", learnings: run, history: item }
limits:  { budget_usd: 8, turns: 120, wall: 90m }
---
Implement {{issue.title}} using TDD. Write failing tests first...
```

### Predictability guardrails

- **`whiletrue plan`** parses the config, resolves every module and plugin, type-checks every wire, and prints the expanded tree. Nothing runs and no tokens are spent.
- **Lockfile.** `whiletrue.lock` pins plugin versions. Each run records hashes of the pipeline, composite and prompt files it used.
- **Effects rules.** Gates must be deterministic, and side-effect modules must provide an idempotency key.
- **Bounds.** `loop` requires `max` (or an explicit `max: unbounded`), and agent steps need `limits`.
- **Record and replay.** Agent outputs are stored, so `whiletrue replay <run>` re-runs the pipeline logic against them without calling any model.

## Runtime

The runtime is an **item-token scheduler**. Each work item, such as an issue, travels through the pipeline as its own token. A human gate parks one token without stopping the run, and other runnable items keep going.

### Item lifecycle

An item is in exactly one state at a time:

| State | Meaning |
|---|---|
| `pending` | Known, but its dependencies aren't done yet |
| `ready` | Its dependencies are done, and it's waiting for a free slot |
| `running` | A step is executing, and it holds a slot and its resources |
| `parked` | Waiting on a wake condition: a human gate, retries exhausted, or a takeover. It holds no slot |
| `done` | Exited the pipeline through `ok` |
| `failed` | Exited through `fail` with an `on_fail: drop` policy |
| `cancelled` | Removed upstream, for example an issue closed by hand, or cancelled by a user command |

### Dependency graph

- `depends_on` is a **core** concept, not a GitHub feature. Any source can supply edges: GitHub "blocked by" links, a `depends_on` field in JSON, or task references in markdown.
- The scheduler keeps the item graph and moves an item to `ready` only when every dependency is `done`. Cycles are rejected at plan time.
- Dependents of a parked or failed item stay `pending`, and the status view shows why.

### Concurrency

- `map: { parallel: N }` caps concurrent items. A global `max_agents` caps live agent sessions across the whole run.
- Resource locks, such as `repo:main` for merges, serialize steps that must not race.

### Parking and waking

- A parked step records a **wake condition**: `cli` (`whiletrue approve` or `whiletrue reject`), `github.pr_review: approved`, `github.pr_merged`, `file_exists`, `timer`, or any plugin check.
- `whiletrue resume` (or the watcher) re-evaluates wake conditions. A woken item continues from the parked step and fires that step's `ok` or `rejected` port.
- `on_idle: exit | watch` decides what happens when only parked items remain. `exit` is the default and prints a summary of what's parked and why. `watch` polls at an interval.

### Failure policy

- `retry` wraps any module: `retry: { max: 3, backoff: 30s, feedback: true }`. With `feedback`, the failure output (the test log, for example) is passed into the next attempt.
- When an item runs out of retries, the default is to **park it as `needs_human`** with the failure log attached. Its dependents stay blocked, and other items continue.
- A crash leaves the store consistent. On restart, steps that were `running` are recovered: side-effect steps are checked against the journal, and agent steps are re-attached or re-run according to their policy.

## State

A local SQLite database (`.whiletrue/state.db`) is the runtime source of truth. GitHub, git and every other system are optional integrations behind plugin interfaces.

### Two kinds of truth

- **External systems own the items.** Whether an item exists and its upstream status (open, closed, its labels, its dependencies) come from the source. They are re-synced at run start, on resume, and before an item becomes `ready`.
- **SQLite owns pipeline progress.** Which step an item is on, its outputs, its retries, its parked gates and its sessions live only in the store.
- When the two conflict, for example an issue closed by hand mid-run, the source wins. The item becomes `cancelled`, its resources are released, and an event records why.

### Store tables (v1 sketch)

| Table | Holds |
|---|---|
| `runs` | Run id, pipeline hash, lockfile, start and end, status |
| `items` | Item id, source key, state, payload snapshot, `depends_on` |
| `steps` | One row per step execution: item, node path, attempt, status, input and output, cost, timings |
| `parks` | Parked steps: wake condition, reason, notes, who approved or rejected it and when |
| `sessions` | Agent sessions: runner, session id, scope, tmux pane, status |
| `effects` | Side-effect journal: idempotency key, status, external ref (PR URL, commit SHA) |
| `learnings` | Run, pipeline and project learnings with their provenance |
| `events` | Append-only event log: every started, output, cost, error, approval and takeover |
| `cache` | Cached results of pure and deterministic modules, keyed by input hash |

The store is behind a `StateStore` interface. SQLite is the only v1 backend, and a JSON-file backend can come later for tests or very small setups.

### Human input history

Comments are first-class pipeline memory. They are human input, just like steers, approvals, git history and docs, and later steps and later items need to see them.

| Record | What is stored | Captured from |
|---|---|---|
| `comments` | Thread, author, body, classification, the agent's reply, status (open, answered, resolved), linked commit | Comments providers: GitHub PR threads, `whiletrue comment` |
| `steers` | Session, step, what you typed, when | The `UserPromptSubmit` hook in interactive panes |
| `parks` | Approvals and rejections, with notes | Human gates |
| Git and docs | Commit refs and doc paths, linked rather than copied | Workspace and knowledge providers |

- Agents read this history through `context.history`, scoped to the item (the default), the run, or the project. They can also fetch it on demand with `whiletrue_context()`.
- `whiletrue history <item>` prints one timeline that interleaves comments, replies, steers, approvals and commits.
- An optional `history.distill` step runs when an item finishes. It turns recurring feedback, such as "always add an integration test for new CLI flags", into proposed project learnings, each linked to the comments it came from. You approve them before they are injected into future runs.

### Side-effect journal

Before a side effect such as a push, opening a PR or posting a comment, the module writes an `effects` row with a deterministic idempotency key, for example `pr:<run>:<item>`. After the external call, it writes the external ref. On resume, an existing key with a ref means **skip and reuse**. An existing key without a ref means **look it up first** ("is there already a PR for this branch?"). Resuming never creates a second PR.

### Resume

`whiletrue resume [run]` re-syncs sources, recovers interrupted steps, re-evaluates wake conditions and continues. A finished step is never re-run unless you ask with `whiletrue rerun <item> --from <step>`.

## Agents and runners

By default, each Claude Code agent step runs as a **real interactive `claude` session in its own tmux pane**. The structure that headless mode would normally provide comes instead from a bundled whiletrue Claude Code plugin: an MCP server for typed results, and hooks for lifecycle events.

### The interactive runner

whiletrue starts each session as `claude "<rendered prompt>"` inside a tmux pane. The prompt goes in as an argument, so no keystrokes are ever sent. Per-step flags come from the agent module:

| Module setting | Claude Code flag |
|---|---|
| Session identity | `--session-id <uuid>`, or `--resume <id>` (plus `--fork-session`) for `session: item` |
| Model | `--model` |
| Tools and permissions | `--tools`, `--allowedTools`, `--disallowedTools`, `--permission-mode` |
| Context | `--append-system-prompt-file`, `--add-dir`, `--mcp-config` |
| whiletrue plugin | `--plugin-dir <bundled plugin>` |
| Reproducible mode (`bare: true`) | `--bare` |

Several headless-only flags (`--json-schema`, `--max-turns`, `--max-budget-usd` and `--permission-prompts`) don't apply to interactive sessions, so whiletrue provides each of these behaviors itself.

### The whiletrue plugin inside each session

- **MCP tools:**
      - `whiletrue_submit(result)` is how an agent finishes a step. The result is validated against the step's output caps. An invalid result returns the schema errors so the agent can fix it.
      - `whiletrue_learn(note)` appends to the learnings store.
      - `whiletrue_ask(question)` parks the step as `needs_human`.
      - `whiletrue_context()` returns item, spec and learnings context on demand.
- **Hooks**, which post events to the local whiletrue process:
      - `SessionStart` binds the session to its step.
      - `PostToolUse` records tool calls for the event log, cost and turn counts.
      - `UserPromptSubmit` marks the step as human-steered. It records the input as a steer event and lifts the step's limits.
      - `Notification` (permission or idle prompts) flags the pane as needing you.
      - `Stop` checks whether a result was submitted. If not, it blocks the stop and nudges the agent to call `whiletrue_submit`, up to N times, then parks the step.
      - `SessionEnd` cleans up.

### Limits without headless flags

- **Turns and wall time** are counted from hook events and enforced by whiletrue.
- **Budget** is estimated from the session transcript's usage. When it's exceeded, a `PreToolUse` hook denies further tool calls and the step parks with `budget_exceeded`. This is softer than `--max-budget-usd` and will be documented as an estimate.
- **Permission prompts** don't hang the run. They show in the pane and are flagged in the status view, so you can answer them.

**Human steering lifts the limits.** Once you type into a session, that step's budget, turn and wall-time limits are dropped for the rest of the step, because you are driving it now. Cost is still recorded, split into agent-only and steered spend. The step's gates still run before the item moves on.

### Other runners

- **`claude-code-headless`** uses `claude -p --output-format stream-json`, with `--json-schema`, `--max-turns`, `--max-budget-usd` and `--permission-prompts none`. It's for unattended runs, CI and cheap utility steps. Its pane shows a read-only live render.
- **`opencode`** is a subprocess runner for review passes.
- Every runner declares its capabilities (`interactive`, `structured_output`, `resume`, `fork`, `budget`), and `validate` rejects a step that asks for something its runner can't do.

### Sessions and memory

| Option | Behavior |
|---|---|
| `session: fresh` | A new session for this step (Ralph-style) |
| `session: item` | One session shared by every step of the same item that asks for it, such as implement and fix-after-review |
| `session: <name>` | A named session shared within its declared scope |
| `fork: true` | Branches from the shared session without changing it |
| `context.knowledge` | Injects spec files or line ranges (`specs/m3/api.md#L40-L88`) through a knowledge provider |
| `context.learnings` | Injects learnings scoped to `run`, `pipeline` or `project`. New ones come only through `whiletrue_learn`, with provenance |
| `session.end` / `context.reset` modules | Explicitly close a session or drop context mid-pipeline |

By default, a session loads the project's own CLAUDE.md, skills and hooks, plus the whiletrue plugin, with tools limited to what the module declares.

## Human gates and the TUI

Human gates park one item and never block the run. The TUI is a tmux session: one window holds the control panel, and every live agent session gets a pane you can type into.

### Gates

```yaml
- human.approve:
    what: pr                      # shown in status and the control panel
    show: "{{steps.pr.output.url}}"
    wake: { any: [cli, github.pr_review: approved] }
    on_comment: review.respond    # answer every new comment, then park again
    on_reject: rejected           # fires the rejected port with your note
```

- `whiletrue approve <item> [--note]` fires `ok`.
- `whiletrue reject <item> --note "..."` fires `rejected`, carrying your note as the payload. Wire `rejected` into a fix step, and your review comments become the next agent's input.
- An external wake condition, such as a GitHub PR approval, fires `ok` on its own. A changes-requested review can map to `rejected`, with the review comments as the payload.
- Every decision is written to `parks` and `events`: who decided, when, the note, and the item's state at that moment. That audit trail is what backs your accountability rule.

### Every comment gets an answer

A human gate is a conversation, not a silent wait. While the gate is parked, each new comment on the item wakes a **respond** step, and then the gate parks again. A comment can be a PR review comment, a reply in a thread, or `whiletrue comment <item> "..."`. The gate closes only on approval or rejection.

The same `review.respond` composite answers human comments and AI review comments, such as the opencode passes:

1. **Collect** unanswered comments from the comments provider (GitHub PR threads, or local comments from the CLI). Each one is a `Comment` cap.
2. **Classify** each comment as a question, a change request, a nit, an approval, or out of scope.
3. **Act on it.** A question gets an answer. A change request gets a fix (agent, gates, push). A nit is fixed, or declined with a reason. Anything out of scope becomes a proposed follow-up issue, which you confirm.
4. **Reply in the same thread** and say what happened: the answer, "fixed in `abc123`", or "declined because ...". Each reply is a journaled side effect, so a comment is never answered twice.
5. **Check.** `gate.comments_answered` is a deterministic gate that fails while any human or reviewer comment still has no reply.

The rules:

- The agent replies but never resolves a human's thread. Resolving is yours.
- Agent replies are marked as coming from whiletrue, for example with a `[whiletrue]` prefix, so you always know who wrote what.
- When the agent can't answer, for example a design decision or missing context, it says so in the thread and the item stays parked as `needs_human`.

### tmux layout

- Window 0 is the **control panel**: a table of items with their state, current step, park reason, cost and age, plus keys to approve, reject, focus a pane, rerun, or cancel an item.
- Each live agent session is a **pane** running real interactive Claude Code, titled `#142 implement` with the step and a cost badge. You can type into any pane at any time. whiletrue records it as a steer and drops that step's limits, but the step's gates still run before the item moves on.
- Panes are laid out in a grid. New windows open when a window is full (four panes by default), and a pane closes or stays open when its step ends, following `on_done: close | keep`.
- Headless steps such as opencode reviews get read-only panes that show the live stream.
- `whiletrue attach` reattaches to the tmux session after you detach or close the terminal. The run keeps going without a terminal attached.

### Observability outside tmux

- `whiletrue status` prints the same table as the control panel.
- `whiletrue logs <item> [--follow]` prints the event log for an item.
- `whiletrue cost [run]` breaks cost down by item, step and runner.

## Reference pipeline: the issue loop

The v0.1 acceptance test is the author's workflow, steps 8 to 11, running end to end on a real milestone with parallel items. Steps 1 to 7 (design, specs, writing issues) stay human-led for now.

```yaml
# whiletrue.yaml
name: milestone-loop
uses: ["@whiletrue/github", "@whiletrue/git", "@whiletrue/opencode"]
on_idle: exit

source:
  github_issues: { milestone: M3, state: open, depends_on: blocked_by }
comments:
  github_pr: {}                    # plus local `whiletrue comment` by default

pipeline:
  map:
    parallel: 4
    workspace: { git-worktree: { branch: "wt/{{item.number}}" } }
    do:
      - id: impl
        retry: { max: 3, feedback: true }
        do:
          - agent: { uses: ./agents/implement.md, session: item }
          - gate.shell: { run: "npm test && npm run coverage:check" }
          - gate.shell: { run: "npm run docs:check" }
          - gate.diff_size: { max_lines: 700 }
      - git.commit_push: {}
      - id: pr
        github.open_pr: { closes: "{{item.number}}" }

      - id: my_review              # non-negotiable 4: the human reads every PR
        human.approve:
          what: pr
          wake: { any: [cli, github.pr_review: approved] }
          on_comment: review.respond   # every question and comment gets a reply
          on_reject: rejected
        wires: { rejected: fix }

      - id: ai_review
        uses: ./loops/review-pass.yaml   # architecture, code and tests, via opencode
        with: { runner: opencode, post_as: comments }

      - id: fix                    # answers AI review comments and your rejection notes
        loop:
          max: 3
          until: "{{steps.fix.output.unanswered == 0}}"
          do:
            - uses: "@whiletrue/recipes/review-respond"   # classify, fix, reply per thread
              with: { session: item }
            - gate.shell: { run: "npm test" }
            - gate.comments_answered: {}

      - github.sync_base: { strategy: rebase }   # resolves conflicts or parks
      - human.approve: { what: merge, wake: { any: [cli] }, on_comment: review.respond }
      - github.merge: { lock: "repo:main" }
      - history.distill: { propose_learnings: true }
```

The pipeline exercises every core feature: the item graph, parallel worktrees, retry with feedback, deterministic gates, a side-effect journal (push and PR), a conversational human gate that answers every comment, with a `rejected` route, a nested loop, a reusable composite, a second runner, a shared session across steps, and a resource lock for merges.

## Packages and repo layout

One pnpm-workspace monorepo publishes everything under the `@whiletrue` npm scope. The core has no knowledge of git, GitHub, tmux or any specific agent.

| Package | Contents | Depends on |
|---|---|---|
| `@whiletrue/core` | Module, strategy and runner interfaces, registry, config loader, validator, scheduler, store interface, event bus | none |
| `@whiletrue/store-sqlite` | SQLite `StateStore` | core |
| `@whiletrue/control` | Strategies: `sequence`, `loop`, `map`, `parallel`, `switch`, `retry`, `try` | core |
| `@whiletrue/basics` | `shell`, `exec`, `gate.*`, `human.*`, `context.*`, `session.*`, sources `json` and `markdown_tasks` | core |
| `@whiletrue/claude-code` | Interactive and headless runners, plus the bundled Claude Code plugin (MCP server and hooks) | core |
| `@whiletrue/opencode` | opencode runner | core |
| `@whiletrue/git` | `git-worktree` workspace, commit, push, diff modules | core |
| `@whiletrue/github` | `github_issues` source, PR, review, merge and wake conditions | core, git |
| `@whiletrue/tmux` | TUI: session, panes, control panel | core |
| `@whiletrue/cli` | The `whiletrue` command; loads plugins listed in `whiletrue.yaml` | all of the above as defaults |
| `@whiletrue/recipes` | Shareable composites: `ralph`, `review-pass`, `issue-loop` | control, basics |

### CLI surface (v0.1)

`whiletrue init` · `validate` · `plan` · `run` · `resume` · `status` · `attach` · `approve` · `reject` · `comment` · `history` · `rerun` · `cancel` · `logs` · `cost` · `replay`

### Project files

- `whiletrue.yaml` is the pipeline, and `whiletrue.lock` pins plugin versions.
- `agents/*.md` and `loops/*.yaml` hold the project's agent modules and composites.
- `.whiletrue/` holds `state.db`, logs and session transcripts. It is git-ignored.

## Milestones to v0.1 and open questions

v0.1 ships when the reference pipeline takes a real milestone from open issues to merged PRs, with four items running in parallel. Each milestone ends with something runnable, and from M1 onward whiletrue is built with its own workflow.

> *[Diagram in the source doc: roadmap to v0.1 · 5 milestones, 1 release gate]*

M2 and M3 both build on M1 and don't depend on each other, so they can run in parallel. M4 needs both.

### Open questions

- Spike early in M2: confirm that a `--plugin-dir` plugin bundling hooks and an MCP server behaves as designed in interactive sessions, including the `Stop` nudge loop.
- How accurate can a budget estimate from transcript usage be, and is a soft cap acceptable for interactive steps?
- After a whiletrue crash, should we re-attach to the still-running tmux pane, or restart it with `claude --resume`?
- For `session: item` across retries, the resumed session must run in the same worktree. Confirm how resume behaves when the working directory has changed.
- Pick the convention for spec links in issue bodies (for example `spec: specs/m3/api.md#L40-L88`) so the knowledge provider can parse them.
- Choose the expression language for `until:` and templates. It needs to be safe and sandboxed, with no arbitrary JavaScript.
- Choose a license (MIT or Apache-2.0), and claim the `@whiletrue` npm org and a GitHub org.
