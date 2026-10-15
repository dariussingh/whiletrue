import type { Plugin } from "@whiletrue-ai/core";
import { loopStrategy } from "./loop.js";
import { sequenceStrategy } from "./sequence.js";

export { runChain } from "./chain.js";
export { loopStrategy } from "./loop.js";
export { sequenceStrategy } from "./sequence.js";

export const controlPlugin: Plugin = {
  name: "@whiletrue-ai/control",
  register(api) {
    api.registerStrategy(sequenceStrategy);
    api.registerStrategy(loopStrategy);
  },
};
