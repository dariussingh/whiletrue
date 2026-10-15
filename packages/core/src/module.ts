import type { ZodType } from "zod";

/** How a module may touch the world. Drives the safety rules (see architecture.md). */
export const EFFECTS = ["pure", "deterministic", "agentic", "side-effect"] as const;
export type Effect = (typeof EFFECTS)[number];

export function isEffect(value: unknown): value is Effect {
  return typeof value === "string" && (EFFECTS as readonly string[]).includes(value);
}

/** A named, typed connection point. `cap` names a schema (Issue, Diff, Text, Any, ...). */
export interface PortSpec {
  name: string;
  cap: string;
  optional?: boolean;
}

/** Values arriving on (or leaving from) a module's ports, keyed by port name. */
export type PortValues = Record<string, unknown>;

/** Which port fires, and with what. Outcome ports (ok, fail, ...) are added in a later item. */
export interface Emit {
  port: string;
  value?: unknown;
}

/** A child in a composite's tree. Expanded when the config loader lands. */
export interface Node {
  id: string;
  module: string;
  config?: unknown;
}

/** Context handed to an action. Grows (workspace, sessions, bus, logger, ...) with the context item. */
export interface StepContext<Cfg> {
  readonly config: Cfg;
}

/** Context handed to a strategy. Children run only through `run`, so every execution is recorded. */
export interface CompositeContext<Cfg> extends StepContext<Cfg> {
  run(child: Node, input: PortValues): Promise<Emit>;
}

export type Severity = "error" | "warning";

/** A static-validation finding about a config (never thrown at run time). */
export interface ConfigIssue {
  severity: Severity;
  message: string;
}

export interface ModuleMeta<Cfg> {
  id: string; // e.g. 'gate.tests'
  kind: "action" | "composite";
  config: ZodType<Cfg>;
  ports: { in: PortSpec[]; out: PortSpec[] };
  effects: Effect;
  capabilities?: string[]; // e.g. 'needs-workspace', 'needs-session'
}

export interface ActionModule<Cfg> extends ModuleMeta<Cfg> {
  kind: "action";
  /** Extra static checks beyond the config schema (e.g. agent limits). */
  validate?(config: Cfg): ConfigIssue[];
  /** Required for `side-effect` modules: a deterministic key so resume never repeats the effect. */
  idempotencyKey?(input: PortValues, ctx: StepContext<Cfg>): string;
  run(input: PortValues, ctx: StepContext<Cfg>): Promise<Emit>;
}

export interface Strategy<Cfg> {
  id: string; // e.g. 'loop'
  config: ZodType<Cfg>;
  /** Children are chained: each child's `out` feeds the next child's `in`. */
  chain?: boolean;
  /** Extra static checks beyond the config schema (e.g. loops must be bounded). */
  validate?(config: Cfg): ConfigIssue[];
  run(children: Node[], input: PortValues, ctx: CompositeContext<Cfg>): Promise<Emit>;
}

export class ModuleDefinitionError extends Error {
  constructor(id: string, problem: string) {
    super(`Invalid module "${id}": ${problem}`);
    this.name = "ModuleDefinitionError";
  }
}

const ID = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/;

function checkId(id: string): void {
  if (!ID.test(id)) {
    throw new ModuleDefinitionError(id, "id must be lowercase dotted segments, e.g. 'gate.tests'");
  }
}

function checkPorts(id: string, direction: string, ports: PortSpec[]): void {
  const seen = new Set<string>();
  for (const { name } of ports) {
    if (seen.has(name)) {
      throw new ModuleDefinitionError(id, `duplicate ${direction} port "${name}"`);
    }
    seen.add(name);
  }
}

/** Validates an action module's static shape at definition time and returns it unchanged. */
export function defineAction<Cfg>(mod: ActionModule<Cfg>): ActionModule<Cfg> {
  checkId(mod.id);
  if (mod.kind !== "action") {
    throw new ModuleDefinitionError(mod.id, `kind must be 'action', got '${String(mod.kind)}'`);
  }
  if (!isEffect(mod.effects)) {
    throw new ModuleDefinitionError(mod.id, `unknown effect '${String(mod.effects)}'`);
  }
  if (mod.id.startsWith("gate.") && mod.effects !== "deterministic") {
    throw new ModuleDefinitionError(
      mod.id,
      "gates must be deterministic: an agent can never grade itself",
    );
  }
  checkPorts(mod.id, "in", mod.ports.in);
  checkPorts(mod.id, "out", mod.ports.out);
  return mod;
}

/** Validates a strategy's static shape at definition time and returns it unchanged. */
export function defineStrategy<Cfg>(strategy: Strategy<Cfg>): Strategy<Cfg> {
  checkId(strategy.id);
  return strategy;
}
