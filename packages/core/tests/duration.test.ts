import { describe, expect, it } from "vitest";
import { parseDuration } from "../src/duration.js";

describe("parseDuration", () => {
  it.each([
    ["250ms", 250],
    ["30s", 30_000],
    ["90m", 5_400_000],
    ["1h", 3_600_000],
    ["1.5s", 1500],
    [" 2s ", 2000],
  ])("parses %s", (input, ms) => {
    expect(parseDuration(input)).toBe(ms);
  });

  it("passes numbers through as ms", () => {
    expect(parseDuration(1234)).toBe(1234);
  });

  it.each(["", "abc", "10", "10d", "-5s"])("rejects %j", (bad) => {
    expect(() => parseDuration(bad)).toThrow(/Invalid duration/);
  });

  it("rejects negative or non-finite numbers", () => {
    expect(() => parseDuration(-1)).toThrow();
    expect(() => parseDuration(Number.NaN)).toThrow();
  });
});
