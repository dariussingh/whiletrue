import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { createStepContext } from "@whiletrue-ai/core";
import { gateShellModule, shellModule } from "../src/shell.js";

const dir = mkdtempSync(join(tmpdir(), "wt-shell-"));
writeFileSync(join(dir, "marker.txt"), "x");

describe("shell", () => {
  it("fires ok with stdout", async () => {
    const r = await shellModule.run({}, createStepContext({ run: "echo hi" }, { cwd: dir }));
    expect(r).toEqual({ port: "ok", value: "hi\n" });
  });

  it("uses cwd and env", async () => {
    const r = await shellModule.run(
      {},
      createStepContext({ run: "ls; echo $V", cwd: ".", env: { V: "1" } }, { cwd: dir }),
    );
    expect(r.value).toBe("marker.txt\n1\n");
  });

  it("fires fail with the exit code and output tail", async () => {
    const r = await shellModule.run(
      {},
      createStepContext({ run: "echo boom; exit 2" }, { cwd: dir }),
    );
    expect(r.port).toBe("fail");
    expect(r.value).toMatchObject({
      exit_code: 2,
      output: "boom",
      error: expect.stringContaining("code 2"),
    });
  });

  it("reports timeouts", async () => {
    const r = await shellModule.run(
      {},
      createStepContext({ run: "sleep 5", timeout: "50ms" }, { cwd: dir }),
    );
    expect(r.value).toMatchObject({ error: expect.stringContaining("timed out") });
  });

  it("reports aborts", async () => {
    const ctl = new AbortController();
    const p = shellModule.run(
      {},
      createStepContext({ run: "sleep 5" }, { cwd: dir, signal: ctl.signal }),
    );
    ctl.abort();
    expect((await p).value).toMatchObject({ error: expect.stringContaining("aborted") });
  });

  it("reports unknown exit codes (spawn failure)", async () => {
    const r = await shellModule.run(
      {},
      createStepContext({ run: "x" }, { cwd: "/definitely/not/here" }),
    );
    expect(r.value).toMatchObject({ error: expect.stringContaining("unknown") });
  });

  it("has an idempotency key and validates timeouts", () => {
    expect(
      shellModule.idempotencyKey?.(
        {},
        createStepContext({ run: "x" }, { runId: "r", itemId: "i", nodeId: "n" }),
      ),
    ).toBe("shell:r:i:n");
    const vctx = { cwd: dir, registry: undefined as never };
    expect(shellModule.validate?.({ run: "x", timeout: "1s" }, vctx)).toEqual([]);
    expect(shellModule.validate?.({ run: "x" }, vctx)).toEqual([]);
    expect(shellModule.validate?.({ run: "x", timeout: "soon" }, vctx)?.[0]?.message).toMatch(
      /Invalid duration/,
    );
  });
});

describe("gate.shell", () => {
  it("is a deterministic gate by exit code", async () => {
    expect(gateShellModule.effects).toBe("deterministic");
    expect(gateShellModule.idempotencyKey).toBeUndefined();
    expect(
      (await gateShellModule.run({}, createStepContext({ run: "true" }, { cwd: dir }))).port,
    ).toBe("ok");
    expect(
      (await gateShellModule.run({}, createStepContext({ run: "false" }, { cwd: dir }))).port,
    ).toBe("fail");
  });
});
