import type { ZodType } from "zod";
import { CapRegistry, createBuiltinCaps } from "./caps.js";
import type { ActionModule, Strategy } from "./module.js";
import type { Runner } from "./runner.js";

export class RegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RegistryError";
  }
}

export type ProviderKind = "source" | "store" | "workspace" | "knowledge" | "comments";

export type Resolved =
  | { kind: "action"; module: ActionModule<unknown> }
  | { kind: "strategy"; module: Strategy<unknown> };

/** The public plugin surface. Built-in modules use exactly this API. */
export interface PluginApi {
  registerAction<Cfg>(module: ActionModule<Cfg>): void;
  registerStrategy<Cfg>(strategy: Strategy<Cfg>): void;
  registerRunner(runner: Runner): void;
  registerProvider(kind: ProviderKind, name: string, provider: unknown): void;
  registerCap(name: string, schema: ZodType): void;
}

export interface Plugin {
  name: string;
  register(api: PluginApi): void;
}

export class Registry implements PluginApi {
  readonly caps: CapRegistry;
  readonly #actions = new Map<string, ActionModule<unknown>>();
  readonly #strategies = new Map<string, Strategy<unknown>>();
  readonly #runners = new Map<string, Runner>();
  readonly #providers = new Map<string, unknown>();
  readonly #plugins: string[] = [];

  constructor(caps: CapRegistry = createBuiltinCaps()) {
    this.caps = caps;
  }

  #assertFreeId(id: string): void {
    if (this.#actions.has(id) || this.#strategies.has(id)) {
      throw new RegistryError(`Module or strategy "${id}" is already registered`);
    }
  }

  registerAction<Cfg>(module: ActionModule<Cfg>): void {
    this.#assertFreeId(module.id);
    this.#actions.set(module.id, module as ActionModule<unknown>);
  }

  registerStrategy<Cfg>(strategy: Strategy<Cfg>): void {
    this.#assertFreeId(strategy.id);
    this.#strategies.set(strategy.id, strategy as Strategy<unknown>);
  }

  registerRunner(runner: Runner): void {
    if (this.#runners.has(runner.id)) {
      throw new RegistryError(`Runner "${runner.id}" is already registered`);
    }
    this.#runners.set(runner.id, runner);
  }

  registerProvider(kind: ProviderKind, name: string, provider: unknown): void {
    const key = `${kind}:${name}`;
    if (this.#providers.has(key)) {
      throw new RegistryError(`Provider "${key}" is already registered`);
    }
    this.#providers.set(key, provider);
  }

  registerCap(name: string, schema: ZodType): void {
    this.caps.register(name, schema);
  }

  resolve(id: string): Resolved | undefined {
    const action = this.#actions.get(id);
    if (action) return { kind: "action", module: action };
    const strategy = this.#strategies.get(id);
    if (strategy) return { kind: "strategy", module: strategy };
    return undefined;
  }

  getRunner(id: string): Runner | undefined {
    return this.#runners.get(id);
  }

  getProvider(kind: ProviderKind, name: string): unknown {
    return this.#providers.get(`${kind}:${name}`);
  }

  ids(): string[] {
    return [...this.#actions.keys(), ...this.#strategies.keys()].sort();
  }

  use(plugin: Plugin): void {
    if (this.#plugins.includes(plugin.name)) {
      throw new RegistryError(`Plugin "${plugin.name}" is already installed`);
    }
    plugin.register(this);
    this.#plugins.push(plugin.name);
  }

  plugins(): string[] {
    return [...this.#plugins];
  }
}
