import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import type { AgentRequest, AgentResult, RunContext, Runner } from "@whiletrue-ai/core";

export interface HeadlessOptions {
  /** The claude binary. Defaults to `claude` on PATH. */
  command?: string;
  /** Extra args appended after the generated ones (e.g. `--bare` for reproducible runs). */
  extraArgs?: string[];
}

/** Builds the argv for `claude -p`. The prompt itself goes over stdin. */
export function buildHeadlessArgs(req: AgentRequest, extraArgs: string[] = []): string[] {
  const args = [
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-prompts",
    "none",
  ];
  if (req.model) args.push("--model", req.model);
  if (req.allowed_tools?.length) args.push("--allowedTools", req.allowed_tools.join(","));
  if (req.disallowed_tools?.length) args.push("--disallowedTools", req.disallowed_tools.join(","));
  if (req.tools?.length) args.push("--tools", req.tools.join(","));
  if (req.permission_mode) args.push("--permission-mode", req.permission_mode);
  if (req.system_prompt_append) args.push("--append-system-prompt", req.system_prompt_append);
  if (req.output_schema) args.push("--json-schema", JSON.stringify(req.output_schema));
  if (req.limits?.turns !== undefined) args.push("--max-turns", String(req.limits.turns));
  if (req.limits?.budget_usd !== undefined) {
    args.push("--max-budget-usd", String(req.limits.budget_usd));
  }
  if (req.resume) args.push("--resume", req.resume);
  else if (req.session_id) args.push("--session-id", req.session_id);
  return [...args, ...extraArgs];
}

interface StreamMessage {
  type?: string;
  subtype?: string;
  is_error?: boolean;
  result?: string;
  structured_output?: unknown;
  total_cost_usd?: number;
  num_turns?: number;
  session_id?: string;
  errors?: string[];
  message?: { content?: { type: string; name?: string; text?: string }[] };
}

function toResult(msg: StreamMessage): AgentResult {
  const ok = msg.is_error !== true && msg.subtype === "success";
  return {
    ok,
    text: msg.result ?? "",
    ...(msg.structured_output !== undefined && { output: msg.structured_output }),
    ...(msg.session_id !== undefined && { session_id: msg.session_id }),
    ...(msg.total_cost_usd !== undefined && { cost_usd: msg.total_cost_usd }),
    ...(msg.num_turns !== undefined && { turns: msg.num_turns }),
    ...(!ok && {
      error: msg.errors?.join("; ") || msg.subtype || "claude reported an error",
    }),
  };
}

/** Unattended runner: `claude -p --output-format stream-json`. For CI and cheap utility steps. */
export function createHeadlessRunner(options: HeadlessOptions = {}): Runner {
  const command = options.command ?? "claude";
  return {
    id: "claude-code-headless",
    capabilities: {
      interactive: false,
      structured_output: true,
      resume: true,
      fork: false,
      budget: true,
    },
    run(req: AgentRequest, ctx: RunContext): Promise<AgentResult> {
      return new Promise((resolve) => {
        let final: AgentResult | undefined;
        let stderr = "";
        let settled = false;
        if (ctx.signal.aborted) return resolve({ ok: false, text: "", error: "aborted" });

        const child = spawn(command, buildHeadlessArgs(req, options.extraArgs), {
          cwd: req.cwd,
          stdio: ["pipe", "pipe", "pipe"],
          detached: true,
        });
        // Kill the whole process group so grandchildren cannot keep our pipes open.
        const kill = () => {
          try {
            if (child.pid !== undefined) process.kill(-child.pid, "SIGKILL");
          } catch {
            child.kill("SIGKILL");
          }
        };
        const finish = (result: AgentResult) => {
          if (settled) return;
          settled = true;
          if (timer) clearTimeout(timer);
          ctx.signal.removeEventListener("abort", onAbort);
          resolve(result);
        };
        const onAbort = () => {
          kill();
          finish({ ok: false, text: "", error: "aborted" });
        };
        const timer =
          req.limits?.wall_ms !== undefined
            ? setTimeout(() => {
                kill();
                finish({ ok: false, text: "", error: "wall-time limit exceeded" });
              }, req.limits.wall_ms)
            : undefined;
        ctx.signal.addEventListener("abort", onAbort, { once: true });
        child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
        child.stdin.on("error", () => {});

        createInterface({ input: child.stdout }).on("line", (line) => {
          let msg: StreamMessage;
          try {
            msg = JSON.parse(line) as StreamMessage;
          } catch {
            return;
          }
          if (msg.type === "result") {
            final = toResult(msg);
            ctx.onEvent?.({ kind: "result", subtype: msg.subtype ?? null });
          } else if (msg.type === "assistant") {
            for (const block of msg.message?.content ?? []) {
              if (block.type === "tool_use")
                ctx.onEvent?.({ kind: "tool_use", name: block.name ?? "" });
              else if (block.type === "text") ctx.onEvent?.({ kind: "text" });
            }
          }
        });

        child.on("error", (error) =>
          finish({
            ok: false,
            text: "",
            error: `Failed to start ${command}: ${error.message}`,
          }),
        );
        child.on("close", (code) => {
          if (final) return finish(final);
          finish({
            ok: false,
            text: "",
            error: `${command} exited with code ${code ?? "unknown"} without a result${stderr ? `: ${stderr.trim().slice(-500)}` : ""}`,
          });
        });
        child.stdin.end(req.prompt);
      });
    },
  };
}
