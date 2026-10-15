import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { basicsPlugin } from "@whiletrue-ai/basics";
import { claudeCodePlugin } from "@whiletrue-ai/claude-code";
import { controlPlugin } from "@whiletrue-ai/control";
import { Registry, type Plugin } from "@whiletrue-ai/core";

export interface RegistryOptions {
  /** Path to the claude binary; defaults to WHILETRUE_CLAUDE_BIN or `claude`. */
  claudeCommand?: string;
}

/** Core + the default plugins every project gets. */
export function createDefaultRegistry(options: RegistryOptions = {}): Registry {
  const registry = new Registry();
  registry.use(controlPlugin);
  registry.use(basicsPlugin);
  registry.use(
    claudeCodePlugin({
      command: options.claudeCommand ?? process.env["WHILETRUE_CLAUDE_BIN"] ?? "claude",
    }),
  );
  return registry;
}

const isPlugin = (v: unknown): v is Plugin =>
  v !== null &&
  typeof v === "object" &&
  typeof (v as Plugin).name === "string" &&
  typeof (v as Plugin).register === "function";

/** Installs plugins named in `uses:` that aren't already built in, resolving from the project. */
export async function loadPlugins(registry: Registry, uses: string[], cwd: string): Promise<void> {
  const require = createRequire(`${cwd}/`);
  for (const name of uses) {
    if (registry.plugins().includes(name)) continue;
    let resolved: string;
    try {
      resolved = require.resolve(name);
    } catch {
      throw new Error(`Cannot find plugin "${name}" (install it in this project)`);
    }
    const mod = (await import(pathToFileURL(resolved).href)) as Record<string, unknown>;
    const dflt = mod["default"] as Record<string, unknown> | undefined;
    // `default` or `plugin`, also when a CommonJS module is wrapped as `default`.
    const candidate = [mod["default"], mod["plugin"], dflt?.["default"], dflt?.["plugin"]].find(
      isPlugin,
    );
    if (!candidate) {
      throw new Error(`Plugin "${name}" must export a Plugin as default or \`plugin\``);
    }
    registry.use(candidate);
  }
}
