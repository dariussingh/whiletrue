import { z } from "zod";
import { defineStrategy, type Emit } from "@whiletrue-ai/core";
import { runChain } from "./chain.js";

const LoopConfig = z.object({
  max: z.union([z.number().int().positive(), z.literal("unbounded")]).optional(),
  /** `ok`: stop as soon as an iteration succeeds (Ralph). `never`: always run all iterations. */
  stop_when: z.enum(["ok", "never"]).default("ok"),
});
type LoopConfig = z.infer<typeof LoopConfig>;

const describe = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (value !== null && typeof value === "object") {
    const v = value as Record<string, unknown>;
    const parts = [v["error"], v["output"]].filter((p): p is string => typeof p === "string");
    if (parts.length) return parts.join("\n");
  }
  return JSON.stringify(value) ?? "";
};

/**
 * Repeats its children, bounded by `max` (or an explicit `max: unbounded`). Each iteration sees
 * `{{loop.index}}` (1-based) and `{{loop.previous}}`, the previous failed iteration's output.
 * M0 semantics; `until` expressions, `over` and `collect` arrive with the expression language.
 */
export const loopStrategy = defineStrategy<LoopConfig>({
  id: "loop",
  config: LoopConfig,
  validate: (config) =>
    config.max === undefined
      ? [
          {
            severity: "error",
            message:
              "`loop` requires `max` (or an explicit `max: unbounded`): loops are bounded by default",
          },
        ]
      : [],
  async run(children, input, ctx) {
    const max =
      ctx.config.max === "unbounded" || ctx.config.max === undefined ? Infinity : ctx.config.max;
    let last: Emit = { port: "ok" };
    for (let index = 1; index <= max; index++) {
      if (ctx.signal.aborted) return { port: "fail", value: { error: "cancelled" } };
      ctx.scope.set("loop", { index, previous: describe(last.port === "ok" ? "" : last.value) });
      ctx.log("info", `loop iteration ${index}${max === Infinity ? "" : `/${max}`}`);
      last = await runChain(children, input, ctx);
      if (last.port === "needs_human" || last.port === "rejected") return last;
      if (last.port === "ok" && ctx.config.stop_when === "ok") return last;
    }
    if (ctx.config.stop_when === "never") return last;
    return {
      port: "fail",
      value: { error: `loop gave up after ${max} iterations`, last: last.value },
    };
  },
});
