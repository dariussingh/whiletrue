"""Sources turn a pipeline's `source:` block into the items it processes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from whiletrue.core.runtime import RunItem

if TYPE_CHECKING:
    from whiletrue.core.registry import Registry


class SourceProvider(Protocol):
    async def fetch(self, config: dict[str, Any], cwd: str) -> list[RunItem]: ...


class SourceError(Exception):
    pass


def source_spec(extra: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """`source: {name: {...config}}` -> (name, config). None when the pipeline has no source."""
    raw = extra.get("source")
    if raw is None:
        return None
    if not isinstance(raw, dict) or len(raw) != 1:  # pyright: ignore[reportUnknownArgumentType]
        raise SourceError(
            "`source` must be a mapping with exactly one provider, e.g. `json: {...}`"
        )
    ((name, config),) = raw.items()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    if config is not None and not isinstance(config, dict):
        raise SourceError(f"`source.{name}` must be a mapping")
    return str(name), dict(config or {})  # pyright: ignore[reportUnknownArgumentType]


async def fetch_items(registry: Registry, extra: dict[str, Any], cwd: str) -> list[RunItem] | None:
    """The items from the pipeline's source, or None when it has none (one implicit item)."""
    spec = source_spec(extra)
    if spec is None:
        return None
    name, config = spec
    provider: SourceProvider | None = registry.get_provider("source", name)
    if provider is None:
        raise SourceError(f'Unknown source "{name}"')
    return await provider.fetch(config, cwd)
