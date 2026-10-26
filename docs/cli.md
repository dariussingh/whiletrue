# CLI reference

`whiletrue <command> [options]`. Commands that read the pipeline use `whiletrue.yaml` in the current directory unless `-f/--file <path>` says otherwise. Runtime state lives in `.whiletrue/` next to the pipeline file.

## Global options

| Option | Meaning |
| --- | --- |
| `-f`, `--file <path>` | Pipeline file (default `whiletrue.yaml`) |
| `-m`, `--note <text>` | `approve` / `reject`: the reason |
| `--step <id>` | `approve` / `reject`: which gate, when an item waits at several |
| `--from <step>` | `rerun`: replay recorded steps before this one |
| `--follow` | `logs`: keep printing new events until the run ends |
| `--force` | `init`: overwrite existing files |
| `-h`, `--help` | Show the command list |

## Setting up and checking

| Command | What it does |
| --- | --- |
| `whiletrue init [--force]` | Scaffold `whiletrue.yaml` and `agents/ralph.md` (a Ralph loop) |
| `whiletrue validate` | Check config, module wiring, caps, expressions, sources, wake conditions and safety rules. Spends no tokens |
| `whiletrue plan` | Print the expanded step tree; with a `source:`, also the items, their dependencies and cycles |
| `whiletrue lock` | Pin the plugin versions in use to `whiletrue.lock`. Once it exists, a version change fails every command until you lock again |

## Running

| Command | What it does |
| --- | --- |
| `whiletrue run` | Fetch the items, run the pipeline (up to `parallel` items at once). Ctrl-C cancels cleanly. Only one run may change a project at a time |
| `whiletrue resume [run]` | Continue a parked run, or recover a run whose process died. Recorded steps are replayed, decided gates let their item through, the source is re-read |
| `whiletrue rerun <item> [--from <step>]` | Run one item again as a new run; with `--from`, steps before that step are replayed |
| `whiletrue cancel <item>` | Cancel an item in the latest run. A running one stops before its next step; a parked one is closed (its gates are recorded as rejected by `cancel`) |
| `whiletrue replay [run]` | Re-run a recorded run's logic without calling any agent or tool, and report whether the outcome matches |

## People in the loop

| Command | What it does |
| --- | --- |
| `whiletrue approve <item> [--note text] [--step id]` | Approve the gate the item is parked at |
| `whiletrue reject <item> --note "..." [--step id]` | Reject it; the note goes to the gate's `rejected` route. A note is required |
| `whiletrue comment <item> "text"` | Leave a comment the pipeline must answer at its next wake |
| `whiletrue history <item>` | The item's timeline: parks, decisions (who, when, note), comments, replies, steers |
| `whiletrue learnings [list \| approve <id> \| reject <id>]` | Review the lessons `history.distill` proposed; only approved ones reach agents |

## Watching

| Command | What it does |
| --- | --- |
| `whiletrue status [run]` | The run's items, their steps, why a `pending` item is blocked, and which gates wait for a person |
| `whiletrue logs <item> [--follow]` | The item's events (steps, states, parks, workspace, agent activity) |
| `whiletrue cost [run]` | Spend by item, step and runner, summed over a run and all its resumes |
| `whiletrue attach` | Attach to the project's tmux session (`wt-<directory>`): window 0 is the control panel, one pane per live interactive agent |
| `whiletrue panel` | The control panel itself (what tmux window 0 runs): `j`/`k` move, `a` approve, `r` reject, `c` cancel, `f` focus the item's pane, `q` quit |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done |
| 1 | Failed, or a validation, configuration or usage-of-state error |
| 2 | Parked: an item needs a person (a gate is waiting, a retry or loop ran out, a rebase conflicted) |
| 64 | Usage error (unknown command or option, missing argument) |
| 130 | Cancelled (Ctrl-C or SIGTERM) |

## Environment

| Variable | Meaning |
| --- | --- |
| `WHILETRUE_CLAUDE_BIN` | The `claude` binary (default `claude`) |
| `WHILETRUE_OPENCODE_BIN` | The `opencode` binary |
| `WHILETRUE_CODEX_BIN` | The `codex` binary |
| `WHILETRUE_TMUX_BIN` | The `tmux` binary (or a wrapper that picks a socket) |
| `WHILETRUE_FAKE_GITHUB` | Path to a JSON file: use a file-backed fake GitHub instead of `gh` (dry runs, demos) |
