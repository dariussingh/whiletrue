# whiletrue

A local, single-machine Python orchestrator that runs configurable pipelines of agentic and deterministic steps around coding agents such as Claude Code and opencode.

> LangGraph builds the agent; whiletrue runs the loop around agents you already have.

**Status:** M0 (core engine), M1 (control flow, composites, recipes, replay) and M2 (interactive Claude Code in tmux panes) are built in Python. `whiletrue init | validate | plan | run | status | lock | replay | attach` work with the headless and interactive Claude Code runners. See [`docs/`](docs/README.md): [getting started](docs/getting-started.md), [reference](docs/reference.md), [architecture](docs/architecture.md), [plan](docs/plan.md), [todo](docs/todo.md).

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
