from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from whiletrue.basics import basics_plugin
from whiletrue.claude_code import HeadlessOptions, claude_code_plugin
from whiletrue.control import control_plugin
from whiletrue.core import Plugin, Registry

#: Third-party plugins advertise themselves under this entry-point group.
PLUGIN_ENTRY_POINT_GROUP = "whiletrue.plugins"


@dataclass
class RegistryOptions:
    #: Path to the claude binary; defaults to WHILETRUE_CLAUDE_BIN or `claude`.
    claude_command: str | None = None


def create_default_registry(options: RegistryOptions | None = None) -> Registry:
    """Core + the default plugins every project gets."""
    opts = options or RegistryOptions()
    registry = Registry()
    registry.use(control_plugin)
    registry.use(basics_plugin)
    registry.use(
        claude_code_plugin(
            HeadlessOptions(
                command=opts.claude_command or os.environ.get("WHILETRUE_CLAUDE_BIN") or "claude"
            )
        )
    )
    return registry


def _as_plugin(candidate: Any) -> Plugin | None:
    # An entry point may name a Plugin or a zero-argument factory returning one.
    if callable(candidate) and not isinstance(candidate, Plugin):
        candidate = candidate()
    return candidate if isinstance(candidate, Plugin) else None


def _find_plugin(name: str, cwd: str) -> Plugin:
    for entry in entry_points(group=PLUGIN_ENTRY_POINT_GROUP):
        if entry.name == name:
            plugin = _as_plugin(entry.load())
            if plugin is None:
                break
            return plugin
    else:
        # Not an installed entry point: treat `name` as a module, importable from the project dir.
        added = cwd not in sys.path
        if added:
            sys.path.insert(0, cwd)
        try:
            importlib.invalidate_caches()
            if importlib.util.find_spec(name) is None:
                raise LookupError(f'Cannot find plugin "{name}" (install it in this project)')
            plugin = _as_plugin(getattr(importlib.import_module(name), "plugin", None))
            if plugin is not None:
                return plugin
        except ModuleNotFoundError as error:
            raise LookupError(
                f'Cannot find plugin "{name}" (install it in this project)'
            ) from error
        finally:
            if added:
                sys.path.remove(cwd)
    raise TypeError(f'Plugin "{name}" must export a Plugin as `plugin`')


def load_plugins(registry: Registry, uses: list[str], cwd: str) -> None:
    """Installs plugins named in `uses:` that aren't already built in.

    A name resolves to a `whiletrue.plugins` entry point, else to an importable module
    (looked up from the project directory) that exposes a `plugin` attribute.
    """
    for name in uses:
        if name in registry.plugins():
            continue
        registry.use(_find_plugin(name, cwd))
