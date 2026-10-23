"""Wake conditions: what ends a human gate's wait.

A person can always decide through the CLI (`cli`). Other conditions are checked by providers
registered under kind `wake` (a file appearing, a timer, a GitHub review, a plugin's own check).
A checker looks at the world and returns a decision, or None to keep waiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from whiletrue.core.history import Decision, ParkRecord

if TYPE_CHECKING:
    from collections.abc import Callable

    from whiletrue.core.registry import Registry


@dataclass(frozen=True)
class WakeCondition:
    name: str
    #: The condition's argument (`approved`, a path, `10m`, ...), or None.
    arg: Any = None


@dataclass(frozen=True)
class WakeSpec:
    mode: Literal["any", "all"]
    #: Conditions that are checked automatically (everything except `cli`).
    conditions: tuple[WakeCondition, ...]


@dataclass(frozen=True)
class WakeResult:
    decision: Decision
    note: str | None = None
    #: Who or what decided: `github`, `file_exists`, ...
    by: str = "wake"


@dataclass
class WakeContext:
    cwd: str
    now: Callable[[], int]
    item_id: str
    park: ParkRecord
    registry: Registry
    #: The item's payload (issue number, PR url once known, ...).
    item: dict[str, Any]
    #: Whatever the step before the gate produced.
    input: Any = None


class WakeChecker(Protocol):
    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None: ...


def parse_wake(raw: Any) -> WakeSpec:
    """`cli`, `[cli, ...]`, `{any: [...]}` or `{all: [...]}`; each entry is a name or `{name: arg}`.

    Raises ValueError with a message fit for a config error.
    """
    mode: Literal["any", "all"] = "any"
    entries: Any = raw
    if isinstance(raw, dict):
        keys = list(cast(dict[str, Any], raw))
        if len(keys) != 1 or keys[0] not in ("any", "all"):
            raise ValueError("`wake` must be a list, or a mapping with a single `any` or `all` key")
        mode = keys[0]
        entries = cast(dict[str, Any], raw)[mode]
    if isinstance(entries, str):
        entries = [entries]
    if not isinstance(entries, list) or len(cast(list[Any], entries)) == 0:
        raise ValueError("`wake` needs at least one condition")
    conditions: list[WakeCondition] = []
    for entry in entries:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(entry, str):
            name, arg = entry, None
        elif isinstance(entry, dict) and len(entry) == 1:  # pyright: ignore[reportUnknownArgumentType]
            ((name, arg),) = entry.items()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        else:
            raise ValueError("A wake condition is a name or a single `name: argument` mapping")
        if name != "cli":
            conditions.append(WakeCondition(str(name), arg))  # pyright: ignore[reportUnknownArgumentType]
    return WakeSpec(mode, tuple(conditions))


def wake_to_dict(spec: WakeSpec) -> dict[str, Any]:
    return {
        "mode": spec.mode,
        "conditions": [{"name": c.name, "arg": c.arg} for c in spec.conditions],
    }


async def evaluate_wake(spec: WakeSpec, ctx: WakeContext) -> WakeResult | None:
    """The automatic decision, if the conditions are met (`cli` decisions are the gate's job)."""
    if not spec.conditions:
        return None
    results: list[WakeResult] = []
    for condition in spec.conditions:
        checker: WakeChecker | None = ctx.registry.get_provider("wake", condition.name)
        if checker is None:
            continue
        result = await checker.check(condition.arg, ctx)
        if result is None:
            if spec.mode == "all":
                return None
            continue
        if spec.mode == "any":
            return result
        results.append(result)
    if spec.mode == "all" and results:
        rejected = next((r for r in results if r.decision == "rejected"), None)
        return rejected or results[0]
    return None
