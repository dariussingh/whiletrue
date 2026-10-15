import type { WtEvent } from "./events.js";
import type { ItemState } from "./outcomes.js";

export type RunStatus = "running" | "done" | "failed" | "parked" | "cancelled";
export type StepStatus = "running" | "completed" | "errored";

export interface RunRecord {
  id: string;
  pipeline_hash: string;
  lockfile: string | null;
  status: RunStatus;
  started_at: number;
  ended_at: number | null;
}

export interface ItemRecord {
  id: string;
  run_id: string;
  source_key: string;
  state: ItemState;
  payload: unknown;
  depends_on: string[];
}

export interface StepRecord {
  id: number;
  run_id: string;
  item_id: string;
  node_path: string;
  attempt: number;
  status: StepStatus;
  /** Port the step fired (ok, fail, ...), once completed. */
  port: string | null;
  input: unknown;
  output: unknown;
  cost_usd: number;
  error: string | null;
  started_at: number;
  ended_at: number | null;
}

export interface StartStepInput {
  run_id: string;
  item_id: string;
  node_path: string;
  attempt: number;
  input: unknown;
  started_at: number;
}

export interface FinishStepInput {
  status: Exclude<StepStatus, "running">;
  port?: string;
  output?: unknown;
  cost_usd?: number;
  error?: string;
  ended_at: number;
}

/**
 * Runtime source of truth for pipeline progress. SQLite is the only v1 backend;
 * everything else (GitHub, git, ...) is an optional integration elsewhere.
 */
export interface StateStore {
  createRun(run: {
    id: string;
    pipeline_hash: string;
    lockfile?: string;
    started_at: number;
  }): void;
  finishRun(id: string, status: Exclude<RunStatus, "running">, ended_at: number): void;
  getRun(id: string): RunRecord | undefined;
  latestRun(): RunRecord | undefined;

  upsertItem(item: ItemRecord): void;
  setItemState(runId: string, itemId: string, state: ItemState): void;
  listItems(runId: string): ItemRecord[];

  startStep(input: StartStepInput): number;
  finishStep(stepId: number, input: FinishStepInput): void;
  listSteps(runId: string, itemId?: string): StepRecord[];

  appendEvent(event: WtEvent): void;
  listEvents(runId: string, filter?: { itemId?: string; type?: string }): WtEvent[];

  close(): void;
}
