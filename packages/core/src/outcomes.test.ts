import { describe, expect, it } from "vitest";
import {
  OUTCOME_PORTS,
  isOutcomePort,
  itemStateFor,
  resolveOutcome,
  type OutcomePolicy,
} from "./outcomes.js";

describe("outcome ports", () => {
  it("lists the documented ports", () => {
    expect(OUTCOME_PORTS).toEqual(["ok", "fail", "needs_human", "skip", "rejected"]);
  });

  it("isOutcomePort", () => {
    expect(isOutcomePort("ok")).toBe(true);
    expect(isOutcomePort("findings")).toBe(false);
  });
});

describe("resolveOutcome", () => {
  const policy: OutcomePolicy = { on_fail: "park" };

  it("ok continues", () => {
    expect(resolveOutcome("ok", {}, policy)).toEqual({ action: "continue" });
  });

  it("skip continues", () => {
    expect(resolveOutcome("skip", {}, policy)).toEqual({ action: "continue" });
  });

  it("a wired port routes to its target, even for ok", () => {
    expect(resolveOutcome("rejected", { rejected: "fix" }, policy)).toEqual({
      action: "route",
      target: "fix",
    });
    expect(resolveOutcome("ok", { ok: "end" }, policy)).toEqual({ action: "route", target: "end" });
  });

  it("unwired fail follows on_fail policy", () => {
    expect(resolveOutcome("fail", {}, { on_fail: "park" })).toEqual({
      action: "exit",
      port: "fail",
      state: "parked",
    });
    expect(resolveOutcome("fail", {}, { on_fail: "drop" })).toEqual({
      action: "exit",
      port: "fail",
      state: "failed",
    });
    expect(resolveOutcome("fail", {}, { on_fail: "propagate" })).toEqual({
      action: "propagate",
      port: "fail",
    });
  });

  it("needs_human and rejected with no route park the item", () => {
    expect(resolveOutcome("needs_human", {}, policy)).toEqual({
      action: "exit",
      port: "needs_human",
      state: "parked",
    });
    expect(resolveOutcome("rejected", {}, policy)).toEqual({
      action: "exit",
      port: "rejected",
      state: "parked",
    });
  });

  it("propagate policy bubbles non-ok ports to the parent", () => {
    expect(resolveOutcome("needs_human", {}, { on_fail: "propagate" })).toEqual({
      action: "propagate",
      port: "needs_human",
    });
  });

  it("custom (non-outcome) ports without a wire continue", () => {
    expect(resolveOutcome("findings", {}, policy)).toEqual({ action: "continue" });
  });
});

describe("itemStateFor", () => {
  it("maps terminal outcome ports to item states", () => {
    expect(itemStateFor("ok", "park")).toBe("done");
    expect(itemStateFor("skip", "park")).toBe("done");
    expect(itemStateFor("fail", "park")).toBe("parked");
    expect(itemStateFor("fail", "drop")).toBe("failed");
    expect(itemStateFor("fail", "propagate")).toBe("failed");
    expect(itemStateFor("needs_human", "drop")).toBe("parked");
    expect(itemStateFor("rejected", "park")).toBe("parked");
  });
});
