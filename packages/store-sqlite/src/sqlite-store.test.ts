import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SqliteStore } from "./sqlite-store.js";

let store: SqliteStore;
beforeEach(() => {
  store = new SqliteStore(":memory:");
  store.createRun({ id: "r1", pipeline_hash: "h", started_at: 100 });
});
afterEach(() => store.close());

describe("schema", () => {
  it("creates every documented table via migrations", () => {
    expect(store.schemaVersion()).toBe(1);
    expect(store.tables()).toEqual(
      expect.arrayContaining([
        "runs",
        "items",
        "steps",
        "parks",
        "sessions",
        "effects",
        "learnings",
        "events",
        "cache",
        "schema_migrations",
      ]),
    );
  });

  it("persists across reopen and does not re-run migrations", () => {
    const dir = mkdtempSync(join(tmpdir(), "wt-"));
    const path = join(dir, "nested", "state.db");
    const a = new SqliteStore(path);
    a.createRun({ id: "x", pipeline_hash: "h", lockfile: "lock", started_at: 1 });
    a.close();
    const b = new SqliteStore(path);
    expect(b.schemaVersion()).toBe(1);
    expect(b.getRun("x")).toMatchObject({ id: "x", lockfile: "lock", status: "running" });
    b.close();
  });
});

describe("runs", () => {
  it("creates, finishes and fetches runs", () => {
    expect(store.getRun("r1")).toEqual({
      id: "r1",
      pipeline_hash: "h",
      lockfile: null,
      status: "running",
      started_at: 100,
      ended_at: null,
    });
    store.finishRun("r1", "done", 200);
    expect(store.getRun("r1")).toMatchObject({ status: "done", ended_at: 200 });
    expect(store.getRun("nope")).toBeUndefined();
  });

  it("returns the latest run", () => {
    store.createRun({ id: "r2", pipeline_hash: "h", started_at: 300 });
    expect(store.latestRun()?.id).toBe("r2");
  });

  it("latestRun is undefined when empty", () => {
    const empty = new SqliteStore(":memory:");
    expect(empty.latestRun()).toBeUndefined();
    empty.close();
  });
});

describe("items", () => {
  it("upserts, updates state and lists", () => {
    store.upsertItem({
      id: "i1",
      run_id: "r1",
      source_key: "k",
      state: "ready",
      payload: { n: 1 },
      depends_on: ["i0"],
    });
    store.upsertItem({
      id: "i1",
      run_id: "r1",
      source_key: "k2",
      state: "running",
      payload: undefined,
      depends_on: [],
    });
    store.setItemState("r1", "i1", "done");
    expect(store.listItems("r1")).toEqual([
      { id: "i1", run_id: "r1", source_key: "k2", state: "done", payload: null, depends_on: [] },
    ]);
  });

  it("rejects state changes for unknown items", () => {
    expect(() => store.setItemState("r1", "ghost", "done")).toThrow(/No item/);
  });
});

describe("steps", () => {
  it("records a step lifecycle", () => {
    const id = store.startStep({
      run_id: "r1",
      item_id: "i1",
      node_path: "root.0",
      attempt: 1,
      input: { a: 1 },
      started_at: 10,
    });
    expect(store.listSteps("r1")[0]).toMatchObject({
      id,
      status: "running",
      port: null,
      input: { a: 1 },
    });
    store.finishStep(id, {
      status: "completed",
      port: "ok",
      output: "x",
      cost_usd: 0.5,
      ended_at: 20,
    });
    expect(store.listSteps("r1", "i1")[0]).toMatchObject({
      status: "completed",
      port: "ok",
      output: "x",
      cost_usd: 0.5,
      error: null,
      ended_at: 20,
    });
    expect(store.listSteps("r1", "other")).toEqual([]);
  });

  it("records errors with defaults", () => {
    const id = store.startStep({
      run_id: "r1",
      item_id: "i1",
      node_path: "p",
      attempt: 2,
      input: undefined,
      started_at: 1,
    });
    store.finishStep(id, { status: "errored", error: "boom", ended_at: 2 });
    expect(store.listSteps("r1")[0]).toMatchObject({
      status: "errored",
      error: "boom",
      cost_usd: 0,
      port: null,
      output: null,
      attempt: 2,
    });
  });

  it("rejects finishing an unknown step", () => {
    expect(() => store.finishStep(999, { status: "completed", ended_at: 1 })).toThrow(/No step/);
  });
});

describe("events", () => {
  it("appends and filters events in order", () => {
    store.appendEvent({ type: "run.started", runId: "r1", ts: 1, data: {} });
    store.appendEvent({
      type: "step.started",
      runId: "r1",
      itemId: "i1",
      stepId: 3,
      ts: 2,
      data: { a: 1 },
    });
    store.appendEvent({ type: "log", runId: "r1", itemId: "i2", ts: 3, data: { m: "x" } });
    expect(store.listEvents("r1").map((e) => e.type)).toEqual([
      "run.started",
      "step.started",
      "log",
    ]);
    expect(store.listEvents("r1", { itemId: "i1" })).toEqual([
      { type: "step.started", runId: "r1", itemId: "i1", stepId: 3, ts: 2, data: { a: 1 } },
    ]);
    expect(store.listEvents("r1", { type: "log" })).toHaveLength(1);
    expect(store.listEvents("r1", { itemId: "i2", type: "log" })).toHaveLength(1);
    expect(store.listEvents("other")).toEqual([]);
  });
});
