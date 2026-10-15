import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  MemoryStore,
  Registry,
  defineAction,
  parsePipeline,
  runPipeline,
  validatePipeline,
  type Emit,
} from "@whiletrue-ai/core";
import { controlPlugin, loopStrategy, sequenceStrategy } from "../src/index.js";

/** `step` replays a scripted list of emits and records each call's input and loop scope. */
function setup() {
  const calls: { input: unknown; loop: unknown }[] = [];
  const scripts = new Map<string, Emit[]>();
  const registry = new Registry();
  registry.use(controlPlugin);
  registry.registerAction(
    defineAction({
      id: "step",
      kind: "action",
      config: z.object({ name: z.string() }),
      ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Any" }] },
      effects: "pure",
      async run(input, ctx) {
        calls.push({ input: input["in"], loop: ctx.scope.get("loop") });
        const script = scripts.get(ctx.config.name) ?? [];
        return script.shift() ?? { port: "ok", value: `${ctx.config.name}-default` };
      },
    }),
  );
  const run = (yaml: string) => {
    const config = parsePipeline(yaml, "w.yaml");
    const store = new MemoryStore();
    return runPipeline({ registry, config, store }).then((result) => ({ result, store }));
  };
  return { registry, scripts, calls, run };
}

describe("sequence", () => {
  it("chains values and stops at the first non-continuing port", async () => {
    const t = setup();
    t.scripts.set("a", [{ port: "ok", value: "A" }]);
    t.scripts.set("b", [{ port: "fail", value: { error: "b broke" } }]);
    const { result } = await t.run(`
pipeline:
  - step: { name: a }
  - step: { name: b }
  - step: { name: c }
`);
    expect(result.items[0]).toMatchObject({ port: "fail", value: { error: "b broke" } });
    expect(t.calls.map((c) => c.input)).toEqual([undefined, "A"]);
  });

  it("skip passes the previous value through", async () => {
    const t = setup();
    t.scripts.set("a", [{ port: "ok", value: "A" }]);
    t.scripts.set("b", [{ port: "skip" }]);
    await t.run(`
pipeline:
  - step: { name: a }
  - step: { name: b }
  - step: { name: c }
`);
    expect(t.calls.map((c) => c.input)).toEqual([undefined, "A", "A"]);
  });

  it("returns the last value, and an ok with nothing for an empty sequence", async () => {
    const t = setup();
    expect((await t.run("pipeline: [ { step: { name: z } } ]")).result.items[0]?.value).toBe(
      "z-default",
    );
    expect((await t.run("pipeline: { sequence: {} }")).result.items[0]).toMatchObject({
      state: "done",
      port: "ok",
    });
  });

  it("stops when cancelled", async () => {
    const t = setup();
    const ctl = new AbortController();
    t.scripts.set("a", []);
    const config = parsePipeline(
      "pipeline: [ { step: { name: a } }, { step: { name: b } } ]",
      "w.yaml",
    );
    ctl.abort();
    const res = await runPipeline({
      registry: t.registry,
      config,
      store: new MemoryStore(),
      signal: ctl.signal,
    });
    expect(res.status).toBe("cancelled");
  });

  it("is registered as a chaining strategy", () => {
    expect(sequenceStrategy.chain).toBe(true);
  });
});

describe("loop", () => {
  it("stops at the first ok iteration (Ralph)", async () => {
    const t = setup();
    t.scripts.set("work", [
      { port: "fail", value: { error: "tests failed", output: "2 failing" } },
      { port: "fail", value: "plain text failure" },
      { port: "ok", value: "green" },
    ]);
    const { result } = await t.run(`
pipeline:
  loop:
    max: 5
    do:
      - step: { name: work }
`);
    expect(result.items[0]).toMatchObject({ state: "done", value: "green" });
    expect(t.calls.map((c) => c.loop)).toEqual([
      { index: 1, previous: "" },
      { index: 2, previous: "tests failed\n2 failing" },
      { index: 3, previous: "plain text failure" },
    ]);
  });

  it("fails when max iterations are exhausted", async () => {
    const t = setup();
    t.scripts.set("work", [
      { port: "fail", value: "x" },
      { port: "fail", value: "y" },
    ]);
    const { result } = await t.run(
      "pipeline: { loop: { max: 2, do: [ { step: { name: work } } ] } }",
    );
    expect(result.items[0]).toMatchObject({
      port: "fail",
      value: { error: "loop gave up after 2 iterations", last: "y" },
    });
    expect(t.calls).toHaveLength(2);
  });

  it("stop_when never runs every iteration", async () => {
    const t = setup();
    const { result } = await t.run(
      "pipeline: { loop: { max: 3, stop_when: never, do: [ { step: { name: w } } ] } }",
    );
    expect(t.calls).toHaveLength(3);
    expect(result.items[0]?.state).toBe("done");
  });

  it("propagates needs_human and rejected immediately", async () => {
    const t = setup();
    t.scripts.set("w", [{ port: "needs_human" }]);
    const a = await t.run("pipeline: { loop: { max: 5, do: [ { step: { name: w } } ] } }");
    expect(a.result.items[0]).toMatchObject({ state: "parked", port: "needs_human" });
    expect(t.calls).toHaveLength(1);
    t.scripts.set("w", [{ port: "rejected" }]);
    const b = await t.run("pipeline: { loop: { max: 5, do: [ { step: { name: w } } ] } }");
    expect(b.result.items[0]?.port).toBe("rejected");
  });

  it("supports an explicit unbounded loop", async () => {
    const t = setup();
    t.scripts.set("w", [
      { port: "fail", value: "x" },
      { port: "ok", value: "fin" },
    ]);
    const { result } = await t.run(
      "pipeline: { loop: { max: unbounded, do: [ { step: { name: w } } ] } }",
    );
    expect(result.items[0]?.value).toBe("fin");
  });

  it("stops looping when cancelled", async () => {
    const t = setup();
    const ctl = new AbortController();
    const config = parsePipeline(
      "pipeline: { loop: { max: 3, do: [ { step: { name: w } } ] } }",
      "w.yaml",
    );
    ctl.abort();
    const res = await runPipeline({
      registry: t.registry,
      config,
      store: new MemoryStore(),
      signal: ctl.signal,
    });
    expect(res.items[0]?.state).toBe("cancelled");
  });

  it("is rejected by validate without max", () => {
    const t = setup();
    const diag = validatePipeline(
      parsePipeline("pipeline: { loop: { do: [ { step: { name: a } } ] } }", "w.yaml"),
      t.registry,
    );
    expect(diag[0]?.message).toMatch(/requires `max`/);
    expect(
      loopStrategy.validate?.({ stop_when: "ok", max: 1 }, { cwd: "", registry: t.registry }),
    ).toEqual([]);
    expect(
      validatePipeline(
        parsePipeline("pipeline: { loop: { max: 2, do: [ { step: { name: a } } ] } }", "w.yaml"),
        t.registry,
      ),
    ).toEqual([]);
    const bad = validatePipeline(
      parsePipeline("pipeline: { loop: { max: 0, do: [ { step: { name: a } } ] } }", "w.yaml"),
      t.registry,
    );
    expect(bad[0]?.message).toMatch(/Invalid config for "loop"/);
  });
});
