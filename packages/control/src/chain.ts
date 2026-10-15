import type { CompositeContext, Emit, PipelineNode, PortValues } from "@whiletrue-ai/core";
import { resolveOutcome } from "@whiletrue-ai/core";

/**
 * Runs children in order, feeding each child's output into the next child's input.
 * Stops at the first outcome that doesn't continue (fail, needs_human, rejected, ...)
 * and returns it unchanged for the parent to handle.
 */
export async function runChain(
  children: PipelineNode[],
  input: PortValues,
  ctx: CompositeContext<unknown>,
): Promise<Emit> {
  let current: Emit = { port: "ok", ...(input["in"] !== undefined && { value: input["in"] }) };
  for (const child of children) {
    if (ctx.signal.aborted) return { port: "fail", value: { error: "cancelled" } };
    const emit = await ctx.run(child, current.value === undefined ? {} : { in: current.value });
    if (resolveOutcome(emit.port, child.wires, ctx.policy).action !== "continue") return emit;
    // `skip` passes the previous value through untouched.
    if (emit.port !== "skip") current = emit;
  }
  return current;
}
