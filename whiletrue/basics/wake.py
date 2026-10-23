from __future__ import annotations

from pathlib import Path
from typing import Any

from whiletrue.core import WakeContext, WakeResult, parse_duration


class FileExistsWake:
    """Approves when a file appears (relative to the project): a build artifact, a sign-off file."""

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        if not isinstance(arg, str) or not arg:
            return None
        if (Path(ctx.cwd) / arg).exists():
            return WakeResult("approved", f"{arg} exists", by="file_exists")
        return None


class TimerWake:
    """Approves once a duration has passed since the item was parked (a cooling-off period)."""

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        waited = ctx.now() - ctx.park.created_at
        if waited >= parse_duration(arg):
            return WakeResult("approved", f"{arg} elapsed", by="timer")
        return None
