"""The `whiletrue` command-line interface."""

from whiletrue.cli.app import EXIT, Io, entrypoint, main
from whiletrue.cli.registry import RegistryOptions, create_default_registry, load_plugins

__all__ = [
    "EXIT",
    "Io",
    "RegistryOptions",
    "create_default_registry",
    "entrypoint",
    "load_plugins",
    "main",
]
