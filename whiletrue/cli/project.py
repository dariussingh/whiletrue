"""Loading a project: its pipeline, plugins, pins and the `rsi:` setting."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from whiletrue.cli import ops
from whiletrue.cli.common import EXIT, Io
from whiletrue.cli.registry import (
    RegistryOptions,
    create_default_registry,
    load_plugins,
    whiletrue_version,
)
from whiletrue.core import (
    LOCK_FILE,
    ConfigError,
    LockError,
    PipelineConfig,
    Registry,
    check_lock,
    format_diagnostic,
    has_errors,
    load_pipeline,
    read_lock,
    sha256_file,
    validate_pipeline,
)
from whiletrue.rsi import SettingError, apply_rsi, rsi_plugin


@dataclass
class Loaded:
    config: PipelineConfig
    registry: Registry
    cwd: str


def _with_rsi(registry: Registry, config: PipelineConfig) -> PipelineConfig:
    """Apply the `rsi:` setting. With it off (or absent) nothing is registered or changed."""
    if config.extra.get("rsi") in (None, False):
        return config
    if "whiletrue.rsi" not in registry.plugins():
        registry.use(rsi_plugin, whiletrue_version())
    return apply_rsi(config, registry)


def _check_pins(io: Io, registry: Registry, cwd: str) -> bool:
    try:
        pinned = read_lock(Path(cwd) / LOCK_FILE)
    except LockError as error:
        io.err(f"{error}\n")
        return False
    if pinned is None:
        return True
    problems = check_lock(pinned, registry.plugin_versions())
    if problems:
        io.err("".join(f"{p}\n" for p in problems) + "Run `whiletrue lock` to update it.\n")
    return not problems


def load(io: Io, file: str, *, check_pins: bool = True) -> Loaded | int:
    """The project's pipeline and registry, or an exit code after printing what is wrong."""
    path = (Path(io.cwd) / file).resolve()
    try:
        config = load_pipeline(path)
    except ConfigError as error:
        io.err(f"{error}\n")
        return EXIT.failed
    except OSError as error:
        io.err(f"Cannot load {file}: {error}\n")
        return EXIT.failed
    cwd = str(path.parent)
    registry = create_default_registry(
        RegistryOptions(project_dir=cwd, pane_host=io.pane_host, github=io.github)
    )
    try:
        load_plugins(registry, config.uses, cwd)
    except Exception as error:
        io.err(f"{error}\n")
        return EXIT.failed
    try:
        config = _with_rsi(registry, config)
    except SettingError as error:
        io.err(f"{path}: {error}\n")
        return EXIT.failed
    if check_pins and not _check_pins(io, registry, cwd):
        return EXIT.failed
    return Loaded(config, registry, cwd)


def validated(io: Io, loaded: Loaded) -> bool:
    diagnostics = validate_pipeline(loaded.config, loaded.registry, cwd=loaded.cwd)
    for d in diagnostics:
        io.err(f"{format_diagnostic(d)}\n")
    return not has_errors(diagnostics)


async def with_loaded(io: Io, file: str, action: Callable[[Loaded], Awaitable[int]]) -> int:
    """Load and validate the project, then run `action` on it."""
    loaded = load(io, file)
    if isinstance(loaded, int):
        return loaded
    if not validated(io, loaded):
        return EXIT.failed
    return await action(loaded)


def lock_hash(cwd: str) -> str | None:
    path = Path(cwd) / LOCK_FILE
    return sha256_file(path) if path.is_file() else None


async def locked(io: Io, loaded: Loaded, action: Callable[[], Awaitable[int]]) -> int:
    """Runs an action that changes a run while holding the project's run lock."""
    lock = ops.RunLock(loaded.cwd)
    if not lock.acquire():
        io.err(f"Another whiletrue run is active in this project (pid {lock.holder()})\n")
        return EXIT.failed
    try:
        return await action()
    finally:
        lock.release()
