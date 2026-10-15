import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { EXIT, main } from "./cli.js";
import { createDefaultRegistry, loadPlugins } from "./registry.js";

let dir: string;
let out: string;
let err: string;
const io = (signal?: AbortSignal) => ({
  cwd: dir,
  out: (t: string) => (out += t),
  err: (t: string) => (err += t),
  ...(signal && { signal }),
});
const cli = (...args: string[]) => main(args, io());

const SUCCESS = (cost = 0.01) =>
  `echo '{"type":"result","subtype":"success","is_error":false,"result":"ok","total_cost_usd":${cost},"num_turns":1,"session_id":"s"}'`;

/** A fake `claude` that "fixes the bug" (touches done.flag) on its Nth invocation. */
function fakeClaude(fixOnAttempt: number): string {
  const p = join(dir, "fake-claude.sh");
  writeFileSync(
    p,
    `#!/bin/sh
n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count
cat > "prompt-$n.txt"
[ "$n" -ge ${fixOnAttempt} ] && touch done.flag
${SUCCESS()}
`,
  );
  chmodSync(p, 0o755);
  return p;
}

const RALPH_YAML = (max = 5) => `name: ralph
pipeline:
  loop:
    max: ${max}
    do:
      - agent: { uses: ./agents/ralph.md }
      - gate.shell: { run: "test -f done.flag || (echo 'done.flag missing'; exit 1)" }
`;

function project(max = 5) {
  writeFileSync(join(dir, "whiletrue.yaml"), RALPH_YAML(max));
  mkdirSync(join(dir, "agents"), { recursive: true });
  writeFileSync(
    join(dir, "agents", "ralph.md"),
    "---\nagent: ralph\nlimits: { budget_usd: 1, turns: 5, wall: 1m }\n---\nFix it. Previous: {{loop.previous}}\n",
  );
}

let savedBin: string | undefined;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "wt-cli-"));
  out = "";
  err = "";
  savedBin = process.env["WHILETRUE_CLAUDE_BIN"];
});
afterEach(() => {
  if (savedBin === undefined) delete process.env["WHILETRUE_CLAUDE_BIN"];
  else process.env["WHILETRUE_CLAUDE_BIN"] = savedBin;
});

describe("usage", () => {
  it("prints help", async () => {
    expect(await cli("--help")).toBe(EXIT.ok);
    expect(out).toContain("Usage: whiletrue");
  });

  it("shows usage on stderr when no command is given", async () => {
    expect(await cli()).toBe(EXIT.usage);
    expect(err).toContain("Usage: whiletrue");
  });

  it("rejects unknown commands and options", async () => {
    expect(await cli("frobnicate")).toBe(EXIT.usage);
    expect(err).toContain('Unknown command "frobnicate"');
    err = "";
    expect(await cli("run", "--nope")).toBe(EXIT.usage);
    expect(err).toMatch(/nope/);
  });
});

describe("init", () => {
  it("scaffolds a Ralph pipeline that validates", async () => {
    expect(await cli("init")).toBe(EXIT.ok);
    expect(existsSync(join(dir, "whiletrue.yaml"))).toBe(true);
    expect(existsSync(join(dir, "agents", "ralph.md"))).toBe(true);
    expect(await cli("validate")).toBe(EXIT.ok);
  });

  it("refuses to overwrite without --force", async () => {
    await cli("init");
    writeFileSync(join(dir, "whiletrue.yaml"), "custom");
    err = "";
    expect(await cli("init")).toBe(EXIT.failed);
    expect(err).toContain("Refusing to overwrite whiletrue.yaml");
    expect(readFileSync(join(dir, "whiletrue.yaml"), "utf8")).toBe("custom");
    expect(await cli("init", "--force")).toBe(EXIT.ok);
    expect(readFileSync(join(dir, "whiletrue.yaml"), "utf8")).toContain("name: ralph");
  });
});

describe("validate and plan", () => {
  it("validate reports ok", async () => {
    project();
    expect(await cli("validate")).toBe(EXIT.ok);
    expect(out).toContain("whiletrue.yaml: ok");
  });

  it("validate reports diagnostics and fails", async () => {
    project();
    writeFileSync(
      join(dir, "whiletrue.yaml"),
      "pipeline:\n  - nope: {}\n  - loop: { do: [ { shell: { run: x } } ] }\n",
    );
    expect(await cli("validate")).toBe(EXIT.failed);
    expect(err).toMatch(/whiletrue\.yaml:2:\d+: error: Unknown module "nope"/);
    expect(err).toContain("requires `max`");
  });

  it("reports config syntax errors and missing files", async () => {
    expect(await cli("validate")).toBe(EXIT.failed);
    expect(err).toContain("Cannot load whiletrue.yaml");
    err = "";
    writeFileSync(join(dir, "whiletrue.yaml"), "pipeline: [\n");
    expect(await cli("validate")).toBe(EXIT.failed);
    expect(err).toMatch(/whiletrue\.yaml:\d+:\d+:/);
  });

  it("supports --file", async () => {
    writeFileSync(join(dir, "alt.yaml"), "pipeline: [ { shell: { run: 'true' } } ]\n");
    expect(await cli("validate", "-f", "alt.yaml")).toBe(EXIT.ok);
  });

  it("plan prints the expanded tree without running anything", async () => {
    project();
    expect(await cli("plan")).toBe(EXIT.ok);
    expect(out).toContain("ralph\n");
    expect(out).toMatch(/loop \{"max":5,"stop_when":"ok"\}|loop \{"max":5\}/);
    expect(out).toContain("├─ agent");
    expect(out).toContain("└─ gate.shell");
    expect(existsSync(join(dir, ".whiletrue"))).toBe(false);
  });

  it("plan fails on invalid config", async () => {
    writeFileSync(join(dir, "whiletrue.yaml"), "pipeline: [ { nope: {} } ]\n");
    expect(await cli("plan")).toBe(EXIT.failed);
  });
});

describe("run", () => {
  it("M0 exit: a bounded Ralph loop runs on the headless runner until the gate passes", async () => {
    project();
    process.env["WHILETRUE_CLAUDE_BIN"] = fakeClaude(3);
    expect(await cli("run")).toBe(EXIT.ok);
    expect(out).toMatch(/run [0-9a-f-]{36}: done/);
    expect(out).toContain("main: done (ok)");
    expect(out).toContain("loop iteration 3/5");
    // Fresh session per attempt, with the previous failure fed back in.
    expect(readFileSync(join(dir, "prompt-1.txt"), "utf8")).toBe("Fix it. Previous: ");
    expect(readFileSync(join(dir, "prompt-2.txt"), "utf8")).toContain("done.flag missing");
    expect(readFileSync(join(dir, "count"), "utf8").trim()).toBe("3");
  });

  it("status shows the run, steps and cost", async () => {
    project();
    process.env["WHILETRUE_CLAUDE_BIN"] = fakeClaude(2);
    await cli("run");
    out = "";
    expect(await cli("status")).toBe(EXIT.ok);
    expect(out).toMatch(/run [0-9a-f-]{36} {2}done/);
    expect(out).toContain("main  done  steps=");
    expect(out).toContain("cost=$0.0200");
    expect(out).toMatch(/root\.1#1 {2}completed {2}fail/);
    const runId = /run ([0-9a-f-]{36})/.exec(out)![1]!;
    out = "";
    expect(await cli("status", runId)).toBe(EXIT.ok);
    expect(out).toContain(runId);
  });

  it("parks the item (exit 2) when the loop is exhausted", async () => {
    project(2);
    process.env["WHILETRUE_CLAUDE_BIN"] = fakeClaude(99);
    expect(await cli("run")).toBe(EXIT.parked);
    expect(out).toContain("main: parked (fail)");
  });

  it("fails fast when validation fails, without creating state", async () => {
    writeFileSync(join(dir, "whiletrue.yaml"), "pipeline: [ { nope: {} } ]\n");
    expect(await cli("run")).toBe(EXIT.failed);
    expect(existsSync(join(dir, ".whiletrue"))).toBe(false);
  });

  it("returns exit 1 when the run fails outright", async () => {
    writeFileSync(
      join(dir, "whiletrue.yaml"),
      "on_fail: drop\npipeline: [ { shell: { run: 'exit 3' } } ]\n",
    );
    expect(await cli("run")).toBe(EXIT.failed);
    expect(out).toContain("main: failed (fail)");
  });

  it("returns 130 when cancelled", async () => {
    project();
    const ctl = new AbortController();
    ctl.abort();
    expect(await main(["run"], io(ctl.signal))).toBe(EXIT.cancelled);
  });

  it("forwards non-debug logs to the output", async () => {
    writeFileSync(
      join(dir, "whiletrue.yaml"),
      "pipeline: { loop: { max: 1, do: [ { shell: { run: 'true' } } ] } }\n",
    );
    await cli("run");
    expect(out).toContain("[info] loop iteration 1/1");
  });
});

describe("status without runs", () => {
  it("reports a missing database and unknown runs", async () => {
    expect(await cli("status")).toBe(EXIT.failed);
    expect(err).toContain("No runs yet");
    writeFileSync(join(dir, "whiletrue.yaml"), "pipeline: [ { shell: { run: 'true' } } ]\n");
    await cli("run");
    err = "";
    expect(await cli("status", "nope")).toBe(EXIT.failed);
    expect(err).toContain('No run "nope"');
  });
});

describe("plugins", () => {
  const writePlugin = (name: string, source: string) => {
    const root = join(dir, "node_modules", name);
    mkdirSync(root, { recursive: true });
    writeFileSync(
      join(root, "package.json"),
      JSON.stringify({ name, main: "index.js", type: "commonjs" }),
    );
    writeFileSync(join(root, "index.js"), source);
  };

  it("loads project plugins named in uses:", async () => {
    writePlugin(
      "demo-plugin",
      `module.exports = { default: { name: "demo-plugin", register(api) {
        api.registerAction({ id: "demo.hi", kind: "action", config: { safeParse: (v) => ({ success: true, data: v }) },
          ports: { in: [], out: [] }, effects: "pure", run: async () => ({ port: "ok" }) });
      } } };`,
    );
    writeFileSync(
      join(dir, "whiletrue.yaml"),
      'uses: ["demo-plugin"]\npipeline: [ { demo.hi: {} } ]\n',
    );
    expect(await cli("validate")).toBe(EXIT.ok);
  });

  it("reports missing plugins and bad exports", async () => {
    writeFileSync(
      join(dir, "whiletrue.yaml"),
      'uses: ["not-installed"]\npipeline: [ { shell: { run: x } } ]\n',
    );
    expect(await cli("validate")).toBe(EXIT.failed);
    expect(err).toContain('Cannot find plugin "not-installed"');
    err = "";
    writePlugin("bad-plugin", "module.exports = { default: 42 };");
    writeFileSync(
      join(dir, "whiletrue.yaml"),
      'uses: ["bad-plugin"]\npipeline: [ { shell: { run: x } } ]\n',
    );
    expect(await cli("validate")).toBe(EXIT.failed);
    expect(err).toContain("must export a Plugin");
  });

  it("accepts a named `plugin` export and skips builtin names", async () => {
    writePlugin("named-plugin", 'exports.plugin = { name: "named-plugin", register() {} };');
    const registry = createDefaultRegistry({ claudeCommand: "x" });
    await loadPlugins(registry, ["@whiletrue-ai/basics", "named-plugin"], dir);
    expect(registry.plugins()).toContain("named-plugin");
  });
});
