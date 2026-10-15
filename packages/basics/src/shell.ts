import { resolve } from "node:path";
import { z } from "zod";
import { defineAction, parseDuration, type ActionModule } from "@whiletrue-ai/core";
import { runCommand, tail } from "./process.js";

const ShellConfig = z.object({
  run: z.string().min(1),
  cwd: z.string().optional(),
  env: z.record(z.string(), z.string()).optional(),
  timeout: z.union([z.string(), z.number()]).optional(),
});
type ShellConfig = z.infer<typeof ShellConfig>;

function makeShell(
  id: string,
  effects: "side-effect" | "deterministic",
): ActionModule<ShellConfig> {
  return defineAction<ShellConfig>({
    id,
    kind: "action",
    config: ShellConfig,
    ports: { in: [{ name: "in", cap: "Any" }], out: [{ name: "out", cap: "Text" }] },
    effects,
    ...(effects === "side-effect" && { idempotencyKey: (_input, ctx) => `shell:${ctx.stepKey}` }),
    validate(config) {
      if (config.timeout === undefined) return [];
      try {
        parseDuration(config.timeout);
        return [];
      } catch (e) {
        return [{ severity: "error", message: (e as Error).message }];
      }
    },
    async run(_input, ctx) {
      const result = await runCommand("sh", ["-c", ctx.config.run], {
        cwd: resolve(ctx.cwd, ctx.config.cwd ?? "."),
        ...(ctx.config.env && { env: ctx.config.env }),
        signal: ctx.signal,
        ...(ctx.config.timeout !== undefined && { timeoutMs: parseDuration(ctx.config.timeout) }),
      });
      if (result.code === 0) return { port: "ok", value: result.stdout };
      const reason = result.timedOut
        ? "timed out"
        : result.aborted
          ? "aborted"
          : `exited with code ${result.code ?? "unknown"}`;
      return {
        port: "fail",
        value: {
          error: `\`${ctx.config.run}\` ${reason}`,
          exit_code: result.code,
          output: tail(result.stdout + result.stderr),
        },
      };
    },
  });
}

/** Runs a shell command. Fires `ok` on exit 0, otherwise `fail` with the output tail. */
export const shellModule = makeShell("shell", "side-effect");
/** A deterministic gate: pass/fail by exit code only. */
export const gateShellModule = makeShell("gate.shell", "deterministic");
