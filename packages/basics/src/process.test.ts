import { describe, expect, it } from "vitest";
import { runCommand, tail } from "./process.js";

const cwd = process.cwd();

describe("runCommand", () => {
  it("captures stdout, stderr and exit code", async () => {
    const r = await runCommand("sh", ["-c", "echo out; echo err >&2; exit 3"], { cwd });
    expect(r).toMatchObject({ code: 3, stdout: "out\n", stderr: "err\n", timedOut: false });
  });

  it("feeds stdin and merges env", async () => {
    const r = await runCommand("sh", ["-c", "cat; echo $FOO"], {
      cwd,
      env: { FOO: "bar" },
      stdin: "in:",
    });
    expect(r.stdout).toBe("in:bar\n");
  });

  it("kills on timeout", async () => {
    const r = await runCommand("sh", ["-c", "sleep 5"], { cwd, timeoutMs: 50 });
    expect(r.timedOut).toBe(true);
    expect(r.code).not.toBe(0);
  });

  it("clears the timer when the command finishes first", async () => {
    const r = await runCommand("true", [], { cwd, timeoutMs: 5000 });
    expect(r).toMatchObject({ code: 0, timedOut: false });
  });

  it("aborts via signal", async () => {
    const ctl = new AbortController();
    const p = runCommand("sh", ["-c", "sleep 5"], { cwd, signal: ctl.signal });
    ctl.abort();
    const r = await p;
    expect(r.aborted).toBe(true);
  });

  it("reports spawn failures", async () => {
    const r = await runCommand("definitely-not-a-command-xyz", [], { cwd, timeoutMs: 1000 });
    expect(r.code).toBeNull();
    expect(r.stderr).toMatch(/ENOENT/);
  });
});

describe("tail", () => {
  it("keeps the last n lines", () => {
    expect(tail("a\nb\nc\n", 2)).toBe("b\nc");
    expect(tail("a", 5)).toBe("a");
  });
});
