from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Every module has these outcome ports in addition to its data ports.
OUTCOME_PORTS = ("ok", "fail", "needs_human", "skip", "rejected")

ItemState = Literal["pending", "ready", "running", "parked", "done", "failed", "cancelled"]

#: What a composite does with an unwired `fail`.
OnFail = Literal["park", "drop", "propagate"]


@dataclass(frozen=True)
class OutcomePolicy:
    on_fail: OnFail = "park"


@dataclass(frozen=True)
class Resolution:
    action: Literal["continue", "route", "propagate", "exit"]
    target: str | None = None
    port: str | None = None
    state: ItemState | None = None


def is_outcome_port(port: str) -> bool:
    return port in OUTCOME_PORTS


def item_state_for(port: str, on_fail: OnFail) -> ItemState:
    """Terminal item state for an outcome that leaves the pipeline."""
    match port:
        case "ok" | "skip":
            return "done"
        case "fail":
            return "parked" if on_fail == "park" else "failed"
        case _:
            return "parked"


def resolve_outcome(port: str, wires: dict[str, str], policy: OutcomePolicy) -> Resolution:
    """Decide what happens when a child fires `port`. A wired port always routes to its target.

    Unwired: ok/skip and custom data ports continue; fail/needs_human/rejected follow the policy.
    """
    target = wires.get(port)
    if target is not None:
        return Resolution("route", target=target)
    if not is_outcome_port(port) or port in ("ok", "skip"):
        return Resolution("continue")
    if policy.on_fail == "propagate":
        return Resolution("propagate", port=port)
    return Resolution("exit", port=port, state=item_state_for(port, policy.on_fail))


class InvalidTransition(Exception):
    pass


#: Where an item may go next. `done` is final; `failed` and `cancelled` can be rerun.
_TRANSITIONS: dict[ItemState, frozenset[ItemState]] = {
    "pending": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"running", "pending", "cancelled"}),
    "running": frozenset({"parked", "done", "failed", "cancelled"}),
    "parked": frozenset({"running", "ready", "cancelled"}),
    "done": frozenset(),
    "failed": frozenset({"ready", "cancelled"}),
    "cancelled": frozenset({"ready"}),
}


def can_transition(old: ItemState, new: ItemState) -> bool:
    return new in _TRANSITIONS[old]


def transition(old: ItemState, new: ItemState) -> ItemState:
    """`new` if the move is allowed, else InvalidTransition."""
    if not can_transition(old, new):
        raise InvalidTransition(f"Invalid item transition {old} -> {new}")
    return new
