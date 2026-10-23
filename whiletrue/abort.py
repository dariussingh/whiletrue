"""Cooperative cancellation, modelled on the web's AbortSignal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class AbortSignal:
    """A one-way flag that can be awaited. Once aborted it stays aborted."""

    def __init__(self) -> None:
        self._aborted = False
        self._reason: str | None = None
        self._event = asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._timer: asyncio.TimerHandle | None = None
        self._detach: list[Callable[[], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    def abort(self, reason: str = "aborted") -> None:
        if self._aborted:
            return
        self._aborted = True
        self._reason = reason
        self._event.set()
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()

    async def wait(self) -> None:
        """Returns once the signal is aborted."""
        await self._event.wait()

    def on_abort(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Calls `callback` on abort (immediately if already aborted). Returns an unsubscribe."""
        if self._aborted:
            callback()
            return lambda: None
        self._callbacks.append(callback)

        def remove() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return remove

    def dispose(self) -> None:
        """Releases timers and subscriptions held by a derived signal (timeout / any)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for detach in self._detach:
            detach()
        self._detach.clear()

    @staticmethod
    def any(*signals: AbortSignal) -> AbortSignal:
        """A signal that aborts as soon as any of `signals` does."""
        derived = AbortSignal()
        for source in signals:
            derived._detach.append(source.on_abort(derived.abort))
        return derived

    @staticmethod
    def timeout(ms: float) -> AbortSignal:
        """A signal that aborts after `ms` milliseconds."""
        derived = AbortSignal()
        derived._timer = asyncio.get_running_loop().call_later(
            ms / 1000, lambda: derived.abort("timeout")
        )
        return derived
