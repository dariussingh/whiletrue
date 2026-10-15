import { z, type ZodType } from "zod";

/** Caps are named schemas attached to ports (like GStreamer caps). */
export class CapError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CapError";
  }
}

export type CapValidation = { ok: true; value: unknown } | { ok: false; errors: string[] };

const CAP_NAME = /^[A-Z][A-Za-z0-9]*$/;

export class CapRegistry {
  readonly #caps = new Map<string, ZodType>();

  register(name: string, schema: ZodType): void {
    if (!CAP_NAME.test(name)) throw new CapError(`Invalid cap name "${name}" (use PascalCase)`);
    if (this.#caps.has(name)) throw new CapError(`Cap "${name}" is already registered`);
    this.#caps.set(name, schema);
  }

  has(name: string): boolean {
    return this.#caps.has(name);
  }

  names(): string[] {
    return [...this.#caps.keys()];
  }

  #get(name: string): ZodType {
    const schema = this.#caps.get(name);
    if (!schema) throw new CapError(`Unknown cap "${name}"`);
    return schema;
  }

  validate(name: string, value: unknown): CapValidation {
    const result = this.#get(name).safeParse(value);
    if (result.success) return { ok: true, value: result.data };
    return {
      ok: false,
      errors: result.error.issues.map((i) => {
        const path = i.path.join(".");
        return path ? `${path}: ${i.message}` : i.message;
      }),
    };
  }

  toJsonSchema(name: string): Record<string, unknown> {
    return z.toJSONSchema(this.#get(name)) as Record<string, unknown>;
  }

  /**
   * Can a value produced under `from` be consumed under `to`?
   * Nominal: identical caps, or either side is `Any`. Unknown caps are never compatible.
   */
  compatible(from: string, to: string): boolean {
    if (!this.has(from) || !this.has(to)) return false;
    return from === to || from === "Any" || to === "Any";
  }
}

const Issue = z.object({
  id: z.union([z.string(), z.number()]),
  number: z.number().optional(),
  title: z.string(),
  body: z.string(),
  labels: z.array(z.string()).optional(),
  url: z.string().optional(),
  spec_refs: z.array(z.string()).optional(),
  depends_on: z.array(z.union([z.string(), z.number()])).optional(),
});

const Diff = z.object({
  files: z.array(z.object({ path: z.string(), additions: z.number(), deletions: z.number() })),
  patch: z.string().optional(),
});

const PullRequest = z.object({
  number: z.number(),
  url: z.string(),
  branch: z.string(),
  base: z.string().optional(),
  title: z.string().optional(),
});

const Verdict = z.object({ pass: z.boolean(), reason: z.string().optional() });

const ReviewFindings = z.object({
  findings: z.array(
    z.object({
      file: z.string().optional(),
      line: z.number().optional(),
      kind: z.string().optional(),
      severity: z.enum(["low", "medium", "high", "critical"]),
      message: z.string(),
    }),
  ),
});

export function createBuiltinCaps(): CapRegistry {
  const caps = new CapRegistry();
  caps.register("Any", z.unknown());
  caps.register("Text", z.string());
  caps.register("Issue", Issue);
  caps.register("Diff", Diff);
  caps.register("PullRequest", PullRequest);
  caps.register("Verdict", Verdict);
  caps.register("ReviewFindings", ReviewFindings);
  return caps;
}
