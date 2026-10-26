# whiletrue

A local, single-machine Python orchestrator that runs configurable pipelines of agentic and deterministic steps around coding agents such as Claude Code and opencode.

> LangGraph builds the agent; whiletrue runs the loop around agents you already have.

**Status:** M0 (core engine), M1 (control flow, composites, recipes, replay), M2 (interactive Claude Code in tmux panes) M3 (parallel items in git worktrees, crash recovery) and M4 (GitHub issues and PRs, human gates, review comments, opencode) are built in Python. `whiletrue init | validate | plan | run | resume | rerun | cancel | logs | cost | status | lock | replay | attach | panel | approve | reject | comment | history | learnings` work with the headless and interactive Claude Code runners and opencode. Start with the [reference pipeline](examples/issue-loop/README.md). See [`docs/`](docs/README.md): [getting started](docs/getting-started.md), [authoring guide](docs/authoring.md), [CLI](docs/cli.md), [reference](docs/reference.md), [architecture](docs/architecture.md), [plan](docs/plan.md), [todo](docs/todo.md).

## Development

See [developing.md](docs/developing.md) and, to extend whiletrue, [plugins.md](docs/plugins.md).

Requires [devenv](https://devenv.sh) (or Python 3.12 + [uv](https://docs.astral.sh/uv/)).

```bash
direnv allow            # or: devenv shell
poe check               # format check, lint, pyright, tests + coverage, docs check
whiletrue --help        # the CLI, installed editable into .devenv/state/venv
```

## License

MIT. See [LICENSE](LICENSE).
