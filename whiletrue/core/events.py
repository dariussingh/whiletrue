from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

#: Every started/output/cost/error/approval/takeover is an event; the log is append-only.
EventType = Literal[
    "run.started",
    "run.finished",
    "run.hashes",
    "item.state",
    "step.started",
    "step.finished",
    "step.error",
    "step.cached",
    "step.journaled",
    "park.created",
    "park.decided",
    "comment.added",
    "comment.answered",
    "step.replayed",
    "agent.event",
    "log",
]


@dataclass
class WtEvent:
    type: EventType
    run_id: str
    #: Epoch milliseconds.
    ts: int
    data: dict[str, Any] = field(default_factory=lambda: {})
    item_id: str | None = None
    step_id: int | None = None


EventListener = Callable[[WtEvent], None]


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


class EventBus:
    def __init__(
        self,
        *,
        now: Callable[[], int] = _wall_clock_ms,
        on_error: Callable[[Exception, WtEvent], None] | None = None,
    ) -> None:
        self._listeners: list[EventListener] = []
        self._now = now
        self._on_error = on_error

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def emit(
        self,
        type: EventType,
        run_id: str,
        data: dict[str, Any] | None = None,
        *,
        item_id: str | None = None,
        step_id: int | None = None,
        ts: int | None = None,
    ) -> WtEvent:
        """Delivers synchronously, in subscription order. A failing listener never breaks us."""
        event = WtEvent(
            type=type,
            run_id=run_id,
            ts=self._now() if ts is None else ts,
            data=data if data is not None else {},
            item_id=item_id,
            step_id=step_id,
        )
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as error:
                if self._on_error is not None:
                    self._on_error(error, event)
        return event
