import { describe, expect, it } from "vitest";
import { z } from "zod";
import { parsePipeline } from "../src/config.js";
import { defineAction, defineStrategy, type ActionModule } from "../src/module.js";
import { Registry } from "../src/registry.js";
import { formatDiagnostic, hasErrors, validatePipeline } from "../src/validate.js";

const action = (
  id: string,
  over: Partial<ActionModule<{ run?: string | undefined }>> = {},
): ActionModule<{ run?: string | undefined }> =>
  defineAction({
    id,
    kind: "action",
    config: z.object({ run: z.string().optional() }),
    ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Any" }] },
    effects: "pure",
    async run() {
      return { port: "ok" };
    },
    ...over,
  });

function makeRegistry(): Registry {
  const r = new Registry();
  r.registerAction(action("noop"));
  r.registerAction(action("emit.text", { ports: { in: [], out: [{ name: "out", cap: "Text" }] } }));
  r.registerAction(
    action("need.issue", { ports: { in: [{ name: "in", cap: "Issue" }], out: [] } }),
  );
  r.registerAction(action("risky", { effects: "side-effect" }));
  r.registerAction(action("safe.risky", { effects: "side-effect", idempotencyKey: () => "k" }));
  r.registerAction(
    action("checked", {
      validate: (c) => (c.run ? [] : [{ severity: "warning", message: "run is empty" }]),
    }),
  );
  r.registerStrategy(
    defineStrategy({
      id: "sequence",
      chain: true,
      config: z.object({}),
      async run() {
        return { port: "ok" };
      },
    }),
  );
  r.registerStrategy(
    defineStrategy({
      id: "loop",
      config: z.object({ max: z.number().optional() }),
      validate: (c) =>
        c.max === undefined ? [{ severity: "error", message: "loop needs max" }] : [],
      async run() {
        return { port: "ok" };
      },
    }),
  );
  return r;
}

const check = (yaml: string) => validatePipeline(parsePipeline(yaml, "w.yaml"), makeRegistry());

describe("validatePipeline", () => {
  it("passes a valid pipeline", () => {
    expect(check("pipeline: [ { noop: {} }, { noop: {} } ]")).toEqual([]);
  });

  it("flags unknown modules and lists known ones", () => {
    const d = check("pipeline: [ { nope: {} } ]");
    expect(d).toHaveLength(1);
    expect(d[0]!.message).toMatch(/Unknown module "nope" \(known: .*noop/);
    expect(hasErrors(d)).toBe(true);
  });

  it("flags config schema errors with the path", () => {
    const d = check("pipeline: [ { noop: { run: 5 } } ]");
    expect(d[0]!.message).toMatch(/Invalid config for "noop".* at "run"/);
  });

  it("runs module validate hooks and keeps warnings non-fatal", () => {
    const d = check("pipeline: [ { checked: {} } ]");
    expect(d).toHaveLength(1);
    expect(d[0]!.severity).toBe("warning");
    expect(hasErrors(d)).toBe(false);
    expect(check("pipeline: [ { checked: { run: x } } ]")).toEqual([]);
  });

  it("enforces strategy hooks (loop needs max)", () => {
    expect(check("pipeline: { loop: { do: [ { noop: {} } ] } }")[0]!.message).toBe(
      "loop needs max",
    );
    expect(check("pipeline: { loop: { max: 2, do: [ { noop: {} } ] } }")).toEqual([]);
  });

  it("forbids children on action modules", () => {
    const d = check("pipeline: [ { noop: {}, do: [ { noop: {} } ] } ]");
    expect(d.map((x) => x.message)).toContain(
      'Action module "noop" cannot have child steps (`do`)',
    );
  });

  it("requires idempotency keys on side-effect modules", () => {
    expect(check("pipeline: [ { risky: {} } ]")[0]!.message).toMatch(/idempotencyKey/);
    expect(check("pipeline: [ { safe.risky: {} } ]")).toEqual([]);
  });

  it("flags gates that are not deterministic even if defined unsafely", () => {
    const r = makeRegistry();
    // built without defineAction, simulating a plugin that skipped it
    r.registerAction({ ...action("x.fake"), id: "gate.fake", effects: "agentic" });
    const d = validatePipeline(parsePipeline("pipeline: [ { gate.fake: {} } ]", "w.yaml"), r);
    expect(d.map((x) => x.message)).toContain('Gate "gate.fake" must be deterministic');
  });

  it("type-checks chained wiring between sibling actions", () => {
    const bad = check("pipeline: [ { emit.text: {} }, { need.issue: {} } ]");
    expect(bad).toHaveLength(1);
    expect(bad[0]!.message).toBe('Cannot wire "root.0".out (Text) into "root.1".in (Issue)');
    expect(check("pipeline: [ { emit.text: {} }, { noop: {} } ]")).toEqual([]);
  });

  it("skips wiring checks around non-action nodes", () => {
    expect(
      check("pipeline: [ { loop: { max: 1, do: [ { noop: {} } ] } }, { need.issue: {} } ]"),
    ).toEqual([]);
  });

  it("validates nested steps", () => {
    const d = check("pipeline: { loop: { max: 1, do: [ { nope: {} } ] } }");
    expect(d[0]!.nodeId).toBe("root.0");
  });

  it("formats diagnostics", () => {
    const d = check("pipeline: [ { nope: {} } ]");
    expect(formatDiagnostic(d[0]!)).toMatch(
      /^w\.yaml:1:\d+: error: Unknown module "nope".*\[root\.0\]$/,
    );
  });
});
