import asyncio

from whiletrue.abort import AbortSignal


def test_starts_unaborted_and_stays_aborted() -> None:
    s = AbortSignal()
    assert not s.aborted
    assert s.reason is None
    s.abort("because")
    s.abort("again")
    assert s.aborted
    assert s.reason == "because"


async def test_wait_returns_once_aborted() -> None:
    s = AbortSignal()
    asyncio.get_running_loop().call_later(0.01, s.abort)
    await asyncio.wait_for(s.wait(), 1)
    assert s.aborted


def test_on_abort_fires_once_and_can_unsubscribe() -> None:
    s = AbortSignal()
    calls: list[int] = []
    s.on_abort(lambda: calls.append(1))
    off = s.on_abort(lambda: calls.append(2))
    off()
    off()
    s.abort()
    s.abort()
    assert calls == [1]


def test_on_abort_on_an_aborted_signal_fires_immediately() -> None:
    s = AbortSignal()
    s.abort()
    calls: list[int] = []
    off = s.on_abort(lambda: calls.append(1))
    off()
    assert calls == [1]


def test_any_aborts_when_any_source_does() -> None:
    a, b = AbortSignal(), AbortSignal()
    derived = AbortSignal.any(a, b)
    assert not derived.aborted
    b.abort()
    assert derived.aborted
    derived.dispose()


def test_any_of_an_already_aborted_signal_is_aborted() -> None:
    a = AbortSignal()
    a.abort()
    assert AbortSignal.any(a).aborted


async def test_timeout_aborts_after_the_delay() -> None:
    s = AbortSignal.timeout(20)
    assert not s.aborted
    await asyncio.wait_for(s.wait(), 1)
    assert s.reason == "timeout"


async def test_dispose_cancels_the_timer_and_detaches_sources() -> None:
    source = AbortSignal()
    timed = AbortSignal.timeout(20)
    derived = AbortSignal.any(source, timed)
    timed.dispose()
    derived.dispose()
    await asyncio.sleep(0.05)
    assert not timed.aborted
    source.abort()
    assert not derived.aborted
    derived.dispose()  # idempotent
