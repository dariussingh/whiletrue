import { readFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { LineCounter, parseDocument, type Document } from "yaml";
import { z } from "zod";
import { parseDuration } from "./duration.js";
import type { OnFail } from "./outcomes.js";

export interface SourceLoc {
  file: string;
  line: number;
  col: number;
}

export class ConfigError extends Error {
  readonly file: string;
  readonly line: number;
  readonly col: number;
  constructor(loc: SourceLoc, message: string) {
    super(`${loc.file}:${loc.line}:${loc.col}: ${message}`);
    this.name = "ConfigError";
    this.file = loc.file;
    this.line = loc.line;
    this.col = loc.col;
  }
}

export interface PipelineNode {
  id: string;
  /** Module or strategy id, e.g. "shell" or "loop". */
  use: string;
  config: Record<string, unknown>;
  children: PipelineNode[];
  /** Outcome/data port -> sibling node id. */
  wires: Record<string, string>;
  loc: SourceLoc;
}

export interface PipelineConfig {
  file: string;
  name: string;
  uses: string[];
  on_idle: "exit" | "watch";
  on_fail: OnFail;
  /** Passed through for providers (source, comments, ...). */
  extra: Record<string, unknown>;
  root: PipelineNode;
}

type Path = (string | number)[];

class Located {
  readonly lines = new LineCounter();
  readonly doc: Document.Parsed;
  constructor(
    readonly text: string,
    readonly file: string,
    private readonly lineOffset = 0,
  ) {
    this.doc = parseDocument(text, { lineCounter: this.lines, prettyErrors: false });
    const first = this.doc.errors[0];
    if (first) {
      const pos = this.lines.linePos(first.pos[0]);
      throw new ConfigError(
        { file, line: pos.line + lineOffset, col: pos.col },
        first.message.split("\n")[0] ?? "YAML syntax error",
      );
    }
  }

  loc(path: Path): SourceLoc {
    let node: unknown = this.doc.getIn(path, true);
    // Fall back to the nearest existing ancestor.
    for (let i = path.length; node == null && i > 0; i--) {
      node = this.doc.getIn(path.slice(0, i - 1), true);
    }
    const range = (node as { range?: [number, number, number] } | null | undefined)?.range;
    const pos = range ? this.lines.linePos(range[0]) : { line: 1, col: 1 };
    return { file: this.file, line: pos.line + this.lineOffset, col: pos.col };
  }

  fail(path: Path, message: string): never {
    throw new ConfigError(this.loc(path), message);
  }

  zod<T>(schema: z.ZodType<T>, value: unknown, base: Path = []): T {
    const r = schema.safeParse(value);
    if (r.success) return r.data;
    const issue = r.error.issues[0]!;
    const path = [
      ...base,
      ...issue.path.filter((p): p is string | number => typeof p !== "symbol"),
    ];
    const where = issue.path.length ? ` at "${issue.path.join(".")}"` : "";
    return this.fail(path, `${issue.message}${where}`);
  }
}

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v);

const RESERVED = new Set(["id", "wires", "do"]);

const TopLevel = z
  .object({
    name: z.string().min(1).optional(),
    uses: z.array(z.string()).default([]),
    on_idle: z.enum(["exit", "watch"]).default("exit"),
    on_fail: z.enum(["park", "drop", "propagate"]).default("park"),
    pipeline: z.unknown().optional(),
  })
  .passthrough();

function buildNode(
  l: Located,
  value: unknown,
  path: Path,
  id: string,
  seen: Map<string, SourceLoc>,
): PipelineNode {
  if (!isPlainObject(value)) l.fail(path, "A pipeline step must be a mapping");
  const keys = Object.keys(value).filter((k) => !RESERVED.has(k));
  if (keys.length !== 1) {
    l.fail(
      path,
      keys.length === 0
        ? "A step needs a module key (e.g. `shell: {...}`)"
        : `A step must have exactly one module key, found: ${keys.join(", ")}`,
    );
  }
  const use = keys[0]!;
  const rawConfig = value[use];
  if (rawConfig !== null && rawConfig !== undefined && !isPlainObject(rawConfig)) {
    l.fail([...path, use], `Config for "${use}" must be a mapping`);
  }
  const config: Record<string, unknown> = { ...(rawConfig ?? {}) };

  // `do` may sit beside the module key or inside its config (strategies); hoist the latter.
  let doPath: Path = [...path, "do"];
  let rawDo = value["do"];
  if (rawDo === undefined && "do" in config) {
    rawDo = config["do"];
    doPath = [...path, use, "do"];
    delete config["do"];
  }
  if (rawDo !== undefined && !Array.isArray(rawDo)) l.fail(doPath, "`do` must be a list of steps");

  const explicitId = value["id"];
  if (explicitId !== undefined && (typeof explicitId !== "string" || explicitId === "")) {
    l.fail([...path, "id"], "`id` must be a non-empty string");
  }
  const nodeId = (explicitId as string | undefined) ?? id;
  const loc = l.loc(path);
  if (seen.has(nodeId)) {
    const prev = seen.get(nodeId)!;
    l.fail(path, `Duplicate step id "${nodeId}" (first defined at line ${prev.line})`);
  }
  seen.set(nodeId, loc);

  const rawWires = value["wires"];
  if (
    rawWires !== undefined &&
    !(isPlainObject(rawWires) && Object.values(rawWires).every((t) => typeof t === "string"))
  ) {
    l.fail([...path, "wires"], "`wires` must map port names to step ids");
  }

  const children = ((rawDo as unknown[] | undefined) ?? []).map((child, i) =>
    buildNode(l, child, [...doPath, i], `${nodeId}.${i}`, seen),
  );
  return {
    id: nodeId,
    use,
    config,
    children,
    wires: (rawWires as Record<string, string> | undefined) ?? {},
    loc,
  };
}

function collectIds(node: PipelineNode, into: Map<string, PipelineNode>): void {
  into.set(node.id, node);
  node.children.forEach((c) => collectIds(c, into));
}

export function parsePipeline(text: string, file = "whiletrue.yaml"): PipelineConfig {
  const l = new Located(text, file);
  const raw = l.doc.toJS() as unknown;
  if (!isPlainObject(raw)) l.fail([], "whiletrue.yaml must be a mapping");
  const top = l.zod(TopLevel, raw);
  if (top.pipeline === undefined) l.fail([], "Missing required key `pipeline`");

  const { name, uses, on_idle, on_fail, pipeline, ...extra } = top;
  const seen = new Map<string, SourceLoc>();
  let root: PipelineNode;
  if (Array.isArray(pipeline)) {
    seen.set("root", l.loc(["pipeline"]));
    const children = pipeline.map((child, i) =>
      buildNode(l, child, ["pipeline", i], `root.${i}`, seen),
    );
    root = {
      id: "root",
      use: "sequence",
      config: {},
      children,
      wires: {},
      loc: l.loc(["pipeline"]),
    };
  } else {
    root = buildNode(l, pipeline, ["pipeline"], "root", seen);
  }

  // Wire targets must be siblings.
  const byId = new Map<string, PipelineNode>();
  collectIds(root, byId);
  const check = (n: PipelineNode): void => {
    const siblings = new Set(n.children.map((c) => c.id));
    for (const c of n.children) {
      for (const [port, target] of Object.entries(c.wires)) {
        if (!siblings.has(target)) {
          throw new ConfigError(c.loc, `Wire "${port}" targets unknown sibling step "${target}"`);
        }
      }
      check(c);
    }
  };
  check(root);

  return {
    file,
    name: name ?? basename(dirname(resolve(file))),
    uses,
    on_idle,
    on_fail,
    extra,
    root,
  };
}

export function loadPipeline(path: string): PipelineConfig {
  return parsePipeline(readFileSync(path, "utf8"), path);
}

// ---- Agent modules (markdown with frontmatter) -------------------------------------------

const AgentFrontmatter = z.object({
  agent: z.string().min(1),
  runner: z.string().default("claude-code-headless"),
  model: z.string().optional(),
  session: z.string().default("fresh"),
  tools: z.array(z.string()).optional(),
  permission_mode: z.string().optional(),
  inputs: z.record(z.string(), z.string()).default({}),
  outputs: z.record(z.string(), z.string()).default({}),
  context: z.record(z.string(), z.unknown()).default({}),
  limits: z
    .object({
      budget_usd: z.number().positive().optional(),
      turns: z.number().int().positive().optional(),
      wall: z.union([z.string(), z.number()]).optional(),
    })
    .optional(),
});

export interface AgentDefinition {
  file: string;
  name: string;
  runner: string;
  model?: string;
  session: string;
  tools?: string[];
  permission_mode?: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  context: Record<string, unknown>;
  limits?: { budget_usd?: number; turns?: number; wall_ms?: number };
  prompt: string;
}

export function parseAgentDefinition(text: string, file: string): AgentDefinition {
  const m = /^---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)([\s\S]*)$/.exec(text);
  if (!m) {
    throw new ConfigError(
      { file, line: 1, col: 1 },
      "Agent file must start with a YAML frontmatter block delimited by ---",
    );
  }
  const l = new Located(m[1] ?? "", file, 1);
  const raw = l.doc.toJS() as unknown;
  if (!isPlainObject(raw)) l.fail([], "Agent frontmatter must be a mapping");
  const fm = l.zod(AgentFrontmatter, raw);

  let wall_ms: number | undefined;
  if (fm.limits?.wall !== undefined) {
    try {
      wall_ms = parseDuration(fm.limits.wall);
    } catch (e) {
      l.fail(["limits", "wall"], (e as Error).message);
    }
  }
  const limits = fm.limits
    ? {
        ...(fm.limits.budget_usd !== undefined && { budget_usd: fm.limits.budget_usd }),
        ...(fm.limits.turns !== undefined && { turns: fm.limits.turns }),
        ...(wall_ms !== undefined && { wall_ms }),
      }
    : undefined;

  return {
    file,
    name: fm.agent,
    runner: fm.runner,
    ...(fm.model !== undefined && { model: fm.model }),
    session: fm.session,
    ...(fm.tools !== undefined && { tools: fm.tools }),
    ...(fm.permission_mode !== undefined && { permission_mode: fm.permission_mode }),
    inputs: fm.inputs,
    outputs: fm.outputs,
    context: fm.context,
    ...(limits !== undefined && { limits }),
    prompt: (m[2] ?? "").trim(),
  };
}

export function loadAgentDefinition(path: string): AgentDefinition {
  return parseAgentDefinition(readFileSync(path, "utf8"), path);
}
