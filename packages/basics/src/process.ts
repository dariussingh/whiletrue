import { spawn } from "node:child_process";

export interface CommandResult {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  aborted: boolean;
}

export interface CommandOptions {
  cwd: string;
  env?: Record<string, string>;
  stdin?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

/** Runs a command to completion. Never rejects for non-zero exits; spawn errors are reported as code null. */
export function runCommand(
  command: string,
  args: string[],
  opts: CommandOptions,
): Promise<CommandResult> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const child = spawn(command, args, {
      cwd: opts.cwd,
      env: { ...process.env, ...opts.env },
      stdio: ["pipe", "pipe", "pipe"],
      ...(opts.signal && { signal: opts.signal }),
    });
    const timer =
      opts.timeoutMs !== undefined
        ? setTimeout(() => {
            timedOut = true;
            child.kill("SIGKILL");
          }, opts.timeoutMs)
        : undefined;
    child.stdout.on("data", (d: Buffer) => (stdout += d.toString()));
    child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
    child.stdin.on("error", () => {});
    child.on("error", (error) => {
      if (timer) clearTimeout(timer);
      resolve({
        code: null,
        stdout,
        stderr: stderr || error.message,
        timedOut,
        aborted: opts.signal?.aborted ?? false,
      });
    });
    child.on("close", (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code, stdout, stderr, timedOut, aborted: opts.signal?.aborted ?? false });
    });
    child.stdin.end(opts.stdin ?? "");
  });
}

/** Last `n` lines of text, for compact failure output. */
export function tail(text: string, n = 40): string {
  const lines = text.trimEnd().split("\n");
  return lines.slice(-n).join("\n");
}
