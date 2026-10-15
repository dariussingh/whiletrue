const UNITS: Record<string, number> = { ms: 1, s: 1000, m: 60_000, h: 3_600_000 };

/** Parses "90m", "30s", "1h", "250ms" (or a bare number of milliseconds) into milliseconds. */
export function parseDuration(input: string | number): number {
  if (typeof input === "number") {
    if (!Number.isFinite(input) || input < 0) throw new Error(`Invalid duration: ${input}`);
    return input;
  }
  const m = /^(\d+(?:\.\d+)?)(ms|s|m|h)$/.exec(input.trim());
  if (!m) throw new Error(`Invalid duration "${input}" (use e.g. 30s, 90m, 1h)`);
  return Math.round(Number(m[1]) * UNITS[m[2] as string]!);
}
