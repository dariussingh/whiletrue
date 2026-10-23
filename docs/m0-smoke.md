# M0 smoke test (real Claude Code)

The automated M0 exit test (`tests/integration/test_cli.py`) drives a Ralph loop with a fake `claude`.
This is the manual counterpart against the real binary. It costs a few tenths of a cent.

```bash
W=$PWD                      # the whiletrue checkout
S=$(mktemp -d); mkdir -p $S/agents; cd $S
cat > whiletrue.yaml <<'YAML'
name: smoke
pipeline:
  loop:
    max: 3
    do:
      - agent: { uses: ./agents/smoke.md }
      - gate.shell: { run: "grep -qx hello done.txt || (echo 'done.txt must contain exactly: hello'; exit 1)" }
YAML
cat > agents/smoke.md <<'MD'
---
agent: smoke
runner: claude-code-headless
model: haiku
permission_mode: acceptEdits
tools: [Write]
limits: { budget_usd: 0.5, turns: 6, wall: 3m }
---
Create a file named done.txt in the current directory containing exactly the word hello. Previous failure output, if any: {{iter.previous}}
MD
WT="$W/.devenv/state/venv/bin/whiletrue"
$WT validate
$WT plan
$WT run
$WT status
```

**Result on 2026-10-15:** `run: done`, one loop iteration, 3 steps, cost $0.0028; `done.txt` contained `hello`.

Notes learned while building the runner (real `claude` 2026-10 behavior):

- `claude -p --output-format stream-json --verbose` ends with a `{"type":"result"}` line carrying `subtype`, `is_error`, `result`, `structured_output`, `total_cost_usd`, `num_turns`, `session_id`.
- With `--json-schema`, the structured answer arrives in `structured_output`, and it takes **two turns** (so `--max-turns 1` fails with `error_max_turns`). Budget `limits.turns` accordingly.
- The prompt is sent on stdin so tool lists (variadic flags) can't swallow it.
