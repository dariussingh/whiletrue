/** What a runner can do; `validate` rejects steps that ask for more. */
export interface RunnerCapabilities {
  interactive: boolean;
  structured_output: boolean;
  resume: boolean;
  fork: boolean;
  budget: boolean;
}

export interface AgentLimits {
  /** Max spend in USD (hard where the runner supports it, an estimate otherwise). */
  budget_usd?: number;
  turns?: number;
  /** Wall-clock limit in milliseconds. */
  wall_ms?: number;
}

export interface AgentRequest {
  prompt: string;
  cwd: string;
  model?: string;
  tools?: string[];
  allowed_tools?: string[];
  disallowed_tools?: string[];
  permission_mode?: string;
  system_prompt_append?: string;
  /** JSON Schema the final structured output must satisfy. */
  output_schema?: Record<string, unknown>;
  limits?: AgentLimits;
  /** New session id to use (fresh sessions). */
  session_id?: string;
  /** Existing session to resume. */
  resume?: string;
}

export interface AgentResult {
  ok: boolean;
  /** Final text answer. */
  text: string;
  /** Structured output when the request carried an output schema. */
  output?: unknown;
  session_id?: string;
  cost_usd?: number;
  turns?: number;
  error?: string;
}

export interface RunContext {
  signal: AbortSignal;
  /** Raw runner events (tool calls, messages) for the event log. */
  onEvent?: (event: Record<string, unknown>) => void;
}

export interface Runner {
  id: string;
  capabilities: RunnerCapabilities;
  run(request: AgentRequest, ctx: RunContext): Promise<AgentResult>;
}
