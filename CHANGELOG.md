# Changelog

## 0.2.0

- **Recursive self-improvement (`rsi`), off by default.** The Harness² method (Xu et al., arXiv:2610.14224) ported into `whiletrue.rsi.harness2`: isolated rollouts, improver sessions that edit the harness, composition, blind selection, a per-domain experience library, human-approved adoption, `rsi.budget_usd` and the R = 4k / P = 3k budget identity. New: top-level `rsi:` setting, strategy `rsi.harness2`, `whiletrue rsi status|diff|library|adopt|reset`. Claude Code and Codex surfaces. Apache-2.0 code keeps its headers; see THIRD_PARTY_NOTICES.md. Guide: docs/rsi.md.
- Core: a scope can redirect its steps to another directory (`cwd`), runners can pass raw messages (`RunContext.on_raw`), agents expose an event tap, and the item scope has `project_dir`.

## 0.1.1

- Fixed: parallel interactive items raced to create the tmux session. The loser failed with `duplicate session` and that error was fed to the agent as its previous failure. Session and pane creation now run one at a time, and parallel agents share one window grid.
- Docs: a new README with a recording of a real run, architecture and flow diagrams, and a step-by-step tutorial (`docs/tutorial.md`).

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
