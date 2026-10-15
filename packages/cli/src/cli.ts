import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import {
  ConfigError,
  formatDiagnostic,
  hasErrors,
  loadPipeline,
  runPipeline,
  validatePipeline,
  type PipelineConfig,
  type PipelineNode,
  type Registry,
} from "@whiletrue-ai/core";
import { SqliteStore } from "@whiletrue-ai/store-sqlite";
import { createDefaultRegistry, loadPlugins } from "./registry.js";
import { RALPH_AGENT, RALPH_PIPELINE } from "./templates.js";

export interface Io {
  cwd: string;
  out: (text: string) => void;
  err: (text: string) => void;
  signal?: AbortSignal;
}

const USAGE = `whiletrue — run configurable loops around coding agents

Usage: whiletrue <command> [options]

Commands:
  init              Scaffold whiletrue.yaml and agents/ralph.md
  validate          Check config, wiring and safety rules (no tokens spent)
  plan              Print the expanded pipeline tree (no tokens spent)
  run               Run the pipeline
  status [run]      Show the latest (or given) run

Options:
  -f, --file <path> Pipeline file (default: whiletrue.yaml)
  --force           init: overwrite existing files
  -h, --help        Show this help
`;

export const EXIT = { ok: 0, failed: 1, parked: 2, usage: 64, cancelled: 130 } as const;

interface Loaded {
  config: PipelineConfig;
  registry: Registry;
  cwd: string;
}

async function load(io: Io, file: string): Promise<Loaded | number> {
  const path = resolve(io.cwd, file);
  let config: PipelineConfig;
  try {
    config = loadPipeline(path);
  } catch (e) {
    io.err(
      `${e instanceof ConfigError ? e.message : `Cannot load ${file}: ${(e as Error).message}`}\n`,
    );
    return EXIT.failed;
  }
  const registry = createDefaultRegistry();
  try {
    await loadPlugins(registry, config.uses, dirname(path));
  } catch (e) {
    io.err(`${(e as Error).message}\n`);
    return EXIT.failed;
  }
  return { config, registry, cwd: dirname(path) };
}

function validated(io: Io, loaded: Loaded): boolean {
  const diagnostics = validatePipeline(loaded.config, loaded.registry, { cwd: loaded.cwd });
  for (const d of diagnostics) io.err(`${formatDiagnostic(d)}\n`);
  return !hasErrors(diagnostics);
}

function renderTree(node: PipelineNode, prefix = "", last = true, root = true): string[] {
  const summary = Object.keys(node.config).length ? ` ${JSON.stringify(node.config)}` : "";
  const line = `${root ? "" : prefix + (last ? "└─ " : "├─ ")}${node.use}${summary}  [${node.id}]`;
  const childPrefix = root ? "" : prefix + (last ? "   " : "│  ");
  return [
    line,
    ...node.children.flatMap((c, i) =>
      renderTree(c, childPrefix, i === node.children.length - 1, false),
    ),
  ];
}

function init(io: Io, force: boolean): number {
  const files: [string, string][] = [
    ["whiletrue.yaml", RALPH_PIPELINE],
    ["agents/ralph.md", RALPH_AGENT],
  ];
  const existing = files.filter(([f]) => existsSync(join(io.cwd, f))).map(([f]) => f);
  if (existing.length && !force) {
    io.err(`Refusing to overwrite ${existing.join(", ")} (use --force)\n`);
    return EXIT.failed;
  }
  for (const [f, content] of files) {
    mkdirSync(dirname(join(io.cwd, f)), { recursive: true });
    writeFileSync(join(io.cwd, f), content);
    io.out(`created ${f}\n`);
  }
  io.out("Next: whiletrue validate && whiletrue plan && whiletrue run\n");
  return EXIT.ok;
}

async function run(io: Io, file: string): Promise<number> {
  const loaded = await load(io, file);
  if (typeof loaded === "number") return loaded;
  if (!validated(io, loaded)) return EXIT.failed;

  const store = new SqliteStore(join(loaded.cwd, ".whiletrue", "state.db"));
  try {
    const result = await runPipeline({
      registry: loaded.registry,
      config: loaded.config,
      store,
      cwd: loaded.cwd,
      ...(io.signal && { signal: io.signal }),
      log: (level, message) => {
        if (level !== "debug") io.out(`[${level}] ${message}\n`);
      },
    });
    io.out(`run ${result.runId}: ${result.status}\n`);
    for (const item of result.items) {
      io.out(`  ${item.itemId}: ${item.state} (${item.port})\n`);
    }
    if (result.status === "done") return EXIT.ok;
    if (result.status === "parked") return EXIT.parked;
    if (result.status === "cancelled") return EXIT.cancelled;
    return EXIT.failed;
  } finally {
    store.close();
  }
}

function status(io: Io, runId: string | undefined): number {
  const dbPath = join(io.cwd, ".whiletrue", "state.db");
  if (!existsSync(dbPath)) {
    io.err("No runs yet (.whiletrue/state.db not found)\n");
    return EXIT.failed;
  }
  const store = new SqliteStore(dbPath);
  try {
    const run = runId ? store.getRun(runId) : store.latestRun();
    if (!run) {
      io.err(runId ? `No run "${runId}"\n` : "No runs yet\n");
      return EXIT.failed;
    }
    io.out(`run ${run.id}  ${run.status}  started ${new Date(run.started_at).toISOString()}\n`);
    const steps = store.listSteps(run.id);
    for (const item of store.listItems(run.id)) {
      const itemSteps = steps.filter((s) => s.item_id === item.id);
      const cost = itemSteps.reduce((sum, s) => sum + s.cost_usd, 0);
      io.out(`  ${item.id}  ${item.state}  steps=${itemSteps.length}  cost=$${cost.toFixed(4)}\n`);
      for (const s of itemSteps) {
        io.out(
          `    ${s.node_path}#${s.attempt}  ${s.status}${s.port ? `  ${s.port}` : ""}${s.error ? `  — ${s.error}` : ""}\n`,
        );
      }
    }
    return EXIT.ok;
  } finally {
    store.close();
  }
}

export async function main(argv: string[], io: Io): Promise<number> {
  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      allowPositionals: true,
      options: {
        file: { type: "string", short: "f", default: "whiletrue.yaml" },
        force: { type: "boolean", default: false },
        help: { type: "boolean", short: "h", default: false },
      },
    });
  } catch (e) {
    io.err(`${(e as Error).message}\n${USAGE}`);
    return EXIT.usage;
  }
  const [command, ...rest] = parsed.positionals;
  const { file, force, help } = parsed.values;
  if (help) {
    io.out(USAGE);
    return EXIT.ok;
  }
  if (command === undefined) {
    io.err(USAGE);
    return EXIT.usage;
  }

  switch (command) {
    case "init":
      return init(io, force);
    case "validate": {
      const loaded = await load(io, file);
      if (typeof loaded === "number") return loaded;
      if (!validated(io, loaded)) return EXIT.failed;
      io.out(`${loaded.config.file}: ok\n`);
      return EXIT.ok;
    }
    case "plan": {
      const loaded = await load(io, file);
      if (typeof loaded === "number") return loaded;
      if (!validated(io, loaded)) return EXIT.failed;
      io.out(`${loaded.config.name}\n${renderTree(loaded.config.root).join("\n")}\n`);
      return EXIT.ok;
    }
    case "run":
      return run(io, file);
    case "status":
      return status(io, rest[0]);
    default:
      io.err(`Unknown command "${command}"\n${USAGE}`);
      return EXIT.usage;
  }
}
