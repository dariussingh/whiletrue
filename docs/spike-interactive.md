# Spike: plugin hooks and MCP in interactive Claude Code (OQ-1)

Run on 2026-10-23 with Claude Code 2.1.293 inside a detached tmux session. A throwaway plugin
(`--plugin-dir`) bundled six hooks plus a stdio MCP server exposing `whiletrue_submit`. Each run
launched `claude "<prompt>" --plugin-dir ...` in a pane and read the hook log.

**Verdict: the design works as written.** Everything below was observed, not assumed.

## What works

| Question | Result |
| --- | --- |
| Do plugin hooks fire in an interactive session? | Yes: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd` all fired. The hook gets one JSON object on stdin. |
| Does a plugin-bundled MCP server start, and can the agent call its tool? | Yes. The tool is deferred, so the agent first runs `ToolSearch`; that shows up as an ordinary `PreToolUse`/`PostToolUse` pair. |
| Do hooks and the MCP server inherit the pane's environment? | Yes. `WT_STEP`, set on the tmux session, reached both. whiletrue can bind a hook to a step through an env var. |
| Can the MCP server reject a bad result so the agent retries? | Yes. Returning `isError: true` with a message made the agent fix its call and resubmit. |
| Does the `Stop` nudge loop work? | Yes. Printing `{"decision":"block","reason":"..."}` made the agent continue and call the tool. |
| Does typed input reach a hook? | Yes. `UserPromptSubmit` carries the `prompt`, so a steer is distinguishable from the initial prompt (the first one). |
| Is per-message usage available? | Yes. `transcript_path` points at a JSONL file; assistant lines carry `message.id`, `message.model` and `usage` (input, output, cache read, cache creation tokens). |

## Details that shape the implementation

- **Hook payload.** Common fields: `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
  `permission_mode`. `PreToolUse`/`PostToolUse` add `tool_name`, `tool_input`, `tool_use_id`
  (`PostToolUse` adds `tool_response`, `duration_ms`). `Stop` adds `last_assistant_message`
  and `stop_hook_active`. MCP tool names are `mcp__<server>__<tool>`; a plugin's server is
  `mcp__plugin_<plugin>_<server>__<tool>`, so whiletrue passes its server with `--mcp-config` to
  keep the name stable (`mcp__whiletrue__whiletrue_submit`).
- **`stop_hook_active` stays true** on every stop after the first nudge, so it cannot count
  nudges. whiletrue counts them itself.
- **Duplicate transcript lines.** One assistant message is written once per content block, with the
  same `message.id`. Turn and token counts must dedupe by id.
- **Trust dialog.** A directory Claude Code has not seen shows "Do you trust this folder?" and
  blocks the prompt. The runner must handle it (answer it, or pre-trust the project).
- **`--allowedTools` is variadic.** It swallows a trailing prompt argument. Pass the prompt first,
  or use `--allowedTools=<rule>`. Wildcards are only allowed after a literal `mcp__<server>__`.
- **Read-only shell commands** (such as `uname -s`) ran without a permission prompt, so the
  `Notification` hook was not exercised; it is wired up but only unit tested.
- **`SessionEnd`** fires when the pane's process exits or is killed.

## Open after the spike

- The `Notification` hook payload (permission and idle prompts) was not observed live.
- OQ-2 (budget estimate accuracy) is unchanged: usage is available, price tables are not.
