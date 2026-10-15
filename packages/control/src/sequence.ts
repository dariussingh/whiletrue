import { z } from "zod";
import { defineStrategy } from "@whiletrue-ai/core";
import { runChain } from "./chain.js";

/** Runs children one after another; each `out` feeds the next `in`. */
export const sequenceStrategy = defineStrategy({
  id: "sequence",
  chain: true,
  config: z.object({}),
  run: (children, input, ctx) => runChain(children, input, ctx),
});
