import { resolve } from "node:path";
import { z } from "zod";
import { defineAction, parseDuration } from "@whiletrue-ai/core";
import { runCommand, tail } from "./process.js";

const ExecConfig = z.object({
  command: z.string().min(1),
  args: z.array(z.string()).default([]),
  cwd: z.string().optional(),
  env: z.record(z.string(), z.string()).optional(),
  timeout: z.union([z.string(), z.number()]).optional(),
});

const Reply = z.object({ port: z.string().optional(), value: z.unknown().optional() });

/**
 * Escape hatch: runs any executable that speaks JSON over stdio, so a module can be written in
 * any language. Receives `{ input, config, item, step }` on stdin; replies `{ port?, value? }`.
 */
export const execModule = defineAction({
  id: "exec",
  kind: "action",
  config: ExecConfig,
  ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Any" }] },
  effects: "side-effect",
  idempotencyKey: (_input, ctx) => `exec:${ctx.stepKey}`,
  async run(input, ctx) {
    const { command, args, cwd, env, timeout } = ctx.config;
    const result = await runCommand(command, args, {
      cwd: resolve(ctx.cwd, cwd ?? "."),
      ...(env && { env }),
      signal: ctx.signal,
      ...(timeout !== undefined && { timeoutMs: parseDuration(timeout) }),
      stdin: JSON.stringify({
        input: input["in"],
        config: ctx.config,
        item: ctx.scope.get("item"),
        step: { id: ctx.nodeId, key: ctx.stepKey, attempt: ctx.attempt },
      }),
    });
    if (result.code !== 0) {
      return {
        port: "fail",
        value: {
          error: `\`${command}\` exited with code ${result.code ?? "unknown"}`,
          output: tail(result.stdout + result.stderr),
        },
      };
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(result.stdout);
    } catch {
      return {
        port: "fail",
        value: { error: `\`${command}\` did not print valid JSON on stdout` },
      };
    }
    const reply = Reply.safeParse(parsed);
    if (!reply.success) {
      return { port: "fail", value: { error: `\`${command}\` reply must be { port?, value? }` } };
    }
    return {
      port: reply.data.port ?? "ok",
      ...(reply.data.value !== undefined && { value: reply.data.value }),
    };
  },
});
