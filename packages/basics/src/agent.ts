import { resolve } from "node:path";
import { z } from "zod";
import {
  ConfigError,
  defineAction,
  loadAgentDefinition,
  renderTemplate,
  type AgentDefinition,
  type AgentRequest,
  type ConfigIssue,
  type Registry,
} from "@whiletrue-ai/core";
import { randomUUID } from "node:crypto";

const AgentConfig = z.object({
  uses: z.string().min(1),
  runner: z.string().optional(),
  vars: z.record(z.string(), z.unknown()).default({}),
});
type AgentConfig = z.infer<typeof AgentConfig>;

/** Caps that carry no useful JSON Schema constraint for structured output. */
const UNSTRUCTURED = new Set(["Text", "Any"]);

function runnerIssues(def: AgentDefinition, runnerId: string, registry: Registry): ConfigIssue[] {
  const runner = registry.getRunner(runnerId);
  if (!runner) {
    const known = [...["claude-code-headless", "opencode"]].join(", ");
    return [{ severity: "error", message: `Unknown runner "${runnerId}" (e.g. ${known})` }];
  }
  const issues: ConfigIssue[] = [];
  const outCap = Object.values(def.outputs)[0];
  if (outCap && !UNSTRUCTURED.has(outCap) && !runner.capabilities.structured_output) {
    issues.push({
      severity: "error",
      message: `Agent "${def.name}" declares structured output (${outCap}) but runner "${runnerId}" cannot produce it`,
    });
  }
  if (def.limits?.budget_usd !== undefined && !runner.capabilities.budget) {
    issues.push({
      severity: "error",
      message: `Agent "${def.name}" sets a budget but runner "${runnerId}" cannot enforce one`,
    });
  }
  if (def.session !== "fresh" && !runner.capabilities.resume) {
    issues.push({
      severity: "error",
      message: `Agent "${def.name}" asks for session "${def.session}" but runner "${runnerId}" cannot resume sessions`,
    });
  }
  return issues;
}

/** Runs a prompt-defined agent (markdown with frontmatter) on a runner. */
export const agentModule = defineAction<AgentConfig>({
  id: "agent",
  kind: "action",
  config: AgentConfig,
  ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Any" }] },
  effects: "agentic",
  validate(config, { cwd, registry }) {
    let def: AgentDefinition;
    try {
      def = loadAgentDefinition(resolve(cwd, config.uses));
    } catch (e) {
      const message =
        e instanceof ConfigError ? e.message : `Cannot read agent file: ${(e as Error).message}`;
      return [{ severity: "error", message }];
    }
    const issues: ConfigIssue[] = [];
    if (!def.limits) {
      issues.push({
        severity: "error",
        message: `Agent "${def.name}" needs \`limits\` (budget_usd, turns, wall): agent steps are bounded by default`,
      });
    }
    return [...issues, ...runnerIssues(def, config.runner ?? def.runner, registry)];
  },
  async run(input, ctx) {
    const def = loadAgentDefinition(resolve(ctx.cwd, ctx.config.uses));
    const runnerId = ctx.config.runner ?? def.runner;
    const runner = ctx.registry.getRunner(runnerId);
    if (!runner) return { port: "fail", value: { error: `Unknown runner "${runnerId}"` } };

    const prompt = renderTemplate(def.prompt, {
      ...ctx.scope.snapshot(),
      input: input["in"],
      ...ctx.config.vars,
    });

    const outCap = Object.values(def.outputs)[0];
    const structured = outCap !== undefined && !UNSTRUCTURED.has(outCap);
    const wallSignal =
      def.limits?.wall_ms !== undefined ? AbortSignal.timeout(def.limits.wall_ms) : undefined;
    const signal = wallSignal ? AbortSignal.any([ctx.signal, wallSignal]) : ctx.signal;

    const request: AgentRequest = {
      prompt,
      cwd: ctx.cwd,
      ...(def.model !== undefined && { model: def.model }),
      ...(def.tools !== undefined && { allowed_tools: def.tools }),
      ...(def.permission_mode !== undefined && { permission_mode: def.permission_mode }),
      ...(structured && { output_schema: ctx.registry.caps.toJsonSchema(outCap) }),
      ...(def.limits !== undefined && {
        limits: {
          ...(def.limits.budget_usd !== undefined && { budget_usd: def.limits.budget_usd }),
          ...(def.limits.turns !== undefined && { turns: def.limits.turns }),
          ...(def.limits.wall_ms !== undefined && { wall_ms: def.limits.wall_ms }),
        },
      }),
      session_id: randomUUID(),
    };

    const result = await runner.run(request, {
      signal,
      onEvent: (event) =>
        ctx.bus.emit({
          type: "agent.event",
          runId: ctx.runId,
          itemId: ctx.itemId,
          data: { node: ctx.nodeId, ...event },
        }),
    });
    if (result.cost_usd) ctx.recordCost(result.cost_usd);
    if (!result.ok) {
      return { port: "fail", value: { error: result.error ?? "agent failed", text: result.text } };
    }
    const value = result.output ?? result.text;
    if (outCap) {
      const check = ctx.registry.caps.validate(outCap, value);
      if (!check.ok) {
        return {
          port: "fail",
          value: { error: `Agent output does not match ${outCap}: ${check.errors.join("; ")}` },
        };
      }
    }
    return { port: "ok", value };
  },
});
