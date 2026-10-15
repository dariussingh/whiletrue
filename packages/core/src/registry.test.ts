import { describe, expect, it } from "vitest";
import { z } from "zod";
import { defineAction, defineStrategy } from "./module.js";
import { Registry, RegistryError, type Plugin } from "./registry.js";
import type { Runner } from "./runner.js";

const action = (id: string) =>
  defineAction({
    id,
    kind: "action",
    config: z.object({}),
    ports: { in: [], out: [] },
    effects: "pure",
    async run() {
      return { port: "ok" };
    },
  });

const strategy = (id: string) =>
  defineStrategy({
    id,
    config: z.object({}),
    async run() {
      return { port: "ok" };
    },
  });

const runner = (id: string): Runner => ({
  id,
  capabilities: {
    interactive: false,
    structured_output: true,
    resume: false,
    fork: false,
    budget: true,
  },
  async run() {
    return { ok: true, text: "" };
  },
});

describe("Registry", () => {
  it("starts with builtin caps", () => {
    expect(new Registry().caps.has("Text")).toBe(true);
  });

  it("registers and resolves actions, strategies and runners", () => {
    const r = new Registry();
    r.registerAction(action("shell"));
    r.registerStrategy(strategy("loop"));
    r.registerRunner(runner("claude-code-headless"));
    expect(r.resolve("shell")).toMatchObject({ kind: "action" });
    expect(r.resolve("loop")).toMatchObject({ kind: "strategy" });
    expect(r.resolve("nope")).toBeUndefined();
    expect(r.getRunner("claude-code-headless")?.id).toBe("claude-code-headless");
    expect(r.getRunner("x")).toBeUndefined();
    expect(r.ids()).toEqual(["loop", "shell"]);
  });

  it("module and strategy ids share one namespace", () => {
    const r = new Registry();
    r.registerAction(action("x"));
    expect(() => r.registerStrategy(strategy("x"))).toThrow(RegistryError);
  });

  it("rejects duplicate registrations", () => {
    const r = new Registry();
    r.registerAction(action("a"));
    expect(() => r.registerAction(action("a"))).toThrow(/already registered/);
    r.registerRunner(runner("r"));
    expect(() => r.registerRunner(runner("r"))).toThrow(/already registered/);
  });

  it("registers and looks up providers by kind and name", () => {
    const r = new Registry();
    r.registerProvider("source", "json", { any: 1 });
    expect(r.getProvider("source", "json")).toEqual({ any: 1 });
    expect(r.getProvider("source", "x")).toBeUndefined();
    expect(() => r.registerProvider("source", "json", {})).toThrow(/already registered/);
  });

  it("installs plugins through the public API", () => {
    const plugin: Plugin = {
      name: "demo",
      register(api) {
        api.registerAction(action("demo.act"));
        api.registerCap("Thing", z.string());
      },
    };
    const r = new Registry();
    r.use(plugin);
    expect(r.resolve("demo.act")).toBeDefined();
    expect(r.caps.has("Thing")).toBe(true);
    expect(r.plugins()).toEqual(["demo"]);
    expect(() => r.use(plugin)).toThrow(/already installed/);
  });
});
