import type { Plugin } from "@whiletrue-ai/core";
import { createHeadlessRunner, type HeadlessOptions } from "./headless.js";

export { buildHeadlessArgs, createHeadlessRunner, type HeadlessOptions } from "./headless.js";

/** Registers the Claude Code runners. */
export function claudeCodePlugin(options: HeadlessOptions = {}): Plugin {
  return {
    name: "@whiletrue-ai/claude-code",
    register(api) {
      api.registerRunner(createHeadlessRunner(options));
    },
  };
}
