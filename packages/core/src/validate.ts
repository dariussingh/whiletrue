import type { PipelineConfig, PipelineNode, SourceLoc } from "./config.js";
import type { ConfigIssue, Severity } from "./module.js";
import type { Registry } from "./registry.js";

export interface Diagnostic {
  severity: Severity;
  message: string;
  nodeId: string;
  loc: SourceLoc;
}

export const hasErrors = (diagnostics: Diagnostic[]): boolean =>
  diagnostics.some((d) => d.severity === "error");

export const formatDiagnostic = (d: Diagnostic): string =>
  `${d.loc.file}:${d.loc.line}:${d.loc.col}: ${d.severity}: ${d.message} [${d.nodeId}]`;

const firstPort = (ports: { name: string; cap: string }[], preferred: string) =>
  ports.find((p) => p.name === preferred) ?? ports[0];

/**
 * Static validation: everything that can be known before any tokens are spent.
 * Resolves modules, checks config schemas, wiring caps and the safety rules.
 */
export function validatePipeline(
  config: PipelineConfig,
  registry: Registry,
  options: { cwd?: string } = {},
): Diagnostic[] {
  const vctx = { cwd: options.cwd ?? process.cwd(), registry };
  const out: Diagnostic[] = [];
  const push = (node: PipelineNode, severity: Severity, message: string) =>
    out.push({ severity, message, nodeId: node.id, loc: node.loc });
  const pushIssues = (node: PipelineNode, issues: ConfigIssue[]) =>
    issues.forEach((i) => push(node, i.severity, i.message));

  const visit = (node: PipelineNode): void => {
    const resolved = registry.resolve(node.use);
    if (!resolved) {
      const known = registry.ids();
      push(
        node,
        "error",
        `Unknown module "${node.use}"${known.length ? ` (known: ${known.join(", ")})` : ""}`,
      );
    } else {
      const parsed = resolved.module.config.safeParse(node.config);
      if (!parsed.success) {
        for (const issue of parsed.error.issues) {
          const at = issue.path.length ? ` at "${issue.path.join(".")}"` : "";
          push(node, "error", `Invalid config for "${node.use}": ${issue.message}${at}`);
        }
      } else {
        pushIssues(node, resolved.module.validate?.(parsed.data, vctx) ?? []);
      }

      if (resolved.kind === "action") {
        const mod = resolved.module;
        if (node.children.length > 0) {
          push(node, "error", `Action module "${node.use}" cannot have child steps (\`do\`)`);
        }
        if (mod.id.startsWith("gate.") && mod.effects !== "deterministic") {
          push(node, "error", `Gate "${mod.id}" must be deterministic`);
        }
        if (mod.effects === "side-effect" && !mod.idempotencyKey) {
          push(node, "error", `Side-effect module "${mod.id}" must provide an idempotencyKey`);
        }
      } else if (resolved.module.chain) {
        for (let i = 1; i < node.children.length; i++) {
          const prev = node.children[i - 1]!;
          const next = node.children[i]!;
          const a = registry.resolve(prev.use);
          const b = registry.resolve(next.use);
          if (a?.kind !== "action" || b?.kind !== "action") continue;
          const from = firstPort(a.module.ports.out, "out");
          const to = firstPort(b.module.ports.in, "in");
          if (from && to && !registry.caps.compatible(from.cap, to.cap)) {
            push(
              next,
              "error",
              `Cannot wire "${prev.id}".${from.name} (${from.cap}) into "${next.id}".${to.name} (${to.cap})`,
            );
          }
        }
      }
    }
    node.children.forEach(visit);
  };
  visit(config.root);
  return out;
}
