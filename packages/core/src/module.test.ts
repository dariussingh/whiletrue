import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  EFFECTS,
  ModuleDefinitionError,
  defineAction,
  defineStrategy,
  isEffect,
  type ActionModule,
  type Strategy,
} from "./module.js";

const shellModule = (): ActionModule<{ run: string }> => ({
  id: "shell",
  kind: "action",
  config: z.object({ run: z.string() }),
  ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Text" }] },
  effects: "side-effect",
  async run(_input, ctx) {
    return { port: "ok", value: ctx.config.run };
  },
});

describe("effects", () => {
  it("lists the four effect kinds", () => {
    expect(EFFECTS).toEqual(["pure", "deterministic", "agentic", "side-effect"]);
  });

  it("isEffect accepts only known effects", () => {
    expect(isEffect("pure")).toBe(true);
    expect(isEffect("agentic")).toBe(true);
    expect(isEffect("magic")).toBe(false);
    expect(isEffect(undefined)).toBe(false);
  });
});

describe("defineAction", () => {
  it("returns a valid module unchanged", () => {
    const mod = shellModule();
    expect(defineAction(mod)).toBe(mod);
  });

  it("accepts dotted ids like gate.tests", () => {
    expect(defineAction({ ...shellModule(), id: "gate.tests" }).id).toBe("gate.tests");
  });

  it.each(["", "Shell", "gate.", ".gate", "gate..x", "1gate", "has space"])(
    "rejects invalid id %j",
    (id) => {
      expect(() => defineAction({ ...shellModule(), id })).toThrow(ModuleDefinitionError);
    },
  );

  it("rejects a non-action kind", () => {
    const bad = { ...shellModule(), kind: "composite" } as unknown as ActionModule<unknown>;
    expect(() => defineAction(bad)).toThrow(/kind/);
  });

  it("rejects an unknown effect", () => {
    const bad = { ...shellModule(), effects: "magic" } as unknown as ActionModule<unknown>;
    expect(() => defineAction(bad)).toThrow(/effect/);
  });

  it("rejects duplicate port names within a direction", () => {
    const bad = {
      ...shellModule(),
      ports: {
        in: [
          { name: "in", cap: "Any" },
          { name: "in", cap: "Text" },
        ],
        out: [],
      },
    };
    expect(() => defineAction(bad)).toThrow(/duplicate/i);
  });

  it("allows the same port name on in and out", () => {
    const ok = {
      ...shellModule(),
      ports: { in: [{ name: "x", cap: "Any" }], out: [{ name: "x", cap: "Any" }] },
    };
    expect(() => defineAction(ok)).not.toThrow();
  });

  it("names the offending module in the error", () => {
    try {
      defineAction({ ...shellModule(), id: "Bad" });
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ModuleDefinitionError);
      expect((e as Error).message).toContain("Bad");
    }
  });
});

describe("defineStrategy", () => {
  const loop = (): Strategy<{ max: number }> => ({
    id: "loop",
    config: z.object({ max: z.number() }),
    async run() {
      return { port: "ok" };
    },
  });

  it("returns a valid strategy unchanged", () => {
    const s = loop();
    expect(defineStrategy(s)).toBe(s);
  });

  it("rejects an invalid id", () => {
    expect(() => defineStrategy({ ...loop(), id: "Loop!" })).toThrow(ModuleDefinitionError);
  });
});

describe("run contract", () => {
  it("an action emits a port and value", async () => {
    const mod = defineAction(shellModule());
    const emit = await mod.run({}, { config: { run: "echo hi" } });
    expect(emit).toEqual({ port: "ok", value: "echo hi" });
  });
});
