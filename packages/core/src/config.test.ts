import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ConfigError,
  loadAgentDefinition,
  loadPipeline,
  parseAgentDefinition,
  parsePipeline,
} from "./config.js";

const err = (fn: () => unknown): ConfigError => {
  try {
    fn();
  } catch (e) {
    expect(e).toBeInstanceOf(ConfigError);
    return e as ConfigError;
  }
  throw new Error("expected ConfigError");
};

describe("parsePipeline", () => {
  it("parses a list pipeline into an implicit sequence", () => {
    const cfg = parsePipeline(
      `name: demo
pipeline:
  - shell: { run: "echo 1" }
  - id: second
    gate.shell: { run: "true" }
`,
      "w.yaml",
    );
    expect(cfg.name).toBe("demo");
    expect(cfg.on_idle).toBe("exit");
    expect(cfg.on_fail).toBe("park");
    expect(cfg.root).toMatchObject({ id: "root", use: "sequence" });
    expect(cfg.root.children.map((c) => [c.id, c.use])).toEqual([
      ["root.0", "shell"],
      ["second", "gate.shell"],
    ]);
    expect(cfg.root.children[0]!.config).toEqual({ run: "echo 1" });
  });

  it("parses a strategy root and hoists do from inside the config", () => {
    const cfg = parsePipeline(
      `pipeline:
  loop:
    max: 3
    do:
      - shell: { run: "a" }
`,
      "w.yaml",
    );
    expect(cfg.root.use).toBe("loop");
    expect(cfg.root.config).toEqual({ max: 3 });
    expect(cfg.root.children).toHaveLength(1);
    expect(cfg.root.children[0]!.id).toBe("root.0");
  });

  it("accepts a sibling do and wires", () => {
    const cfg = parsePipeline(
      `pipeline:
  - id: a
    retry: { max: 2 }
    do:
      - shell: { run: x }
    wires: { fail: b }
  - id: b
    shell: { run: y }
`,
      "w.yaml",
    );
    const a = cfg.root.children[0]!;
    expect(a.children).toHaveLength(1);
    expect(a.wires).toEqual({ fail: "b" });
  });

  it("keeps passthrough keys in extra and uses/on_idle/on_fail", () => {
    const cfg = parsePipeline(
      `uses: ["@whiletrue-ai/git"]
on_idle: watch
on_fail: drop
source: { json: { path: a.json } }
pipeline: [ { shell: { run: x } } ]
`,
      join(tmpdir(), "proj", "w.yaml"),
    );
    expect(cfg.uses).toEqual(["@whiletrue-ai/git"]);
    expect(cfg.on_idle).toBe("watch");
    expect(cfg.on_fail).toBe("drop");
    expect(cfg.extra).toEqual({ source: { json: { path: "a.json" } } });
    expect(cfg.name).toBe("proj");
  });

  it("reports YAML syntax errors with file and line", () => {
    const e = err(() => parsePipeline("pipeline:\n  - shell: {run: [\n", "bad.yaml"));
    expect(e.file).toBe("bad.yaml");
    expect(e.line).toBeGreaterThanOrEqual(2);
  });

  it("requires a mapping and a pipeline", () => {
    expect(err(() => parsePipeline("- a\n", "w.yaml")).message).toMatch(/must be a mapping/);
    expect(err(() => parsePipeline("name: x\n", "w.yaml")).message).toMatch(/Missing required key/);
  });

  it("reports schema errors at the offending line", () => {
    const e = err(() =>
      parsePipeline("name: x\non_idle: sometimes\npipeline: [ {shell: {run: x}} ]\n", "w.yaml"),
    );
    expect(e.line).toBe(2);
    expect(e.message).toContain("on_idle");
  });

  it("rejects malformed steps with locations", () => {
    expect(err(() => parsePipeline("pipeline:\n  - 5\n", "w.yaml")).line).toBe(2);
    expect(err(() => parsePipeline("pipeline:\n  - id: x\n", "w.yaml")).message).toMatch(
      /needs a module key/,
    );
    expect(
      err(() => parsePipeline("pipeline:\n  - { shell: {}, exec: {} }\n", "w.yaml")).message,
    ).toMatch(/exactly one module key/);
    expect(err(() => parsePipeline("pipeline:\n  - shell: 5\n", "w.yaml")).message).toMatch(
      /must be a mapping/,
    );
    expect(
      err(() => parsePipeline("pipeline:\n  - { shell: {}, do: 3 }\n", "w.yaml")).message,
    ).toMatch(/list of steps/);
    expect(
      err(() => parsePipeline("pipeline:\n  - { shell: {}, id: '' }\n", "w.yaml")).message,
    ).toMatch(/non-empty/);
    expect(
      err(() => parsePipeline("pipeline:\n  - { shell: {}, wires: [1] }\n", "w.yaml")).message,
    ).toMatch(/wires/);
  });

  it("rejects duplicate ids and unknown wire targets", () => {
    const dup = err(() =>
      parsePipeline("pipeline:\n  - { id: a, shell: {} }\n  - { id: a, shell: {} }\n", "w.yaml"),
    );
    expect(dup.message).toMatch(/Duplicate step id "a" \(first defined at line 2\)/);
    const wire = err(() =>
      parsePipeline("pipeline:\n  - { id: a, shell: {}, wires: { fail: zzz } }\n", "w.yaml"),
    );
    expect(wire.message).toMatch(/unknown sibling step "zzz"/);
  });
});

describe("loadPipeline", () => {
  it("reads a file from disk", () => {
    const dir = mkdtempSync(join(tmpdir(), "wt-"));
    const path = join(dir, "whiletrue.yaml");
    writeFileSync(path, "pipeline: [ { shell: { run: x } } ]\n");
    expect(loadPipeline(path).root.children).toHaveLength(1);
  });
});

const AGENT = `---
agent: implement
runner: claude-code-headless
model: opus
tools: [Read, Edit]
permission_mode: acceptEdits
inputs: { issue: Issue }
outputs: { result: Verdict }
limits: { budget_usd: 8, turns: 120, wall: 90m }
---
Implement {{issue.title}}.
`;

describe("parseAgentDefinition", () => {
  it("parses frontmatter and prompt body", () => {
    const def = parseAgentDefinition(AGENT, "a.md");
    expect(def).toMatchObject({
      name: "implement",
      runner: "claude-code-headless",
      model: "opus",
      session: "fresh",
      tools: ["Read", "Edit"],
      permission_mode: "acceptEdits",
      inputs: { issue: "Issue" },
      outputs: { result: "Verdict" },
      limits: { budget_usd: 8, turns: 120, wall_ms: 5_400_000 },
      prompt: "Implement {{issue.title}}.",
    });
  });

  it("applies defaults for a minimal agent", () => {
    const def = parseAgentDefinition("---\nagent: x\n---\nhi", "a.md");
    expect(def).toMatchObject({ runner: "claude-code-headless", session: "fresh", prompt: "hi" });
    expect(def.limits).toBeUndefined();
    expect(def.model).toBeUndefined();
  });

  it("supports partial limits", () => {
    const def = parseAgentDefinition("---\nagent: x\nlimits: { turns: 5 }\n---\nhi", "a.md");
    expect(def.limits).toEqual({ turns: 5 });
  });

  it("requires frontmatter", () => {
    const e = err(() => parseAgentDefinition("no frontmatter", "a.md"));
    expect(e.line).toBe(1);
  });

  it("cites file lines (offset past the --- fence) for schema errors", () => {
    const e = err(() =>
      parseAgentDefinition("---\nagent: x\nlimits: { turns: -1 }\n---\nhi", "a.md"),
    );
    expect(e.file).toBe("a.md");
    expect(e.line).toBe(3);
  });

  it("rejects bad wall durations and non-mapping frontmatter", () => {
    expect(
      err(() => parseAgentDefinition("---\nagent: x\nlimits: { wall: soon }\n---\n", "a.md"))
        .message,
    ).toMatch(/Invalid duration/);
    expect(err(() => parseAgentDefinition("---\n- a\n---\n", "a.md")).message).toMatch(
      /must be a mapping/,
    );
  });

  it("reports YAML errors in frontmatter on the right line", () => {
    const e = err(() => parseAgentDefinition("---\nagent: x\nmodel: [\n---\nhi", "a.md"));
    expect(e.line).toBeGreaterThanOrEqual(3);
  });

  it("loads from disk", () => {
    const dir = mkdtempSync(join(tmpdir(), "wt-"));
    const path = join(dir, "a.md");
    writeFileSync(path, AGENT);
    expect(loadAgentDefinition(path).name).toBe("implement");
  });
});
