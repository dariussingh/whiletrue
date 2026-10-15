import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { Registry, type AgentRequest } from "@whiletrue-ai/core";
import { buildHeadlessArgs, createHeadlessRunner } from "./headless.js";
import { claudeCodePlugin } from "./index.js";

const dir = mkdtempSync(join(tmpdir(), "wt-cc-"));
/** A fake `claude`: dumps its argv + stdin to files, then runs the given shell body. */
function fakeClaude(name: string, body: string): string {
  const p = join(dir, name);
  writeFileSync(p, `#!/bin/sh\nprintf '%s\\n' "$@" > "${p}.args"\ncat > "${p}.stdin"\n${body}\n`);
  chmodSync(p, 0o755);
  return p;
}
const req = (over: Partial<AgentRequest> = {}): AgentRequest => ({
  prompt: "do it",
  cwd: dir,
  ...over,
});
const ctx = (signal = new AbortController().signal, events: unknown[] = []) => ({
  signal,
  onEvent: (e: Record<string, unknown>) => void events.push(e),
});
const SUCCESS =
  '{"type":"result","subtype":"success","is_error":false,"result":"done","structured_output":{"pass":true},"total_cost_usd":0.5,"num_turns":2,"session_id":"s1"}';

describe("buildHeadlessArgs", () => {
  it("builds the minimal argv", () => {
    expect(buildHeadlessArgs(req())).toEqual([
      "-p",
      "--output-format",
      "stream-json",
      "--verbose",
      "--permission-prompts",
      "none",
    ]);
  });

  it("maps every request field to a flag", () => {
    const args = buildHeadlessArgs(
      req({
        model: "opus",
        allowed_tools: ["Read", "Bash(npm test*)"],
        disallowed_tools: ["Write"],
        tools: ["Read"],
        permission_mode: "acceptEdits",
        system_prompt_append: "be brief",
        output_schema: { type: "object" },
        limits: { turns: 7, budget_usd: 2.5 },
        session_id: "abc",
      }),
      ["--bare"],
    );
    expect(args).toEqual(
      expect.arrayContaining([
        "--model",
        "opus",
        "--allowedTools",
        "Read,Bash(npm test*)",
        "--disallowedTools",
        "Write",
        "--tools",
        "Read",
        "--permission-mode",
        "acceptEdits",
        "--append-system-prompt",
        "be brief",
        "--json-schema",
        '{"type":"object"}',
        "--max-turns",
        "7",
        "--max-budget-usd",
        "2.5",
        "--session-id",
        "abc",
        "--bare",
      ]),
    );
  });

  it("prefers --resume over --session-id", () => {
    const args = buildHeadlessArgs(req({ resume: "old", session_id: "new" }));
    expect(args).toContain("--resume");
    expect(args).not.toContain("--session-id");
  });
});

describe("headless runner", () => {
  it("parses the stream, returns the result and sends the prompt on stdin", async () => {
    const cmd = fakeClaude(
      "ok.sh",
      `echo '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"},{"type":"tool_use","name":"Read"}]}}'
echo 'not json'
echo '{"type":"system"}'
echo '${SUCCESS}'`,
    );
    const events: unknown[] = [];
    const r = await createHeadlessRunner({ command: cmd }).run(req(), ctx(undefined, events));
    expect(r).toEqual({
      ok: true,
      text: "done",
      output: { pass: true },
      session_id: "s1",
      cost_usd: 0.5,
      turns: 2,
    });
    expect(events).toEqual([
      { kind: "text" },
      { kind: "tool_use", name: "Read" },
      { kind: "result", subtype: "success" },
    ]);
    const { readFileSync } = await import("node:fs");
    expect(readFileSync(`${cmd}.stdin`, "utf8")).toBe("do it");
    expect(readFileSync(`${cmd}.args`, "utf8")).toContain("stream-json");
  });

  it("works without an event callback", async () => {
    const cmd = fakeClaude("quiet.sh", `echo '${SUCCESS}'`);
    const r = await createHeadlessRunner({ command: cmd }).run(req(), {
      signal: new AbortController().signal,
    });
    expect(r.ok).toBe(true);
  });

  it("reports error results with their reason", async () => {
    const cmd = fakeClaude(
      "err.sh",
      `echo '{"type":"result","subtype":"error_max_turns","is_error":true,"errors":["Reached maximum number of turns (1)"],"num_turns":2}'`,
    );
    const r = await createHeadlessRunner({ command: cmd }).run(req(), ctx());
    expect(r).toMatchObject({ ok: false, error: "Reached maximum number of turns (1)", turns: 2 });
  });

  it("falls back to the subtype or a generic message", async () => {
    const a = fakeClaude(
      "e2.sh",
      `echo '{"type":"result","subtype":"error_during_execution","is_error":true}'`,
    );
    expect((await createHeadlessRunner({ command: a }).run(req(), ctx())).error).toBe(
      "error_during_execution",
    );
    const b = fakeClaude("e3.sh", `echo '{"type":"result","is_error":true}'`);
    expect((await createHeadlessRunner({ command: b }).run(req(), ctx())).error).toBe(
      "claude reported an error",
    );
  });

  it("fails when the process exits without a result, including stderr", async () => {
    const cmd = fakeClaude("crash.sh", "echo 'auth failed' >&2; exit 3");
    const r = await createHeadlessRunner({ command: cmd }).run(req(), ctx());
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/exited with code 3 without a result: auth failed/);
  });

  it("omits stderr detail when there is none", async () => {
    const cmd = fakeClaude("silent.sh", "exit 1");
    const r = await createHeadlessRunner({ command: cmd }).run(req(), ctx());
    expect(r.error).toMatch(/exited with code 1 without a result$/);
  });

  it("enforces the wall-time limit", async () => {
    const cmd = fakeClaude("slow.sh", "sleep 5");
    const r = await createHeadlessRunner({ command: cmd }).run(
      req({ limits: { wall_ms: 100 } }),
      ctx(),
    );
    expect(r).toMatchObject({ ok: false, error: "wall-time limit exceeded" });
  });

  it("aborts on signal", async () => {
    const cmd = fakeClaude("hang.sh", "sleep 5");
    const ctl = new AbortController();
    const p = createHeadlessRunner({ command: cmd }).run(req(), ctx(ctl.signal));
    setTimeout(() => ctl.abort(), 100);
    expect(await p).toMatchObject({ ok: false, error: "aborted" });
  });

  it("aborts before start", async () => {
    const ctl = new AbortController();
    ctl.abort();
    const r = await createHeadlessRunner({ command: join(dir, "slow.sh") }).run(
      req(),
      ctx(ctl.signal),
    );
    expect(r).toMatchObject({ ok: false, error: "aborted" });
  });

  it("reports a missing binary", async () => {
    const r = await createHeadlessRunner({ command: "definitely-no-claude-here" }).run(
      req(),
      ctx(),
    );
    expect(r.error).toMatch(/Failed to start definitely-no-claude-here/);
  });

  it("defaults to the `claude` command and declares capabilities", () => {
    const runner = createHeadlessRunner();
    expect(runner.id).toBe("claude-code-headless");
    expect(runner.capabilities).toEqual({
      interactive: false,
      structured_output: true,
      resume: true,
      fork: false,
      budget: true,
    });
  });
});

describe("claudeCodePlugin", () => {
  it("registers the headless runner", () => {
    const r = new Registry();
    r.use(claudeCodePlugin());
    expect(r.getRunner("claude-code-headless")).toBeDefined();
  });
});
