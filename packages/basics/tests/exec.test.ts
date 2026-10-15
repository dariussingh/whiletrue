import { mkdtempSync, writeFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { createStepContext } from "@whiletrue-ai/core";
import { execModule } from "../src/exec.js";

const dir = mkdtempSync(join(tmpdir(), "wt-exec-"));
const script = (name: string, body: string) => {
  const p = join(dir, name);
  writeFileSync(p, `#!/bin/sh\n${body}\n`);
  chmodSync(p, 0o755);
  return p;
};
const run = (command: string, input: unknown = "in") =>
  execModule.run(
    { in: input },
    createStepContext(
      { command, args: [] as string[] },
      { cwd: dir, runId: "r", itemId: "i", nodeId: "n" },
    ),
  );

describe("exec", () => {
  it("round-trips JSON over stdio", async () => {
    const p = script("echo.sh", 'cat > in.json; echo \'{"port":"ok","value":{"got":true}}\'');
    expect(await run(p)).toEqual({ port: "ok", value: { got: true } });
  });

  it("passes input, config, item and step on stdin", async () => {
    const p = script("dump.sh", 'v=$(cat); printf \'{"value":%s}\' "$v"');
    const r = await run(p, { n: 1 });
    expect(r.port).toBe("ok");
    expect(r.value).toMatchObject({ input: { n: 1 }, step: { id: "n", key: "r:i:n", attempt: 1 } });
  });

  it("defaults the port to ok and lets the program choose another", async () => {
    expect(await run(script("a.sh", "echo '{}'"))).toEqual({ port: "ok" });
    expect((await run(script("b.sh", 'echo \'{"port":"needs_human"}\''))).port).toBe("needs_human");
  });

  it("fails on non-zero exit, bad JSON and bad shape", async () => {
    const bad = await run(script("c.sh", "echo oops; exit 4"));
    expect(bad).toMatchObject({
      port: "fail",
      value: { error: expect.stringContaining("code 4"), output: "oops" },
    });
    expect((await run(script("d.sh", "echo not-json"))).value).toMatchObject({
      error: expect.stringContaining("valid JSON"),
    });
    expect((await run(script("e.sh", "echo '[1]'"))).value).toMatchObject({
      error: expect.stringContaining("reply must be"),
    });
  });

  it("reports spawn failures as unknown exit code", async () => {
    expect((await run("./missing-command")).value).toMatchObject({
      error: expect.stringContaining("unknown"),
    });
  });

  it("applies cwd, env and timeout", async () => {
    const p = script("env.sh", 'cat >/dev/null; printf \'{"value":"%s"}\' "$MY"');
    const r = await execModule.run(
      {},
      createStepContext(
        { command: p, args: [] as string[], env: { MY: "x" }, cwd: ".", timeout: "5s" },
        { cwd: dir },
      ),
    );
    expect(r.value).toBe("x");
  });

  it("has an idempotency key", () => {
    expect(
      execModule.idempotencyKey?.(
        {},
        createStepContext(
          { command: "x", args: [] as string[] },
          { runId: "r", itemId: "i", nodeId: "n" },
        ),
      ),
    ).toBe("exec:r:i:n");
  });
});
