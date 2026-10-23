from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from whiletrue.core.config import PipelineNode
    from whiletrue.core.context import CompositeContext, StepContext
    from whiletrue.core.registry import Registry

#: How a module may touch the world. Drives the safety rules (see architecture.md).
EFFECTS = ("pure", "deterministic", "agentic", "side-effect")
Effect = Literal["pure", "deterministic", "agentic", "side-effect"]

#: Values arriving on (or leaving from) a module's ports, keyed by port name.
PortValues = dict[str, Any]


def is_effect(value: object) -> bool:
    return isinstance(value, str) and value in EFFECTS


class EmptyConfig(BaseModel):
    """Config for modules that take none."""


@dataclass(frozen=True)
class PortSpec:
    """A named, typed connection point. `cap` names a schema (Issue, Diff, Text, Any, ...)."""

    name: str
    cap: str
    optional: bool = False


@dataclass
class Ports:
    inputs: list[PortSpec] = field(default_factory=lambda: [])
    outputs: list[PortSpec] = field(default_factory=lambda: [])


@dataclass
class Emit:
    """Which port fires, and with what. `value=None` means no value."""

    port: str
    value: Any = None


@dataclass(frozen=True)
class ConfigIssue:
    """A static-validation finding about a config (never raised at run time)."""

    severity: Literal["error", "warning"]
    message: str


@dataclass
class ValidateContext:
    """What static checks may look at (e.g. an agent step loading its prompt file)."""

    cwd: str
    registry: Registry


@dataclass
class ActionModule[Cfg: BaseModel]:
    id: str  # e.g. 'gate.tests'
    config: type[Cfg]
    ports: Ports
    effects: Effect
    run: Callable[[PortValues, StepContext[Cfg]], Awaitable[Emit]]
    kind: Literal["action"] = "action"
    capabilities: list[str] = field(default_factory=lambda: [])
    #: Whether results of `pure`/`deterministic` runs may be cached by input hash. Modules that
    #: read the workspace (gates, shell) must set this to False.
    cacheable: bool = True
    #: Extra static checks beyond the config schema (e.g. agent limits).
    validate: Callable[[Cfg, ValidateContext], list[ConfigIssue]] | None = None
    #: Required for `side-effect` modules: a deterministic key so resume never repeats the effect.
    idempotency_key: Callable[[PortValues, StepContext[Cfg]], str] | None = None
    #: Record the result under `idempotency_key` so a repeat (resume, a second run) reuses it
    #: instead of repeating the effect. The key must name the *effect*, not the run.
    journal: bool = False


@dataclass
class Strategy[Cfg: BaseModel]:
    id: str  # e.g. 'loop'
    config: type[Cfg]
    run: Callable[[list[PipelineNode], PortValues, CompositeContext[Cfg]], Awaitable[Emit]]
    #: Children are chained: each child's `out` feeds the next child's `in`.
    chain: bool = False
    #: Extra static checks beyond the config schema (e.g. loops must be bounded).
    validate: Callable[[Cfg, ValidateContext], list[ConfigIssue]] | None = None
    #: Static checks that need the step's children (e.g. `switch` case targets).
    validate_node: Callable[[Cfg, PipelineNode, ValidateContext], list[ConfigIssue]] | None = None


class ModuleDefinitionError(Exception):
    def __init__(self, module_id: str, problem: str) -> None:
        super().__init__(f'Invalid module "{module_id}": {problem}')


_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _check_id(module_id: str) -> None:
    if not _ID.match(module_id):
        raise ModuleDefinitionError(
            module_id, "id must be lowercase dotted segments, e.g. 'gate.tests'"
        )


def _check_ports(module_id: str, direction: str, ports: list[PortSpec]) -> None:
    seen: set[str] = set()
    for port in ports:
        if port.name in seen:
            raise ModuleDefinitionError(module_id, f'duplicate {direction} port "{port.name}"')
        seen.add(port.name)


def define_action[Cfg: BaseModel](mod: ActionModule[Cfg]) -> ActionModule[Cfg]:
    """Validates an action module's static shape at definition time and returns it unchanged."""
    _check_id(mod.id)
    if str(mod.kind) != "action":
        raise ModuleDefinitionError(mod.id, f"kind must be 'action', got '{mod.kind}'")
    if not is_effect(mod.effects):
        raise ModuleDefinitionError(mod.id, f"unknown effect '{mod.effects}'")
    if mod.id.startswith("gate.") and mod.effects != "deterministic":
        raise ModuleDefinitionError(
            mod.id, "gates must be deterministic: an agent can never grade itself"
        )
    _check_ports(mod.id, "in", mod.ports.inputs)
    _check_ports(mod.id, "out", mod.ports.outputs)
    return mod


def define_strategy[Cfg: BaseModel](strategy: Strategy[Cfg]) -> Strategy[Cfg]:
    """Validates a strategy's static shape at definition time and returns it unchanged."""
    _check_id(strategy.id)
    return strategy
