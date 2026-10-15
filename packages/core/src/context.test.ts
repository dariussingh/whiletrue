import { describe, expect, it } from "vitest";
import { Scope, createStepContext } from "./context.js";

describe("Scope", () => {
  it("inherits values from the parent and shadows them", () => {
    const root = new Scope();
    root.set("repo", "r");
    root.set("item", "a");
    const child = root.child();
    child.set("item", "b");
    expect(child.get("repo")).toBe("r");
    expect(child.get("item")).toBe("b");
    expect(root.get("item")).toBe("a");
    expect(child.has("repo")).toBe(true);
    expect(child.has("zzz")).toBe(false);
    expect(child.get("zzz")).toBeUndefined();
    expect(child.snapshot()).toEqual({ repo: "r", item: "b" });
  });

  it("falls back to an empty snapshot and has() on a root scope", () => {
    const s = new Scope();
    expect(s.snapshot()).toEqual({});
    expect(s.has("x")).toBe(false);
  });

  it("disposes resources in reverse order, once", async () => {
    const order: string[] = [];
    const s = new Scope();
    s.addResource({ name: "worktree", dispose: () => void order.push("worktree") });
    s.addResource({
      name: "session",
      dispose: async () => {
        order.push("session");
      },
    });
    expect(s.resourceNames).toEqual(["worktree", "session"]);
    await s.dispose();
    await s.dispose();
    expect(order).toEqual(["session", "worktree"]);
  });

  it("runs every disposer and rethrows the first failure", async () => {
    const order: string[] = [];
    const s = new Scope();
    s.addResource({ name: "a", dispose: () => void order.push("a") });
    s.addResource({
      name: "b",
      dispose: () => {
        throw new Error("first");
      },
    });
    s.addResource({
      name: "c",
      dispose: () => {
        throw new Error("second");
      },
    });
    await expect(s.dispose()).rejects.toThrow("second");
    expect(order).toEqual(["a"]);
  });

  it("refuses new resources after disposal", async () => {
    const s = new Scope();
    await s.dispose();
    expect(() => s.addResource({ name: "late", dispose: () => {} })).toThrow(/already disposed/);
  });
});

describe("createStepContext", () => {
  it("fills defaults", () => {
    const ctx = createStepContext({ a: 1 });
    expect(ctx.config).toEqual({ a: 1 });
    expect(ctx.stepKey).toBe("run:item:node");
    expect(ctx.attempt).toBe(1);
    expect(ctx.signal.aborted).toBe(false);
    expect(() => ctx.log("info", "x")).not.toThrow();
    expect(ctx.registry.caps.has("Text")).toBe(true);
  });

  it("accepts overrides", () => {
    const ctx = createStepContext({}, { runId: "r1", itemId: "i1", nodeId: "n1", cwd: "/tmp" });
    expect(ctx.stepKey).toBe("r1:i1:n1");
    expect(ctx.cwd).toBe("/tmp");
  });
});
