# whiletrue docs

| Doc                                    | Purpose                                                                           |
| -------------------------------------- | --------------------------------------------------------------------------------- |
| [architecture.md](architecture.md)     | Full design: core model, runtime, state, agents, human gates, packages            |
| [getting-started.md](getting-started.md) | User guide: install, first loop, commands, exit codes, safety defaults          |
| [tutorial.md](tutorial.md)             | Tutorial: install, first loop, then build your own gates, review, parallel and human steps |
| [authoring.md](authoring.md)           | Authoring guide: pipelines, composites, agents, choosing a backend                |
| [cli.md](cli.md)                       | CLI reference: every command, option, exit code and environment variable          |
| [rsi.md](rsi.md)                       | Recursive self-improvement: turn it on, what it costs, adoption, limits           |
| [reference.md](reference.md)           | User reference: modules, control flow, expressions, composites, recipes, replay   |
| [plugins.md](plugins.md)               | Extending whiletrue: modules, strategies, runners, caps, entry points              |
| [developing.md](developing.md)         | Contributing: setup, layout, run flow, workflow                                    |
| [rsi-plan.md](rsi-plan.md)             | Execution plan for M5: Harness² recursive self-improvement and the Gemini CLI backend |
| [plan.md](plan.md)                     | Implementation plan: milestones M0–M4, dependencies, exit criteria, risks, spikes |
| [todo.md](todo.md)                     | Numbered, ordered implementation checklist (work top to bottom)                   |
| [open-questions.md](open-questions.md) | Undecided design questions, each tied to the milestone that forces the decision   |
| [spike-rsi.md](spike-rsi.md)           | Findings: Harness² method, what is ported, concept mapping and decisions            |
| [spike-interactive.md](spike-interactive.md) | Findings: plugin hooks and MCP in interactive Claude Code (OQ-1)               |
| [conventions.md](conventions.md)       | Engineering conventions, test and docs rules (the non-negotiables made concrete)  |

Non-negotiables (every change must honor them): human keeps context; maximal test coverage; user and architecture docs stay current; human is accountable for every line.
