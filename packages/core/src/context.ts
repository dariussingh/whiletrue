import { EventBus } from "./events.js";
import type { PipelineNode } from "./config.js";
import type { Emit, PortValues } from "./module.js";
import type { OutcomePolicy } from "./outcomes.js";
import { Registry } from "./registry.js";

export interface Resource {
  name: string;
  dispose(): void | Promise<void>;
}

/**
 * Key-value context inherited by children, plus owned resources (workspaces, sessions, panes).
 * Resources are released when the scope exits, which makes "one worktree per issue" a matter of
 * where you declare it.
 */
export class Scope {
  readonly #values = new Map<string, unknown>();
  readonly #resources: Resource[] = [];
  #disposed = false;

  constructor(readonly parent?: Scope) {}

  get(key: string): unknown {
    if (this.#values.has(key)) return this.#values.get(key);
    return this.parent?.get(key);
  }

  has(key: string): boolean {
    return this.#values.has(key) || (this.parent?.has(key) ?? false);
  }

  set(key: string, value: unknown): void {
    this.#values.set(key, value);
  }

  /** All visible values; children shadow parents. Used as the template scope. */
  snapshot(): Record<string, unknown> {
    return { ...this.parent?.snapshot(), ...Object.fromEntries(this.#values) };
  }

  child(): Scope {
    return new Scope(this);
  }

  addResource(resource: Resource): void {
    if (this.#disposed) throw new Error(`Scope already disposed; cannot add "${resource.name}"`);
    this.#resources.push(resource);
  }

  get resourceNames(): string[] {
    return this.#resources.map((r) => r.name);
  }

  /** Releases resources in reverse order. Every disposer runs; the first failure is rethrown. */
  async dispose(): Promise<void> {
    if (this.#disposed) return;
    this.#disposed = true;
    let first: unknown;
    let failed = false;
    for (const r of [...this.#resources].reverse()) {
      try {
        await r.dispose();
      } catch (error) {
        if (!failed) {
          failed = true;
          first = error;
        }
      }
    }
    if (failed) throw first;
  }
}

export type LogLevel = "debug" | "info" | "warn" | "error";
export type Logger = (level: LogLevel, message: string, data?: Record<string, unknown>) => void;

/** Everything an action can see. */
export interface StepContext<Cfg> {
  readonly config: Cfg;
  readonly runId: string;
  readonly itemId: string;
  /** The step's id in the pipeline tree. */
  readonly nodeId: string;
  readonly attempt: number;
  /** Working directory for this step (project root or a workspace). */
  readonly cwd: string;
  readonly scope: Scope;
  readonly registry: Registry;
  readonly bus: EventBus;
  readonly log: Logger;
  readonly signal: AbortSignal;
  /** Adds spend (USD) to this step's recorded cost. */
  readonly recordCost: (usd: number) => void;
  /** Stable per-execution key, e.g. for idempotency: `<run>:<item>:<node>`. */
  readonly stepKey: string;
}

/** Everything a strategy can see. Children run only through `run`, so every execution is recorded. */
export interface CompositeContext<Cfg> extends StepContext<Cfg> {
  readonly policy: OutcomePolicy;
  run(child: PipelineNode, input: PortValues): Promise<Emit>;
}

const noopLog: Logger = () => {};

/** Builds a context with sensible defaults; used by the scheduler and by tests. */
export function createStepContext<Cfg>(
  config: Cfg,
  overrides: Partial<Omit<StepContext<Cfg>, "config">> = {},
): StepContext<Cfg> {
  const runId = overrides.runId ?? "run";
  const itemId = overrides.itemId ?? "item";
  const nodeId = overrides.nodeId ?? "node";
  return {
    config,
    runId,
    itemId,
    nodeId,
    attempt: 1,
    cwd: process.cwd(),
    scope: new Scope(),
    registry: new Registry(),
    bus: new EventBus(),
    log: noopLog,
    recordCost: () => {},
    signal: new AbortController().signal,
    stepKey: `${runId}:${itemId}:${nodeId}`,
    ...overrides,
  };
}
