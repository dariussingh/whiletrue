from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from whiletrue.core.caps import CapRegistry, create_builtin_caps
from whiletrue.core.module import ActionModule, Strategy
from whiletrue.core.runner import Runner

ProviderKind = Literal["source", "store", "workspace", "knowledge", "comments"]


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Resolved:
    kind: Literal["action", "strategy"]
    module: Any  # ActionModule[Any] | Strategy[Any]; narrowed by `kind`


class PluginApi(Protocol):
    """The public plugin surface. Built-in modules use exactly this API."""

    def register_action[Cfg: BaseModel](self, module: ActionModule[Cfg]) -> None: ...
    def register_strategy[Cfg: BaseModel](self, strategy: Strategy[Cfg]) -> None: ...
    def register_runner(self, runner: Runner) -> None: ...
    def register_provider(self, kind: ProviderKind, name: str, provider: Any) -> None: ...
    def register_cap(self, name: str, schema: Any) -> None: ...


@runtime_checkable
class Plugin(Protocol):
    @property
    def name(self) -> str: ...

    def register(self, api: PluginApi, /) -> None: ...


@dataclass(frozen=True)
class SimplePlugin:
    """A plugin made of a name and a registration function."""

    name: str
    register: Callable[[PluginApi], None]


class Registry:
    def __init__(self, caps: CapRegistry | None = None) -> None:
        self.caps = caps if caps is not None else create_builtin_caps()
        self._actions: dict[str, ActionModule[Any]] = {}
        self._strategies: dict[str, Strategy[Any]] = {}
        self._runners: dict[str, Runner] = {}
        self._providers: dict[str, Any] = {}
        self._plugins: list[str] = []
        self._plugin_versions: dict[str, str] = {}

    def _assert_free_id(self, module_id: str) -> None:
        if module_id in self._actions or module_id in self._strategies:
            raise RegistryError(f'Module or strategy "{module_id}" is already registered')

    def register_action[Cfg: BaseModel](self, module: ActionModule[Cfg]) -> None:
        self._assert_free_id(module.id)
        self._actions[module.id] = module

    def register_strategy[Cfg: BaseModel](self, strategy: Strategy[Cfg]) -> None:
        self._assert_free_id(strategy.id)
        self._strategies[strategy.id] = strategy

    def register_runner(self, runner: Runner) -> None:
        if runner.id in self._runners:
            raise RegistryError(f'Runner "{runner.id}" is already registered')
        self._runners[runner.id] = runner

    def register_provider(self, kind: ProviderKind, name: str, provider: Any) -> None:
        key = f"{kind}:{name}"
        if key in self._providers:
            raise RegistryError(f'Provider "{key}" is already registered')
        self._providers[key] = provider

    def register_cap(self, name: str, schema: Any) -> None:
        self.caps.register(name, schema)

    def resolve(self, module_id: str) -> Resolved | None:
        action = self._actions.get(module_id)
        if action is not None:
            return Resolved("action", action)
        strategy = self._strategies.get(module_id)
        if strategy is not None:
            return Resolved("strategy", strategy)
        return None

    def get_runner(self, runner_id: str) -> Runner | None:
        return self._runners.get(runner_id)

    def get_provider(self, kind: ProviderKind, name: str) -> Any:
        return self._providers.get(f"{kind}:{name}")

    def ids(self) -> list[str]:
        return sorted([*self._actions, *self._strategies])

    def use(self, plugin: Plugin, version: str | None = None) -> None:
        """Installs a plugin. `version` (else its `version` attribute) is what the lock pins."""
        if plugin.name in self._plugins:
            raise RegistryError(f'Plugin "{plugin.name}" is already installed')
        plugin.register(self)
        self._plugins.append(plugin.name)
        self._plugin_versions[plugin.name] = version or str(
            getattr(plugin, "version", "unversioned")
        )

    def plugin_versions(self) -> dict[str, str]:
        return dict(self._plugin_versions)

    def plugins(self) -> list[str]:
        return list(self._plugins)
