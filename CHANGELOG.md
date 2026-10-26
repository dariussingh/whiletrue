# Changelog

## 0.1.0 (first release)

whiletrue runs configurable pipelines of agentic and deterministic steps around coding agents.

**Engine**
- Typed modules with outcome ports (`ok`, `fail`, `needs_human`, `skip`, `rejected`), a plugin API (entry points), a static validator, and a SQLite-backed runtime with an append-only event log.
- Control flow: `sequence`, `tap`, `loop`, `map` (optionally parallel), `parallel`, `switch`, `retry`, `try`; CEL expressions; composites with params and ports; recipes `ralph`, `review-pass`, `review-respond`, `issue-loop`.
- Reproducibility: `whiletrue.lock`, run hashes, result cache, record and `replay`.
- Parallel items with dependencies, `max_agents`, an item state machine, workspaces (git worktree, directory copy), crash recovery, and `resume`, `rerun`, `cancel`, `logs`, `cost`.

**Agent backends**
- Claude Code: headless (`claude -p`) and interactive (live tmux panes with hooks, an MCP result tool, limits without headless flags, steering).
- opencode and Codex CLI runners, with structured output and session resume.

**People in the loop**
- `human.approve` gates with park/resume, approve/reject with notes, wake conditions (CLI, file, timer, GitHub review/merge), per-item history, comments that must be answered (`[whiletrue]`-prefixed replies, never resolving human threads), lessons proposed from rejections and steers that need approval, and a tmux control panel.

**Integrations**
- GitHub through the `gh` CLI: issues as items (blocked-by as dependencies, `spec:` links), PRs, sync with base, merge under a lock, review-comment replies. A file-backed fake GitHub for dry runs.

**Known limits**
- The Codex success path has not been run against a live account in CI (only its failure path and a stand-in); the real-GitHub path has been exercised only through a stand-in `gh`.
- A watching gate keeps its parallel slot; locks are per process; interactive agents ask to trust each new worktree once.
