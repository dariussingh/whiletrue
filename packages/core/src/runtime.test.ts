import { describe, expect, it } from "vitest";
import { z } from "zod";
import { parsePipeline } from "./config.js";
import { MemoryStore } from "./memory-store.js";
import { defineAction, defineStrategy, type Emit } from "./module.js";
import { Registry } from "./registry.js";
import { hashPipeline, runPipeline } from "./runtime.js";
import { EventBus } from "./events.js";

function makeRegistry(): Registry {
  const r = new Registry();
  const cfg = z.object({
    value: z.unknown().optional(),
    port: z.string().optional(),
    cost: z.number().optional(),
    throws: z.boolean().optional(),
  });
  r.registerAction(
    defineAction({
      id: "emit",
      kind: "action",
      config: cfg,
      ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Any" }] },
      effects: "pure",
      async run(input, ctx) {
        if (ctx.config.throws) throw new Error("kaboom");
        if (ctx.config.cost) ctx.recordCost(ctx.config.cost);
        const value = ctx.config.value ?? input["in"];
        return { port: ctx.config.port ?? "ok", ...(value !== undefined && { value }) };
      },
    }),
  );
  r.registerAction(
    defineAction({
      id: "text",
      kind: "action",
      config: z.object({ value: z.unknown() }),
      ports: { in: [], out: [{ name: "out", cap: "Text" }] },
      effects: "pure",
      async run(_i, ctx) {
        return { port: "ok", value: ctx.config.value };
      },
    }),
  );
  r.registerAction(
    defineAction({
      id: "leak",
      kind: "action",
      config: z.object({}),
      ports: { in: [], out: [] },
      effects: "pure",
      async run(_i, ctx) {
        ctx.scope.addResource({ name: "res", dispose: () => void disposed.push(ctx.nodeId) });
        return { port: "ok" };
      },
    }),
  );
  r.registerStrategy(
    defineStrategy({
      id: "sequence",
      chain: true,
      config: z.object({}),
      async run(children, input, ctx) {
        let cur: Emit = { port: "ok", value: input["in"] };
        for (const child of children) {
          cur = await ctx.run(child, { in: cur.value });
          if (cur.port !== "ok") return cur;
        }
        return cur;
      },
    }),
  );
  return r;
}
const disposed: string[] = [];

const run = (yaml: string, extra: Partial<Parameters<typeof runPipeline>[0]> = {}) => {
  const store = new MemoryStore();
  const bus = new EventBus();
  return runPipeline({
    registry: makeRegistry(),
    config: parsePipeline(yaml, "w.yaml"),
    store,
    bus,
    now: () => 1000,
    ...extra,
  }).then((result) => ({ result, store }));
};

describe("runPipeline", () => {
  it("runs a sequence, chaining outputs, and persists everything", async () => {
    const { result, store } = await run(`
pipeline:
  - emit: { value: hello }
  - emit: {}
`);
    expect(result.status).toBe("done");
    expect(result.items).toEqual([{ itemId: "main", state: "done", port: "ok", value: "hello" }]);
    const steps = store.listSteps(result.runId);
    expect(steps.map((s) => [s.node_path, s.status, s.port])).toEqual([
      ["root", "completed", "ok"],
      ["root.0", "completed", "ok"],
      ["root.1", "completed", "ok"],
    ]);
    expect(store.getRun(result.runId)).toMatchObject({
      status: "done",
      pipeline_hash: expect.any(String),
    });
    expect(store.listItems(result.runId)[0]?.state).toBe("done");
    const types = store.listEvents(result.runId).map((e) => e.type);
    expect(types[0]).toBe("run.started");
    expect(types.at(-1)).toBe("run.finished");
    expect(types).toContain("step.finished");
    expect(types).toContain("item.state");
  });

  it("renders templates from item scope", async () => {
    const { result } = await run(
      `
pipeline:
  - emit: { value: "issue {{item.title}} ({{item.id}})" }
`,
      { items: [{ id: "i1", payload: { title: "T" } }] },
    );
    expect(result.items[0]?.value).toBe("issue T (i1)");
  });

  it("handles non-object item payloads", async () => {
    const { result } = await run("pipeline: [ { emit: { value: '{{item.id}}' } } ]", {
      items: [{ id: "x", payload: "scalar" }],
    });
    expect(result.items[0]?.value).toBe("x");
  });

  it("fails an item when a step fails (default policy parks it)", async () => {
    const { result, store } = await run("pipeline: [ { emit: { port: fail, value: nope } } ]");
    expect(result.items[0]).toMatchObject({ state: "parked", port: "fail" });
    expect(result.status).toBe("parked");
    expect(store.listItems(result.runId)[0]?.state).toBe("parked");
  });

  it("drops failing items when on_fail is drop", async () => {
    const { result } = await run("on_fail: drop\npipeline: [ { emit: { port: fail } } ]");
    expect(result.items[0]?.state).toBe("failed");
    expect(result.status).toBe("failed");
  });

  it("parks on needs_human", async () => {
    const { result } = await run("pipeline: [ { emit: { port: needs_human } } ]");
    expect(result.items[0]?.state).toBe("parked");
  });

  it("converts thrown errors into a failing, errored step", async () => {
    const { result, store } = await run("pipeline: [ { emit: { throws: true } } ]");
    expect(result.items[0]).toMatchObject({ port: "fail" });
    const step = store.listSteps(result.runId).find((s) => s.node_path === "root.0");
    expect(step).toMatchObject({ status: "errored", error: "kaboom" });
    expect(store.listEvents(result.runId).some((e) => e.type === "step.error")).toBe(true);
  });

  it("fails on invalid module config", async () => {
    const { result, store } = await run("pipeline: [ { emit: { cost: nope } } ]");
    expect(result.items[0]?.port).toBe("fail");
    expect(
      store.listSteps(result.runId).at(-1)?.error ?? store.listSteps(result.runId)[1]?.error,
    ).toMatch(/Invalid config/);
  });

  it("fails on unknown modules and unresolved templates", async () => {
    expect((await run("pipeline: [ { nope: {} } ]")).result.items[0]?.port).toBe("fail");
    const t = await run("pipeline: [ { emit: { value: '{{item.zzz}}' } } ]");
    expect(t.result.items[0]?.port).toBe("fail");
  });

  it("validates ok-output against the out port's cap", async () => {
    const bad = await run("pipeline: [ { text: { value: 5 } } ]");
    expect(bad.result.items[0]?.port).toBe("fail");
    const stepErr = bad.store
      .listSteps(bad.result.runId)
      .find((s) => s.node_path === "root.0")?.error;
    expect(stepErr).toMatch(/does not match cap Text/);
    const good = await run("pipeline: [ { text: { value: hi } } ]");
    expect(good.result.items[0]?.value).toBe("hi");
  });

  it("records per-step cost", async () => {
    const { result, store } = await run("pipeline: [ { emit: { cost: 0.25 } } ]");
    expect(store.listSteps(result.runId).find((s) => s.node_path === "root.0")?.cost_usd).toBe(
      0.25,
    );
  });

  it("releases scoped resources when a step's scope exits", async () => {
    disposed.length = 0;
    await run("pipeline: [ { leak: {} } ]");
    expect(disposed).toEqual(["root.0"]);
  });

  it("runs multiple items sequentially", async () => {
    const { result, store } = await run("pipeline: [ { emit: { value: '{{item.id}}' } } ]", {
      items: [{ id: "a" }, { id: "b" }],
    });
    expect(result.items.map((i) => i.value)).toEqual(["a", "b"]);
    expect(store.listItems(result.runId)).toHaveLength(2);
  });

  it("cancels cleanly when the signal is aborted", async () => {
    const ctl = new AbortController();
    ctl.abort();
    const { result } = await run("pipeline: [ { emit: {} } ]", { signal: ctl.signal });
    expect(result.status).toBe("cancelled");
    expect(result.items[0]?.state).toBe("cancelled");
  });

  it("counts attempts when a node runs again", async () => {
    const reg = makeRegistry();
    reg.registerStrategy(
      defineStrategy({
        id: "twice",
        config: z.object({}),
        async run(children, input, ctx) {
          await ctx.run(children[0]!, input);
          return ctx.run(children[0]!, input);
        },
      }),
    );
    const store = new MemoryStore();
    const res = await runPipeline({
      registry: reg,
      config: parsePipeline("pipeline: { twice: { do: [ { emit: {} } ] } }", "w.yaml"),
      store,
    });
    expect(
      store
        .listSteps(res.runId)
        .filter((s) => s.node_path === "root.0")
        .map((s) => s.attempt),
    ).toEqual([1, 2]);
  });

  it("logs when a scope fails to release", async () => {
    const reg = makeRegistry();
    reg.registerAction(
      defineAction({
        id: "badres",
        kind: "action",
        config: z.object({}),
        ports: { in: [], out: [] },
        effects: "pure",
        async run(_i, ctx) {
          ctx.scope.addResource({
            name: "x",
            dispose: () => {
              throw new Error("cannot release");
            },
          });
          return { port: "ok" };
        },
      }),
    );
    const logs: string[] = [];
    await runPipeline({
      registry: reg,
      config: parsePipeline("pipeline: [ { badres: {} } ]", "w.yaml"),
      store: new MemoryStore(),
      log: (_l, m) => void logs.push(m),
    });
    expect(logs[0]).toMatch(/Failed to release resources for root\.0: cannot release/);
  });

  it("stringifies non-Error throws", async () => {
    const reg = makeRegistry();
    reg.registerAction(
      defineAction({
        id: "throwstr",
        kind: "action",
        config: z.object({}),
        ports: { in: [], out: [] },
        effects: "pure",
        async run() {
          throw "plain string";
        },
      }),
    );
    const store = new MemoryStore();
    const res = await runPipeline({
      registry: reg,
      config: parsePipeline("pipeline: [ { throwstr: {} } ]", "w.yaml"),
      store,
    });
    expect(store.listSteps(res.runId).find((s) => s.node_path === "root.0")?.error).toBe(
      "plain string",
    );
  });
});

describe("hashPipeline", () => {
  it("is stable and sensitive to config", () => {
    const a = parsePipeline("pipeline: [ { emit: { value: 1 } } ]", "a.yaml");
    const b = parsePipeline("pipeline: [ { emit: { value: 1 } } ]", "b.yaml");
    const c = parsePipeline("pipeline: [ { emit: { value: 2 } } ]", "c.yaml");
    expect(hashPipeline(a)).toBe(hashPipeline(b));
    expect(hashPipeline(a)).not.toBe(hashPipeline(c));
  });
});
