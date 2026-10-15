import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  Registry,
  Scope,
  createStepContext,
  type AgentRequest,
  type AgentResult,
  type Runner,
} from "@whiletrue-ai/core";
import { agentModule } from "./agent.js";

const dir = mkdtempSync(join(tmpdir(), "wt-agent-"));
const agent = (name: string, fm: string, body = "Do {{input}} for {{item.id}} {{extra}}") => {
  writeFileSync(join(dir, `${name}.md`), `---\nagent: ${name}\n${fm}\n---\n${body}\n`);
  return `./${name}.md`;
};
const LIMITS = "limits: { budget_usd: 1, turns: 5, wall: 1m }";

function fakeRunner(
  reply: Partial<AgentResult> | ((r: AgentRequest) => AgentResult),
  caps: Partial<Runner["capabilities"]> = {},
): Runner & { requests: AgentRequest[] } {
  const requests: AgentRequest[] = [];
  return {
    id: "claude-code-headless",
    capabilities: {
      interactive: false,
      structured_output: true,
      resume: false,
      fork: false,
      budget: true,
      ...caps,
    },
    requests,
    async run(request, ctx) {
      requests.push(request);
      ctx.onEvent?.({ kind: "tool", name: "Read" });
      return typeof reply === "function" ? reply(request) : { ok: true, text: "done", ...reply };
    },
  };
}

function setup(runner: Runner, uses: string, vars: Record<string, unknown> = { extra: "!" }) {
  const registry = new Registry();
  registry.registerRunner(runner);
  const scope = new Scope();
  scope.set("item", { id: "i1" });
  const events: unknown[] = [];
  registry.registerAction(agentModule);
  const ctx = createStepContext({ uses, vars }, { cwd: dir, registry, scope });
  ctx.bus.subscribe((e) => events.push(e));
  return { registry, ctx, events };
}

describe("agent.run", () => {
  it("renders the prompt, calls the runner and returns text", async () => {
    const runner = fakeRunner({ text: "all good", cost_usd: 0.3 });
    let cost = 0;
    const { ctx, events } = setup(
      runner,
      agent("a1", `${LIMITS}\nmodel: opus\ntools: [Read]\npermission_mode: plan`),
    );
    const r = await agentModule.run({ in: "work" }, { ...ctx, recordCost: (c) => (cost += c) });
    expect(r).toEqual({ port: "ok", value: "all good" });
    expect(cost).toBe(0.3);
    const req = runner.requests[0]!;
    expect(req).toMatchObject({
      prompt: "Do work for i1 !",
      cwd: dir,
      model: "opus",
      allowed_tools: ["Read"],
      permission_mode: "plan",
      limits: { budget_usd: 1, turns: 5, wall_ms: 60_000 },
    });
    expect(req.session_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "agent.event", data: { kind: "tool", node: "node" } });
  });

  it("omits optional request fields when not configured", async () => {
    const runner = fakeRunner({});
    const { ctx } = setup(runner, agent("a2", ""));
    await agentModule.run({ in: "x" }, ctx);
    expect(runner.requests[0]).not.toHaveProperty("limits");
    expect(runner.requests[0]).not.toHaveProperty("model");
    expect(runner.requests[0]).not.toHaveProperty("output_schema");
  });

  it("requests and validates structured output", async () => {
    const runner = fakeRunner({ output: { pass: true } });
    const { ctx } = setup(runner, agent("a3", `${LIMITS}\noutputs: { result: Verdict }`));
    const r = await agentModule.run({ in: "x" }, ctx);
    expect(r).toEqual({ port: "ok", value: { pass: true } });
    expect(runner.requests[0]?.output_schema).toMatchObject({ type: "object" });
  });

  it("fails when structured output does not match its cap", async () => {
    const runner = fakeRunner({ output: { pass: "nope" } });
    const { ctx } = setup(runner, agent("a4", "outputs: { result: Verdict }"));
    const r = await agentModule.run({ in: "x" }, ctx);
    expect(r.port).toBe("fail");
    expect(r.value).toMatchObject({ error: expect.stringContaining("does not match Verdict") });
  });

  it("does not request a schema for Text outputs", async () => {
    const runner = fakeRunner({ text: "t" });
    const { ctx } = setup(runner, agent("a5", "outputs: { result: Text }"));
    expect((await agentModule.run({ in: "x" }, ctx)).port).toBe("ok");
    expect(runner.requests[0]).not.toHaveProperty("output_schema");
  });

  it("fails when the runner reports failure", async () => {
    const runner = fakeRunner({ ok: false, text: "partial", error: "budget exceeded" });
    const { ctx } = setup(runner, agent("a6", ""));
    expect(await agentModule.run({ in: "x" }, ctx)).toEqual({
      port: "fail",
      value: { error: "budget exceeded", text: "partial" },
    });
  });

  it("defaults the failure message", async () => {
    const runner = fakeRunner({ ok: false, text: "" });
    const { ctx } = setup(runner, agent("a7", ""));
    expect((await agentModule.run({ in: "x" }, ctx)).value).toMatchObject({
      error: "agent failed",
    });
  });

  it("fails on an unknown runner and honors a runner override", async () => {
    const runner = fakeRunner({});
    const { ctx } = setup(runner, agent("a8", ""));
    const withOverride = { ...ctx, config: { ...ctx.config, runner: "nope" } };
    const r = await agentModule.run({ in: "x" }, withOverride);
    expect(r.value).toMatchObject({ error: 'Unknown runner "nope"' });
  });

  it("passes a wall-time abort signal to the runner", async () => {
    let seen: AbortSignal | undefined;
    const runner: Runner = {
      ...fakeRunner({}),
      async run(_r, ctx) {
        seen = ctx.signal;
        return { ok: true, text: "" };
      },
    };
    const { ctx } = setup(runner, agent("a9", "limits: { wall: 1h }"));
    await agentModule.run({ in: "x" }, ctx);
    expect(seen?.aborted).toBe(false);
  });
});

describe("agent.validate", () => {
  const validate = (runner: Runner, uses: string) => {
    const { registry } = setup(runner, uses);
    return agentModule.validate!({ uses, vars: {} }, { cwd: dir, registry });
  };

  it("passes a well-formed agent", () => {
    expect(validate(fakeRunner({}), agent("v1", LIMITS))).toEqual([]);
  });

  it("requires limits", () => {
    expect(validate(fakeRunner({}), agent("v2", ""))[0]?.message).toMatch(/needs `limits`/);
  });

  it("reports unreadable and malformed agent files", () => {
    expect(validate(fakeRunner({}), "./missing.md")[0]?.message).toMatch(/Cannot read agent file/);
    writeFileSync(join(dir, "bad.md"), "no frontmatter");
    expect(validate(fakeRunner({}), "./bad.md")[0]?.message).toMatch(/frontmatter/);
  });

  it("reports unknown runners", () => {
    const uses = agent("v3", `${LIMITS}\nrunner: gpt`);
    expect(validate(fakeRunner({}), uses)[0]?.message).toMatch(/Unknown runner "gpt"/);
  });

  it("rejects asks the runner cannot satisfy", () => {
    const uses = agent("v4", `${LIMITS}\noutputs: { r: Verdict }\nsession: item`);
    const msgs = validate(
      fakeRunner({}, { structured_output: false, budget: false, resume: false }),
      uses,
    ).map((i) => i.message);
    expect(msgs).toEqual([
      expect.stringContaining("structured output"),
      expect.stringContaining("budget"),
      expect.stringContaining('session "item"'),
    ]);
  });
});
