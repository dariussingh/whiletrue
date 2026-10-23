from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


#: Rough list prices by model family, matched by substring of the model id. These drive the
#: *estimate* that enforces budgets in interactive sessions; they are not billing data.
DEFAULT_PRICES: dict[str, Price] = {
    "opus": Price(input=15.0, output=75.0, cache_read=1.5, cache_write=18.75),
    "sonnet": Price(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
    "haiku": Price(input=1.0, output=5.0, cache_read=0.1, cache_write=1.25),
}


@dataclass
class Usage:
    #: Assistant messages (model calls) so far.
    turns: int = 0
    cost_usd: float = 0.0
    #: The part of `cost_usd` spent after a human steered the session.
    steered_cost_usd: float = 0.0

    @property
    def agent_cost_usd(self) -> float:
        return self.cost_usd - self.steered_cost_usd


def _price(model: str, prices: dict[str, Price]) -> Price:
    for family, price in prices.items():
        if family in model:
            return price
    # Unknown model: assume the dearest family so a budget is never silently under-counted.
    return max(prices.values(), key=lambda p: p.output)


def _epoch(stamp: Any) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _obj(value: Any) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _tokens(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0


def read_usage(
    path: str | None,
    prices: dict[str, Price] | None = None,
    *,
    steered_since: float | None = None,
) -> Usage:
    """Estimates turns and spend from a Claude Code session transcript (JSONL).

    A message is logged once per content block, so messages are deduplicated by id. Spend after
    `steered_since` (epoch seconds) is reported separately.
    """
    table = prices if prices is not None else DEFAULT_PRICES
    usage = Usage()
    if path is None:
        return usage
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return usage
    seen: set[str] = set()
    for raw in text.splitlines():
        entry = _obj(_loads(raw))
        if entry is None or entry.get("type") != "assistant":
            continue
        message = _obj(entry.get("message"))
        if message is None:
            continue
        message_id = message.get("id")
        if isinstance(message_id, str):
            if message_id in seen:
                continue
            seen.add(message_id)
        usage.turns += 1
        counts = _obj(message.get("usage"))
        if counts is None:
            continue
        model = message.get("model")
        price = _price(model if isinstance(model, str) else "", table)
        cost = (
            _tokens(counts, "input_tokens") * price.input
            + _tokens(counts, "output_tokens") * price.output
            + _tokens(counts, "cache_read_input_tokens") * price.cache_read
            + _tokens(counts, "cache_creation_input_tokens") * price.cache_write
        ) / 1_000_000
        usage.cost_usd += cost
        when = _epoch(entry.get("timestamp"))
        if steered_since is not None and when is not None and when >= steered_since:
            usage.steered_cost_usd += cost
    return usage
