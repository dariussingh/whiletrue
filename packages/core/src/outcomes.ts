/** Every module has these outcome ports in addition to its data ports. */
export const OUTCOME_PORTS = ["ok", "fail", "needs_human", "skip", "rejected"] as const;
export type OutcomePort = (typeof OUTCOME_PORTS)[number];

export function isOutcomePort(port: string): port is OutcomePort {
  return (OUTCOME_PORTS as readonly string[]).includes(port);
}

export type ItemState =
  "pending" | "ready" | "running" | "parked" | "done" | "failed" | "cancelled";

/** What a composite does with an unwired `fail`. */
export type OnFail = "park" | "drop" | "propagate";

export interface OutcomePolicy {
  on_fail: OnFail;
}

export type Resolution =
  | { action: "continue" }
  | { action: "route"; target: string }
  | { action: "propagate"; port: string }
  | { action: "exit"; port: string; state: ItemState };

/** Terminal item state for an outcome that leaves the pipeline. */
export function itemStateFor(port: OutcomePort, onFail: OnFail): ItemState {
  switch (port) {
    case "ok":
    case "skip":
      return "done";
    case "fail":
      return onFail === "park" ? "parked" : "failed";
    case "needs_human":
    case "rejected":
      return "parked";
  }
}

/**
 * Decide what happens when a child fires `port`. A wired port always routes to its target.
 * Unwired: ok/skip and custom data ports continue; fail/needs_human/rejected follow the policy.
 */
export function resolveOutcome(
  port: string,
  wires: Record<string, string>,
  policy: OutcomePolicy,
): Resolution {
  const target = wires[port];
  if (target !== undefined) return { action: "route", target };
  if (!isOutcomePort(port) || port === "ok" || port === "skip") return { action: "continue" };
  if (policy.on_fail === "propagate") return { action: "propagate", port };
  return { action: "exit", port, state: itemStateFor(port, policy.on_fail) };
}
