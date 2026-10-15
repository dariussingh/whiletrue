import { describe, expect, it } from "vitest";
import { z } from "zod";
import { CapRegistry, CapError, createBuiltinCaps } from "../src/caps.js";

describe("CapRegistry", () => {
  it("registers and retrieves caps", () => {
    const caps = new CapRegistry();
    caps.register("Num", z.number());
    expect(caps.has("Num")).toBe(true);
    expect(caps.names()).toEqual(["Num"]);
  });

  it("rejects duplicate registration and invalid names", () => {
    const caps = new CapRegistry();
    caps.register("Num", z.number());
    expect(() => caps.register("Num", z.number())).toThrow(CapError);
    expect(() => caps.register("bad name", z.number())).toThrow(CapError);
  });

  it("throws on unknown cap lookups", () => {
    const caps = new CapRegistry();
    expect(() => caps.validate("Nope", 1)).toThrow(/unknown cap/i);
    expect(() => caps.toJsonSchema("Nope")).toThrow(/unknown cap/i);
  });

  it("validates values and reports issues", () => {
    const caps = new CapRegistry();
    caps.register("Num", z.number());
    expect(caps.validate("Num", 3)).toEqual({ ok: true, value: 3 });
    const bad = caps.validate("Num", "x");
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(bad.errors[0]).toMatch(/expected number/i);
  });

  it("prefixes error paths", () => {
    const caps = new CapRegistry();
    caps.register("Obj", z.object({ a: z.object({ b: z.number() }) }));
    const bad = caps.validate("Obj", { a: { b: "x" } });
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(bad.errors[0]).toMatch(/^a\.b:/);
  });

  it("emits JSON Schema", () => {
    const caps = new CapRegistry();
    caps.register("Num", z.number());
    expect(caps.toJsonSchema("Num")).toMatchObject({ type: "number" });
  });
});

describe("compatibility", () => {
  const caps = createBuiltinCaps();

  it("same cap is compatible", () => {
    expect(caps.compatible("Issue", "Issue")).toBe(true);
  });

  it("Any accepts and produces anything", () => {
    expect(caps.compatible("Issue", "Any")).toBe(true);
    expect(caps.compatible("Any", "Diff")).toBe(true);
  });

  it("different concrete caps are incompatible", () => {
    expect(caps.compatible("Issue", "Diff")).toBe(false);
  });

  it("unknown caps are incompatible", () => {
    expect(caps.compatible("Nope", "Issue")).toBe(false);
  });
});

describe("builtin caps", () => {
  const caps = createBuiltinCaps();

  it("defines the documented set", () => {
    expect(caps.names().sort()).toEqual(
      ["Any", "Diff", "Issue", "PullRequest", "ReviewFindings", "Text", "Verdict"].sort(),
    );
  });

  it("accepts representative values", () => {
    expect(caps.validate("Text", "hi").ok).toBe(true);
    expect(caps.validate("Any", { x: 1 }).ok).toBe(true);
    expect(caps.validate("Verdict", { pass: true }).ok).toBe(true);
    expect(caps.validate("Issue", { id: "1", title: "t", body: "b" }).ok).toBe(true);
    expect(caps.validate("Diff", { files: [{ path: "a", additions: 1, deletions: 0 }] }).ok).toBe(
      true,
    );
    expect(caps.validate("PullRequest", { number: 1, url: "u", branch: "b" }).ok).toBe(true);
    expect(
      caps.validate("ReviewFindings", { findings: [{ severity: "high", message: "m" }] }).ok,
    ).toBe(true);
  });

  it("rejects malformed values", () => {
    expect(caps.validate("Verdict", { pass: "yes" }).ok).toBe(false);
    expect(caps.validate("Issue", { title: "t" }).ok).toBe(false);
    expect(caps.validate("Text", 5).ok).toBe(false);
  });
});
