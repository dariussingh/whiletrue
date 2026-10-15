export class TemplateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TemplateError";
  }
}

const TOKEN = /\{\{\s*([^{}]*?)\s*\}\}/g;
const PATH = /^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*$/;

function lookup(scope: unknown, path: string): unknown {
  let cur: unknown = scope;
  for (const key of path.split(".")) {
    if (cur === null || typeof cur !== "object" || !(key in cur)) {
      throw new TemplateError(`Template variable "${path}" is not defined`);
    }
    cur = (cur as Record<string, unknown>)[key];
  }
  return cur;
}

/**
 * Replaces `{{dotted.path}}` with values from `scope`. Strict: a missing variable throws.
 * Objects are JSON-encoded. (Full expressions arrive with the expression-language item.)
 */
export function renderTemplate(template: string, scope: unknown): string {
  return template.replace(TOKEN, (_all, expr: string) => {
    if (!PATH.test(expr)) throw new TemplateError(`Unsupported template expression "${expr}"`);
    const value = lookup(scope, expr);
    return typeof value === "string" ? value : JSON.stringify(value);
  });
}

/** Renders every string leaf of a JSON-like value. */
export function renderDeep<T>(value: T, scope: unknown): T {
  if (typeof value === "string") return renderTemplate(value, scope) as T;
  if (Array.isArray(value)) return value.map((v) => renderDeep(v, scope)) as T;
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, renderDeep(v, scope)]),
    ) as T;
  }
  return value;
}
