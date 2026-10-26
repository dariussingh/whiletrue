"""Everything one item's improvement run shares: config, paths, the spend so far."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from whiletrue.core import CompositeContext
from whiletrue.rsi.harness2.config import Harness2Config
from whiletrue.rsi.harness2.ledger import Ledger
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.prompts import Profile
from whiletrue.rsi.harness2.surfaces import SurfaceSpec


class BudgetStop(Exception):
    """The item's rsi budget is spent; the item parks for a person."""

    def __init__(self, spent: float, budget: float) -> None:
        super().__init__(f"rsi budget ${budget:.2f} reached (spent ${spent:.2f})")
        self.spent = spent
        self.budget = budget


@dataclass
class Env:
    """The state of one item's recursion."""

    ctx: CompositeContext[Harness2Config]
    cfg: Harness2Config
    surface: SurfaceSpec
    profile: Profile
    task: str
    #: The item's own working directory: what rollouts are copied from and results applied to.
    cwd: Path
    #: Where this item's rollouts, stages and proposal live (outside the working directory).
    dir: Path
    library: Library
    ledger: Ledger
    gate: asyncio.Semaphore
    spent: float = 0.0
    notes: list[str] = field(default_factory=lambda: [])

    @property
    def item_id(self) -> str:
        return self.ctx.item_id

    @property
    def remaining(self) -> float:
        return max(self.cfg.budget_usd - self.spent, 0.0)

    def charge(self, usd: float) -> None:
        self.spent += usd

    def check_budget(self) -> None:
        """Raise `BudgetStop` before starting more work once the budget is used up."""
        if self.spent >= self.cfg.budget_usd:
            raise BudgetStop(self.spent, self.cfg.budget_usd)

    def log(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.ctx.log("info", f"rsi: {message}", data)
