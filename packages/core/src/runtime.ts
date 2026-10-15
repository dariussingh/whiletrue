import { createHash, randomUUID } from "node:crypto";
import type { PipelineConfig, PipelineNode } from "./config.js";
import { Scope, createStepContext, type CompositeContext, type Logger } from "./context.js";
import { EventBus } from "./events.js";
import type { Emit, PortValues } from "./module.js";
import { isOutcomePort, itemStateFor, type ItemState } from "./outcomes.js";
import type { Registry } from "./registry.js";
import type { RunStatus, StateStore } from "./store.js";
import { renderDeep } from "./template.js";

export interface RunItem {
  id: string;
  payload?: unknown;
}

export interface RunOptions {
  registry: Registry;
  config: PipelineConfig;
  store: StateStore;
  bus?: EventBus;
  /** Defaults to a single implicit item named "main". */
  items?: RunItem[];
  cwd?: string;
  signal?: AbortSignal;
  runId?: string;
  log?: Logger;
  now?: () => number;
}

export interface ItemOutcome {
  itemId: string;
  state: ItemState;
  port: string;
  value?: unknown;
}

export interface RunResult {
  runId: string;
  status: RunStatus;
  items: ItemOutcome[];
}

export function hashPipeline(config: PipelineConfig): string {
  const strip = (n: PipelineNode): unknown => ({
    id: n.id,
    use: n.use,
    config: n.config,
    wires: n.wires,
    children: n.children.map(strip),
  });
  return createHash("sha256")
    .update(JSON.stringify(strip(config.root)))
    .digest("hex");
}

const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/**
 * Sequential scheduler: runs each item through the module tree and persists every step.
 * (Parallel item-token scheduling replaces the item loop in a later milestone.)
 */
export async function runPipeline(opts: RunOptions): Promise<RunResult> {
  const { registry, config, store } = opts;
  const now = opts.now ?? Date.now;
  const bus = opts.bus ?? new EventBus();
  const runId = opts.runId ?? randomUUID();
  const cwd = opts.cwd ?? process.cwd();
  const signal = opts.signal ?? new AbortController().signal;
  const log: Logger = opts.log ?? (() => {});
  const items = opts.items ?? [{ id: "main" }];
  const policy = { on_fail: config.on_fail };

  store.createRun({ id: runId, pipeline_hash: hashPipeline(config), started_at: now() });
  const unsubscribe = bus.subscribe((e) => store.appendEvent(e));
  bus.emit({ type: "run.started", runId, data: { pipeline: config.name, items: items.length } });

  const attempts = new Map<string, number>();
  const outcomes: ItemOutcome[] = [];

  for (const item of items) {
    store.upsertItem({
      id: item.id,
      run_id: runId,
      source_key: item.id,
      state: "ready",
      payload: item.payload ?? null,
      depends_on: [],
    });
  }

  for (const item of items) {
    const itemScope = new Scope();
    itemScope.set("run", { id: runId });
    itemScope.set("item", { id: item.id, ...(asRecord(item.payload) ?? {}) });

    const setState = (state: ItemState) => {
      store.setItemState(runId, item.id, state);
      bus.emit({ type: "item.state", runId, itemId: item.id, data: { state } });
    };

    const execute = async (
      node: PipelineNode,
      input: PortValues,
      parentScope: Scope,
    ): Promise<Emit> => {
      const resolved = registry.resolve(node.use);
      const key = `${item.id}:${node.id}`;
      const attempt = (attempts.get(key) ?? 0) + 1;
      attempts.set(key, attempt);

      const stepId = store.startStep({
        run_id: runId,
        item_id: item.id,
        node_path: node.id,
        attempt,
        input,
        started_at: now(),
      });
      bus.emit({
        type: "step.started",
        runId,
        itemId: item.id,
        stepId,
        data: { node: node.id, use: node.use, attempt },
      });

      let cost = 0;
      const finish = (emit: Emit, errored?: string): Emit => {
        store.finishStep(stepId, {
          status: errored ? "errored" : "completed",
          port: emit.port,
          output: emit.value,
          cost_usd: cost,
          ...(errored && { error: errored }),
          ended_at: now(),
        });
        bus.emit({
          type: errored ? "step.error" : "step.finished",
          runId,
          itemId: item.id,
          stepId,
          data: {
            node: node.id,
            port: emit.port,
            cost_usd: cost,
            ...(errored && { error: errored }),
          },
        });
        return emit;
      };
      const fail = (message: string): Emit =>
        finish({ port: "fail", value: { error: message } }, message);

      if (signal.aborted) return fail("cancelled");
      if (!resolved) return fail(`Unknown module "${node.use}"`);

      const scope = parentScope.child();
      try {
        const rendered = renderDeep(node.config, { ...scope.snapshot(), input });
        const parsed = resolved.module.config.safeParse(rendered);
        if (!parsed.success) {
          return fail(
            `Invalid config for "${node.use}": ${parsed.error.issues.map((i) => i.message).join("; ")}`,
          );
        }
        const base = {
          runId,
          itemId: item.id,
          nodeId: node.id,
          attempt,
          cwd,
          scope,
          registry,
          bus,
          log,
          signal,
          recordCost: (usd: number) => {
            cost += usd;
          },
          stepKey: `${runId}:${item.id}:${node.id}`,
        };
        let emit: Emit;
        if (resolved.kind === "action") {
          emit = await resolved.module.run(input, createStepContext(parsed.data, base));
          const outPort = resolved.module.ports.out.find((p) => p.name === "out");
          if (emit.port === "ok" && emit.value !== undefined && outPort) {
            const check = registry.caps.validate(outPort.cap, emit.value);
            if (!check.ok) {
              return fail(
                `Output of "${node.use}" does not match cap ${outPort.cap}: ${check.errors.join("; ")}`,
              );
            }
          }
        } else {
          const ctx: CompositeContext<unknown> = {
            ...createStepContext(parsed.data, base),
            policy,
            run: (child, childInput) => execute(child, childInput, scope),
          };
          emit = await resolved.module.run(node.children, input, ctx);
        }
        return finish(emit);
      } catch (error) {
        return fail(errorMessage(error));
      } finally {
        await scope.dispose().catch((error: unknown) => {
          log("warn", `Failed to release resources for ${node.id}: ${errorMessage(error)}`);
        });
      }
    };

    setState("running");
    const emit = await execute(config.root, {}, itemScope);
    await itemScope.dispose();
    const cancelled = signal.aborted;
    const state: ItemState = cancelled
      ? "cancelled"
      : isOutcomePort(emit.port)
        ? itemStateFor(emit.port, config.on_fail)
        : "done";
    setState(state);
    outcomes.push({
      itemId: item.id,
      state,
      port: emit.port,
      ...(emit.value !== undefined && { value: emit.value }),
    });
  }

  const status: RunStatus = outcomes.some((o) => o.state === "cancelled")
    ? "cancelled"
    : outcomes.some((o) => o.state === "failed")
      ? "failed"
      : outcomes.some((o) => o.state === "parked")
        ? "parked"
        : "done";
  store.finishRun(runId, status, now());
  bus.emit({ type: "run.finished", runId, data: { status } });
  unsubscribe();
  return { runId, status, items: outcomes };
}

const asRecord = (v: unknown): Record<string, unknown> | undefined =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : undefined;
