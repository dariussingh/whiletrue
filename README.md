# whiletrue

A local, single-machine Python orchestrator that runs configurable pipelines of agentic and deterministic steps around coding agents such as Claude Code and opencode.

> LangGraph builds the agent; whiletrue runs the loop around agents you already have.

**Status:** M0 (core engine) built in Python, pending review. `whiletrue init | validate | plan | run | status` work with the headless Claude Code runner. See [`docs/`](docs/README.md): [architecture](docs/architecture.md), [plan](docs/plan.md), [todo](docs/todo.md).

## Development

Requires [devenv](https://devenv.sh) (or Python 3.12 + [uv](https://docs.astral.sh/uv/)).

```bash
direnv allow            # or: devenv shell
poe check               # format check, lint, pyright, tests + coverage, docs check
whiletrue --help        # the CLI, installed editable into .devenv/state/venv
```

## License

MIT. See [LICENSE](LICENSE).
