# Reference (M1 and M2)

What you can write in `whiletrue.yaml` today. The design rationale is in [architecture.md](architecture.md).

## Pipeline file

```yaml
name: my-pipeline          # defaults to the directory name
uses: [my_plugin]          # extra plugins (entry point group `whiletrue.plugins`, or an importable module with `plugin`)
on_fail: park              # park | drop | propagate (what an unwired `fail` does)
cache: true                # cache pure/deterministic results by input hash (default true)
pipeline:                  # one step, or a list (an implicit `sequence`)
  - shell: { run: "make build" }
```

A step has exactly one module key (`shell: {...}`), plus optional `id:`, `wires:` and (for strategies) `do:`.

## Built-in modules

| Module | Effect | What it does |
| --- | --- | --- |
| `shell` | side-effect | Run a shell command; `ok` on exit 0 (stdout is the value), else `fail` with the output tail |
| `exec` | side-effect | Run a program with arguments (no shell) |
| `agent` | agentic | Run a prompt file (`uses: ./agents/x.md`) on a runner; needs `limits` |
| `gate.shell` | deterministic | Pass/fail by exit code of `run` |
| `gate.tests` | deterministic | Like `gate.shell`; `run` defaults to `pytest` |
| `gate.diff_size` | deterministic | Fail when the working tree differs from `base` (default `HEAD`) by more than `max_lines` / `max_files` |
| `set` | pure | Emit a constant `value` (templates rendered) |
| `session.end` | deterministic | Close the shared agent session `name` (default `item`); the input passes through |
| `context.reset` | deterministic | Close every shared agent session of the item; the input passes through |

Gates must be deterministic; the validator rejects anything else named `gate.*`.

## Agent files

`agents/*.md`: a YAML header, then the prompt (templates: `{{input}}`, `{{item.*}}`, `{{iter.*}}`, `{{run.id}}`, and `vars` from the step).

| Header field | Meaning |
| --- | --- |
| `agent` | Name (required) |
| `runner` | `claude-code-headless` (default) or `claude-code` (interactive pane) |
| `model`, `tools`, `permission_mode` | Passed to Claude Code (`--model`, `--allowedTools`, `--permission-mode`) |
| `limits` | `budget_usd`, `turns`, `wall` (e.g. `20m`); required |
| `outputs` | `{ name: Cap }`: structured output checked against the cap |
| `session` | `fresh` (default), `item`, or a name shared within the item |
| `fork` | With a shared `session`: branch from it without changing it (needs a runner that can fork) |
| `on_done` | Interactive only: `close` (default) or `keep` the pane after the step |
| `add_dirs` | Extra directories Claude may read (`--add-dir`) |
| `append_system_prompt_file` | A file appended to the system prompt |
| `mcp_config` | Extra MCP config files (`--mcp-config`) |
| `bare` | `--bare`. On `claude-code` this turns off whiletrue's hooks (no nudges, limits or steering), so `validate` warns |

`validate` rejects an agent whose runner can't do what it asks (structured output, budget, resume, fork).

### Runners

| Runner | Capabilities | Notes |
| --- | --- | --- |
| `claude-code-headless` | structured output, resume, budget | `claude -p`; hard `--max-turns` / `--max-budget-usd` |
| `claude-code` | interactive, structured output, resume, fork, budget (estimate) | Live tmux pane; finishes via `whiletrue_submit`; see [getting-started.md](getting-started.md#interactive-agents-a-live-tmux-pane) |

When an agent parks (`needs_human`), the step's output is `{reason, detail, text}`.

## Control flow

Children go under `do:`. Each child's `out` feeds the next child's `in`.

| Strategy | Config | Behaviour |
| --- | --- | --- |
| `sequence` | | Run children in order; stop at the first `fail` / `needs_human` / `rejected` |
| `loop` | `max` (or `unbounded`), `until`, `over`, `collect`, `stop_when` | Repeat the children. Stops on first `ok` by default (Ralph). `until` is a CEL condition; `over` iterates a CEL list; `collect: true` returns every result. Needs `max` or `over` |
| `map` | `over` | Run the children once per list element, return the list; the first non-`ok` stops it |
| `parallel` | | Run children at once on the same input; return the list, or the first non-`ok` in child order |
| `switch` | `on`, `cases`, `default` | Run the one child whose id `cases` maps the value of `on` to; no match and no default fires `skip` |
| `retry` | `max` (3), `backoff`, `factor`, `feedback`, `on_exhausted` | Re-run on `fail`; exhausted attempts park the item as `needs_human` (or `fail`) |
| `try` | `emit` (`skip` \| `ok`) | Turn a `fail` into `skip` (or `ok`) so optional steps never stop the item |

Inside `loop`, `map` and `retry` the scope `iter` holds `iter.index` (1-based), `iter.previous` (the previous failed attempt's error/output) and `iter.value` (the current `over` element).

### Wires

`wires: { fail: fix }` sends that outcome port to a sibling step (carrying the value) instead of stopping, and the chain continues from the sibling. A chain gives up after 1000 routes, so a fixer wired back to its checker cannot spin forever.

## Expressions (CEL)

`until`, `over`, `on` are [CEL](https://cel.dev) expressions (the `common-expression-language` package), checked by `validate` for unknown variables and functions. Variables: `run`, `item` (the item's payload fields plus `id`), `iter`, `input`, and in `until` also `last` (`last.ok`, `last.port`, `last.value`). `loop` is a CEL reserved word, hence `iter`. Extension functions such as `max`, `split`, `lowerAscii` are available. No I/O is possible.

Templates (`{{iter.previous}}`, `{{item.title}}`) are separate: plain dotted-path substitution in strings, strict about missing variables.

## Composites and recipes

A composite is a reusable sub-pipeline in its own file:

```yaml
composite: ralph
params:
  agent: { required: true }
  max: { default: 5 }
ports: { in: Any, out: Any }     # optional; checked against neighbours by `validate`
pipeline:
  loop:
    max: "{{params.max}}"        # a lone token keeps the param's type (here an int)
    do: [ { agent: { uses: "{{params.agent}}" } } ]
```

Use it with `- composite: { uses: ./composites/ralph.yaml, params: { agent: ./agents/a.md } }`. Composites nest (cycles and depth over 10 are rejected) and expand at load time, so `plan`, `validate` and hashes see the real steps; step ids become `<step id>.<inner id>`.

Built-in recipes use `uses: "recipe:<name>"`:

- `recipe:ralph` — params `agent` (required), `gate` (default `pytest`), `max` (default 5).
- `recipe:review-pass` — params `reviewer` (agent whose `outputs: { out: Verdict }`), `fixer`, `max_rounds` (default 3). Reviews, runs the fixer on a failed verdict, repeats; exhausted rounds park the item.

## Reproducibility

- **`whiletrue lock`** writes `whiletrue.lock`, pinning the version of each plugin in use. Once it exists, `validate`/`plan`/`run` fail if the plugins drift; run `whiletrue lock` again to accept.
- Every run records its pipeline hash, the SHA-256 of the pipeline file, expanded composites and prompt files (a `run.hashes` event), and a hash of `whiletrue.lock`.
- **Result cache:** `pure` and `deterministic` modules that don't read the workspace are cached by (module, config, input) across runs. Gates and `shell` are never cached.
- **`whiletrue replay [run]`** re-runs a recorded run (default: latest) with no agent or tool calls: recorded results are served for every module except `pure` ones, so loops, expressions and wiring run for real. It reports whether the outcome matches the recording and notes when the pipeline changed since. A step with no recording fails with "Replay diverged".
