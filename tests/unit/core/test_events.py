import time

from whiletrue.core import EventBus, WtEvent


def test_stamps_events_and_delivers_in_subscription_order() -> None:
    bus = EventBus(now=lambda: 42)
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(f"a:{e.type}:{e.ts}"))
    bus.subscribe(lambda e: seen.append(f"b:{e.type}"))
    event = bus.emit("log", "r", {"msg": "hi"})
    assert event.ts == 42
    assert seen == ["a:log:42", "b:log"]


def test_keeps_an_explicit_timestamp() -> None:
    assert EventBus(now=lambda: 1).emit("log", "r", ts=9).ts == 9


def test_defaults_to_the_wall_clock() -> None:
    before = int(time.time() * 1000)
    assert EventBus().emit("log", "r").ts >= before


def test_unsubscribes() -> None:
    bus = EventBus()
    seen: list[WtEvent] = []
    off = bus.subscribe(seen.append)
    bus.emit("log", "r")
    off()
    off()  # idempotent
    bus.emit("log", "r")
    assert len(seen) == 1


def test_isolates_listener_failures_and_reports_them() -> None:
    errors: list[Exception] = []
    bus = EventBus(on_error=lambda e, _event: errors.append(e))
    seen: list[WtEvent] = []

    def boom(_e: WtEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe(boom)
    bus.subscribe(seen.append)
    bus.emit("log", "r")
    assert len(seen) == 1
    assert len(errors) == 1


def test_swallows_listener_failures_silently_without_on_error() -> None:
    bus = EventBus()

    def boom(_e: WtEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe(boom)
    bus.emit("log", "r")


def test_carries_item_and_step_ids() -> None:
    event = EventBus().emit("step.started", "r", {"a": 1}, item_id="i", step_id=3)
    assert (event.item_id, event.step_id, event.data) == ("i", 3, {"a": 1})
