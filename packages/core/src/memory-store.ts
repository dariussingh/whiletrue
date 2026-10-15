import type { WtEvent } from "./events.js";
import type { ItemState } from "./outcomes.js";
import type {
  FinishStepInput,
  ItemRecord,
  RunRecord,
  RunStatus,
  StartStepInput,
  StateStore,
  StepRecord,
} from "./store.js";

/** In-memory StateStore for tests and throwaway runs. */
export class MemoryStore implements StateStore {
  readonly #runs: RunRecord[] = [];
  readonly #items: ItemRecord[] = [];
  readonly #steps: StepRecord[] = [];
  readonly #events: WtEvent[] = [];

  createRun(run: {
    id: string;
    pipeline_hash: string;
    lockfile?: string;
    started_at: number;
  }): void {
    this.#runs.push({
      id: run.id,
      pipeline_hash: run.pipeline_hash,
      lockfile: run.lockfile ?? null,
      status: "running",
      started_at: run.started_at,
      ended_at: null,
    });
  }

  finishRun(id: string, status: Exclude<RunStatus, "running">, ended_at: number): void {
    const run = this.getRun(id);
    if (!run) throw new Error(`No run "${id}"`);
    run.status = status;
    run.ended_at = ended_at;
  }

  getRun(id: string): RunRecord | undefined {
    return this.#runs.find((r) => r.id === id);
  }

  latestRun(): RunRecord | undefined {
    return this.#runs.at(-1);
  }

  upsertItem(item: ItemRecord): void {
    const i = this.#items.findIndex((x) => x.run_id === item.run_id && x.id === item.id);
    if (i >= 0) this.#items[i] = { ...item };
    else this.#items.push({ ...item });
  }

  setItemState(runId: string, itemId: string, state: ItemState): void {
    const item = this.#items.find((x) => x.run_id === runId && x.id === itemId);
    if (!item) throw new Error(`No item "${itemId}" in run "${runId}"`);
    item.state = state;
  }

  listItems(runId: string): ItemRecord[] {
    return this.#items.filter((x) => x.run_id === runId).map((x) => ({ ...x }));
  }

  startStep(input: StartStepInput): number {
    const id = this.#steps.length + 1;
    this.#steps.push({
      id,
      run_id: input.run_id,
      item_id: input.item_id,
      node_path: input.node_path,
      attempt: input.attempt,
      status: "running",
      port: null,
      input: input.input ?? null,
      output: null,
      cost_usd: 0,
      error: null,
      started_at: input.started_at,
      ended_at: null,
    });
    return id;
  }

  finishStep(stepId: number, input: FinishStepInput): void {
    const step = this.#steps.find((s) => s.id === stepId);
    if (!step) throw new Error(`No step ${stepId}`);
    step.status = input.status;
    step.port = input.port ?? null;
    step.output = input.output ?? null;
    step.cost_usd = input.cost_usd ?? 0;
    step.error = input.error ?? null;
    step.ended_at = input.ended_at;
  }

  listSteps(runId: string, itemId?: string): StepRecord[] {
    return this.#steps
      .filter((s) => s.run_id === runId && (itemId === undefined || s.item_id === itemId))
      .map((s) => ({ ...s }));
  }

  appendEvent(event: WtEvent): void {
    this.#events.push({ ...event });
  }

  listEvents(runId: string, filter: { itemId?: string; type?: string } = {}): WtEvent[] {
    return this.#events.filter(
      (e) =>
        e.runId === runId &&
        (filter.itemId === undefined || e.itemId === filter.itemId) &&
        (filter.type === undefined || e.type === filter.type),
    );
  }

  close(): void {}
}
