# Writing plugins (extending whiletrue)

Everything in whiletrue, including the built-in `shell`, `loop` and `agent`, is a plugin using the same public API: `whiletrue.core`. Your plugin can add modules, strategies, runners and caps.

## A complete example

```python
# shout.py  (in the project directory)
from pydantic import BaseModel

from whiletrue.core import (
    ActionModule,
    Emit,
    PluginApi,
    Ports,
    PortSpec,
    SimplePlugin,
    StepContext,
    define_action,
)
from whiletrue.core.module import PortValues


class ShoutConfig(BaseModel):
    suffix: str = "!"


async def run(input: PortValues, ctx: StepContext[ShoutConfig]) -> Emit:
    return Emit("ok", str(input.get("in") or "").upper() + ctx.config.suffix)


shout = define_action(
    ActionModule(
        id="shout",
        config=ShoutConfig,
        ports=Ports(inputs=[PortSpec("in", "Text")], outputs=[PortSpec("out", "Text")]),
        effects="pure",
        run=run,
    )
)


def register(api: PluginApi) -> None:
    api.register_action(shout)


plugin = SimplePlugin(name="shout", register=register)
```

```yaml
# whiletrue.yaml
uses: [shout]
pipeline:
  - set: { value: "hello" }
  - shout: { suffix: "!!" }
```

`whiletrue validate && whiletrue run` records `HELLO!!` as the last step's output (in `.whiletrue/state.db`).

## How plugins are found

`uses: [name]` resolves to, in order: an installed entry point in the group `whiletrue.plugins` named `name`; otherwise an importable module `name` (looked up from the project directory) that exposes `plugin`. To publish one, add to its `pyproject.toml`:

```toml
[project.entry-points."whiletrue.plugins"]
shout = "shout_pkg:plugin"
```

The entry point may name a `Plugin` object or a zero-argument function returning one. `whiletrue lock` pins the installed distribution's version.

## Action modules

An `ActionModule` is one unit of work.

| Field | Meaning |
| --- | --- |
| `id` | Lowercase dotted name (`gate.tests`). Unique across all plugins |
| `config` | A pydantic model; the step's YAML config is validated against it |
| `ports` | Input and output ports, each with a cap name (`Text`, `Any`, `Diff`, ...). Wiring is checked statically |
| `effects` | `pure`, `deterministic`, `agentic` or `side-effect` |
| `run(input, ctx)` | Async; returns `Emit(port, value)` |
| `validate` | Optional static checks returning `ConfigIssue`s (no running, no tokens) |
| `idempotency_key` | Required for `side-effect` modules |
| `cacheable` | Default true. Set false if a `pure`/`deterministic` module reads the workspace |

Rules the validator and `define_action` enforce:

- `gate.*` modules must be `deterministic`.
- `side-effect` modules must provide `idempotency_key`.
- Outputs on the `out` port are checked against the declared cap at run time.

Effects matter: `pure` and `deterministic` results are cached by input hash and re-executed during replay (only `pure` ones); `agentic` and `side-effect` results are served from the recording on replay and never cached.

### Emitting outcomes

`Emit("ok", value)` continues. `Emit("fail", {"error": "..."})` reports a failure (the next `loop` iteration sees it as `{{iter.previous}}`). Other outcome ports: `needs_human` (park for a person), `skip` (pass the previous value through), `rejected`. Raising an exception is turned into a `fail` for you.

### The step context

`ctx` gives you the validated `config`, `cwd`, a `signal` (honour it: cancellation and timeouts arrive through it), `log(level, message)`, `record_cost(usd)`, `scope` (inherited key/values plus resources released when the scope ends, for example a worktree), `registry`, `bus` and a stable `step_key` for idempotency.

## Strategies

A `Strategy` runs its child steps. It is how `loop`, `map` and `retry` are built.

```python
from whiletrue.core import (
    CompositeContext,
    Emit,
    EmptyConfig,
    PipelineNode,
    Strategy,
    define_strategy,
)


async def run(children: list[PipelineNode], input, ctx: CompositeContext[EmptyConfig]) -> Emit:
    return await ctx.run(children[0], input)  # the only way to run a child, so it is recorded


first = define_strategy(Strategy(id="first", config=EmptyConfig, run=run))
```

Use `whiletrue.control.run_chain` to run children in order with `out → in` wiring and `wires:` routing. Set `validate_node` for checks that need the children (as `switch` does for its case targets). Always check `ctx.signal.aborted` between children.

## Runners

A runner executes an agent prompt: `id`, `capabilities` (`interactive`, `structured_output`, `resume`, `fork`, `budget`) and `async run(request, ctx) -> AgentResult`. Register it with `api.register_runner(...)`; agent files select it with `runner: <id>`. The validator rejects an agent that needs a capability its runner lacks. `whiletrue/claude_code/headless.py` is the reference implementation.

## Caps

`api.register_cap("Ticket", TicketModel)` registers a PascalCase name backed by anything pydantic can validate. Caps with the same name are compatible; `Any` matches everything.

## Testing a plugin

Build a `Registry`, `registry.use(your_plugin)`, then `run_pipeline(registry=..., config=parse_pipeline(yaml_text), store=MemoryStore())`, or call `module.run(input, create_step_context(config, cwd=...))` directly. `tests/unit/control/test_control.py` shows a scripted-module harness.
