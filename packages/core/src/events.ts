/** Every started/output/cost/error/approval/takeover is an event; the log is append-only. */
export type EventType =
  | "run.started"
  | "run.finished"
  | "item.state"
  | "step.started"
  | "step.finished"
  | "step.error"
  | "agent.event"
  | "log";

export interface WtEvent {
  type: EventType;
  runId: string;
  itemId?: string;
  stepId?: number;
  /** Epoch milliseconds. */
  ts: number;
  data: Record<string, unknown>;
}

export type EventInput = Omit<WtEvent, "ts"> & { ts?: number };
export type EventListener = (event: WtEvent) => void;

export class EventBus {
  readonly #listeners = new Set<EventListener>();
  readonly #now: () => number;
  readonly #onError: (error: unknown, event: WtEvent) => void;

  constructor(
    options: { now?: () => number; onError?: (error: unknown, event: WtEvent) => void } = {},
  ) {
    this.#now = options.now ?? Date.now;
    this.#onError = options.onError ?? (() => {});
  }

  subscribe(listener: EventListener): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  /** Delivers synchronously, in subscription order. A failing listener never breaks the emitter. */
  emit(input: EventInput): WtEvent {
    const event: WtEvent = { ...input, ts: input.ts ?? this.#now() };
    for (const listener of [...this.#listeners]) {
      try {
        listener(event);
      } catch (error) {
        this.#onError(error, event);
      }
    }
    return event;
  }
}
