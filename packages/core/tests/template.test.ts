import { describe, expect, it } from "vitest";
import { TemplateError, renderDeep, renderTemplate } from "../src/template.js";

const scope = { item: { id: 7, title: "Fix it", tags: ["a"] }, steps: { "my-step": { out: "x" } } };

describe("renderTemplate", () => {
  it("substitutes dotted paths", () => {
    expect(renderTemplate("#{{item.id}}: {{ item.title }}", scope)).toBe("#7: Fix it");
  });

  it("supports dashed keys and JSON-encodes objects", () => {
    expect(renderTemplate("{{steps.my-step.out}}", scope)).toBe("x");
    expect(renderTemplate("{{item.tags}}", scope)).toBe('["a"]');
  });

  it("leaves text without tokens alone", () => {
    expect(renderTemplate("plain", scope)).toBe("plain");
  });

  it("throws on missing variables", () => {
    expect(() => renderTemplate("{{item.nope}}", scope)).toThrow(TemplateError);
    expect(() => renderTemplate("{{nope.x}}", scope)).toThrow(/not defined/);
    expect(() => renderTemplate("{{item.id.deeper}}", scope)).toThrow(/not defined/);
  });

  it("rejects expressions", () => {
    expect(() => renderTemplate("{{ item.id == 7 }}", scope)).toThrow(/Unsupported/);
  });
});

describe("renderDeep", () => {
  it("renders strings in nested structures and keeps other values", () => {
    expect(
      renderDeep({ a: "{{item.id}}", b: [1, "{{item.title}}"], c: { d: true }, e: null }, scope),
    ).toEqual({ a: "7", b: [1, "Fix it"], c: { d: true }, e: null });
  });
});
