import { describe, expect, it, vi } from "vitest";
import { EventBus, type WtEvent } from "./events.js";

describe("EventBus", () => {
  it("stamps events and delivers them in subscription order", () => {
    const bus = new EventBus({ now: () => 42 });
    const seen: string[] = [];
    bus.subscribe((e) => seen.push(`a:${e.type}:${e.ts}`));
    bus.subscribe((e) => seen.push(`b:${e.type}`));
    const e = bus.emit({ type: "log", runId: "r", data: { msg: "hi" } });
    expect(e.ts).toBe(42);
    expect(seen).toEqual(["a:log:42", "b:log"]);
  });

  it("keeps an explicit timestamp", () => {
    const bus = new EventBus({ now: () => 1 });
    expect(bus.emit({ type: "log", runId: "r", ts: 9, data: {} }).ts).toBe(9);
  });

  it("defaults to the wall clock", () => {
    const before = Date.now();
    const e = new EventBus().emit({ type: "log", runId: "r", data: {} });
    expect(e.ts).toBeGreaterThanOrEqual(before);
  });

  it("unsubscribes", () => {
    const bus = new EventBus();
    const fn = vi.fn();
    const off = bus.subscribe(fn);
    bus.emit({ type: "log", runId: "r", data: {} });
    off();
    bus.emit({ type: "log", runId: "r", data: {} });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("isolates listener failures and reports them", () => {
    const errors: unknown[] = [];
    const bus = new EventBus({ onError: (e) => errors.push(e) });
    const ok = vi.fn();
    bus.subscribe(() => {
      throw new Error("boom");
    });
    bus.subscribe(ok);
    bus.emit({ type: "log", runId: "r", data: {} });
    expect(ok).toHaveBeenCalled();
    expect(errors).toHaveLength(1);
  });

  it("swallows listener failures silently without an onError", () => {
    const bus = new EventBus();
    bus.subscribe(() => {
      throw new Error("boom");
    });
    expect(() =>
      bus.emit({ type: "log", runId: "r", data: {} } as Omit<WtEvent, "ts">),
    ).not.toThrow();
  });
});
