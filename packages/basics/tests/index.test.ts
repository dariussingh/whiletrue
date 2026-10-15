import { describe, expect, it } from "vitest";
import { Registry } from "@whiletrue-ai/core";
import { basicsPlugin } from "../src/index.js";

describe("basicsPlugin", () => {
  it("registers shell, gate.shell, exec and agent", () => {
    const r = new Registry();
    r.use(basicsPlugin);
    expect(r.ids()).toEqual(["agent", "exec", "gate.shell", "shell"]);
  });
});
