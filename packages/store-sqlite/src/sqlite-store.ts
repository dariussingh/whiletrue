import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type {
  FinishStepInput,
  ItemRecord,
  ItemState,
  RunRecord,
  RunStatus,
  StartStepInput,
  StateStore,
  StepRecord,
  WtEvent,
} from "@whiletrue-ai/core";
import { MIGRATIONS } from "./migrations.js";

type Row = Record<string, unknown>;

const json = (v: unknown): string | null => (v === undefined ? null : JSON.stringify(v));
const parse = (s: unknown): unknown => (typeof s === "string" ? JSON.parse(s) : null);

export class SqliteStore implements StateStore {
  readonly #db: DatabaseSync;

  /** `path` may be ":memory:". Parent directories are created for file databases. */
  constructor(path: string) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.#db = new DatabaseSync(path);
    this.#db.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;");
    this.#migrate();
  }

  #migrate(): void {
    this.#db.exec("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)");
    const row = this.#db.prepare("SELECT MAX(version) AS v FROM schema_migrations").get() as Row;
    const current = (row["v"] as number | null) ?? 0;
    MIGRATIONS.forEach((sql, i) => {
      const version = i + 1;
      if (version <= current) return;
      this.#db.exec("BEGIN");
      try {
        this.#db.exec(sql);
        this.#db.prepare("INSERT INTO schema_migrations (version) VALUES (?)").run(version);
        this.#db.exec("COMMIT");
      } catch (error) {
        this.#db.exec("ROLLBACK");
        throw error;
      }
    });
  }

  schemaVersion(): number {
    const row = this.#db.prepare("SELECT MAX(version) AS v FROM schema_migrations").get() as Row;
    return (row["v"] as number | null) ?? 0;
  }

  tables(): string[] {
    const rows = this.#db
      .prepare(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
      )
      .all() as Row[];
    return rows.map((r) => r["name"] as string);
  }

  createRun(run: {
    id: string;
    pipeline_hash: string;
    lockfile?: string;
    started_at: number;
  }): void {
    this.#db
      .prepare(
        "INSERT INTO runs (id, pipeline_hash, lockfile, status, started_at) VALUES (?, ?, ?, 'running', ?)",
      )
      .run(run.id, run.pipeline_hash, run.lockfile ?? null, run.started_at);
  }

  finishRun(id: string, status: Exclude<RunStatus, "running">, ended_at: number): void {
    this.#db
      .prepare("UPDATE runs SET status = ?, ended_at = ? WHERE id = ?")
      .run(status, ended_at, id);
  }

  #run(row: Row | undefined): RunRecord | undefined {
    if (!row) return undefined;
    return {
      id: row["id"] as string,
      pipeline_hash: row["pipeline_hash"] as string,
      lockfile: (row["lockfile"] as string | null) ?? null,
      status: row["status"] as RunStatus,
      started_at: row["started_at"] as number,
      ended_at: (row["ended_at"] as number | null) ?? null,
    };
  }

  getRun(id: string): RunRecord | undefined {
    return this.#run(
      this.#db.prepare("SELECT * FROM runs WHERE id = ?").get(id) as Row | undefined,
    );
  }

  latestRun(): RunRecord | undefined {
    return this.#run(
      this.#db.prepare("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").get() as
        Row | undefined,
    );
  }

  upsertItem(item: ItemRecord): void {
    this.#db
      .prepare(
        `INSERT INTO items (run_id, id, source_key, state, payload, depends_on)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT (run_id, id) DO UPDATE SET
           source_key = excluded.source_key, state = excluded.state,
           payload = excluded.payload, depends_on = excluded.depends_on`,
      )
      .run(
        item.run_id,
        item.id,
        item.source_key,
        item.state,
        json(item.payload),
        JSON.stringify(item.depends_on),
      );
  }

  setItemState(runId: string, itemId: string, state: ItemState): void {
    const res = this.#db
      .prepare("UPDATE items SET state = ? WHERE run_id = ? AND id = ?")
      .run(state, runId, itemId);
    if (res.changes === 0) throw new Error(`No item "${itemId}" in run "${runId}"`);
  }

  listItems(runId: string): ItemRecord[] {
    const rows = this.#db
      .prepare("SELECT * FROM items WHERE run_id = ? ORDER BY rowid")
      .all(runId) as Row[];
    return rows.map((r) => ({
      id: r["id"] as string,
      run_id: r["run_id"] as string,
      source_key: r["source_key"] as string,
      state: r["state"] as ItemState,
      payload: parse(r["payload"]),
      depends_on: JSON.parse(r["depends_on"] as string) as string[],
    }));
  }

  startStep(input: StartStepInput): number {
    const res = this.#db
      .prepare(
        `INSERT INTO steps (run_id, item_id, node_path, attempt, status, input, started_at)
         VALUES (?, ?, ?, ?, 'running', ?, ?)`,
      )
      .run(
        input.run_id,
        input.item_id,
        input.node_path,
        input.attempt,
        json(input.input),
        input.started_at,
      );
    return Number(res.lastInsertRowid);
  }

  finishStep(stepId: number, input: FinishStepInput): void {
    const res = this.#db
      .prepare(
        `UPDATE steps SET status = ?, port = ?, output = ?, cost_usd = ?, error = ?, ended_at = ?
         WHERE id = ?`,
      )
      .run(
        input.status,
        input.port ?? null,
        json(input.output),
        input.cost_usd ?? 0,
        input.error ?? null,
        input.ended_at,
        stepId,
      );
    if (res.changes === 0) throw new Error(`No step ${stepId}`);
  }

  listSteps(runId: string, itemId?: string): StepRecord[] {
    const rows = (
      itemId === undefined
        ? this.#db.prepare("SELECT * FROM steps WHERE run_id = ? ORDER BY id").all(runId)
        : this.#db
            .prepare("SELECT * FROM steps WHERE run_id = ? AND item_id = ? ORDER BY id")
            .all(runId, itemId)
    ) as Row[];
    return rows.map((r) => ({
      id: r["id"] as number,
      run_id: r["run_id"] as string,
      item_id: r["item_id"] as string,
      node_path: r["node_path"] as string,
      attempt: r["attempt"] as number,
      status: r["status"] as StepRecord["status"],
      port: (r["port"] as string | null) ?? null,
      input: parse(r["input"]),
      output: parse(r["output"]),
      cost_usd: r["cost_usd"] as number,
      error: (r["error"] as string | null) ?? null,
      started_at: r["started_at"] as number,
      ended_at: (r["ended_at"] as number | null) ?? null,
    }));
  }

  appendEvent(event: WtEvent): void {
    this.#db
      .prepare(
        "INSERT INTO events (run_id, item_id, step_id, type, data, ts) VALUES (?, ?, ?, ?, ?, ?)",
      )
      .run(
        event.runId,
        event.itemId ?? null,
        event.stepId ?? null,
        event.type,
        JSON.stringify(event.data),
        event.ts,
      );
  }

  listEvents(runId: string, filter: { itemId?: string; type?: string } = {}): WtEvent[] {
    const where = ["run_id = ?"];
    const args: (string | number)[] = [runId];
    if (filter.itemId !== undefined) {
      where.push("item_id = ?");
      args.push(filter.itemId);
    }
    if (filter.type !== undefined) {
      where.push("type = ?");
      args.push(filter.type);
    }
    const rows = this.#db
      .prepare(`SELECT * FROM events WHERE ${where.join(" AND ")} ORDER BY id`)
      .all(...args) as Row[];
    return rows.map((r) => ({
      type: r["type"] as WtEvent["type"],
      runId: r["run_id"] as string,
      ...(r["item_id"] !== null && { itemId: r["item_id"] as string }),
      ...(r["step_id"] !== null && { stepId: r["step_id"] as number }),
      ts: r["ts"] as number,
      data: JSON.parse(r["data"] as string) as Record<string, unknown>,
    }));
  }

  close(): void {
    this.#db.close();
  }
}
