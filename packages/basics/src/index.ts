import type { Plugin } from "@whiletrue-ai/core";
import { agentModule } from "./agent.js";
import { execModule } from "./exec.js";
import { gateShellModule, shellModule } from "./shell.js";

export { agentModule } from "./agent.js";
export { execModule } from "./exec.js";
export { gateShellModule, shellModule } from "./shell.js";
export { runCommand, tail, type CommandOptions, type CommandResult } from "./process.js";

/** Registers the built-in modules through the same public plugin API third parties use. */
export const basicsPlugin: Plugin = {
  name: "@whiletrue-ai/basics",
  register(api) {
    api.registerAction(shellModule);
    api.registerAction(gateShellModule);
    api.registerAction(execModule);
    api.registerAction(agentModule);
  },
};
