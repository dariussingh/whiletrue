import json
from pathlib import Path

import pytest

from whiletrue.claude_code.transcript import DEFAULT_PRICES, Price, read_usage


def line(msg_id: str, ts: str, *, model: str = "claude-sonnet-5-5", **usage: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "message": {"id": msg_id, "model": model, "usage": usage, "content": []},
        }
    )


def write(path: Path, *lines: str) -> str:
    path.write_text("\n".join(lines) + "\n")
    return str(path)


PRICES = {"sonnet": Price(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)}


def test_counts_each_assistant_message_once_and_prices_its_tokens(tmp_path: Path) -> None:
    path = write(
        tmp_path / "t.jsonl",
        line("m1", "2026-10-23T09:00:00.000Z", input_tokens=1_000_000, output_tokens=0),
        # The same message is logged once per content block.
        line("m1", "2026-10-23T09:00:00.100Z", input_tokens=1_000_000, output_tokens=0),
        line(
            "m2",
            "2026-10-23T09:00:05.000Z",
            output_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
        ),
        json.dumps({"type": "user", "timestamp": "2026-10-23T09:00:06.000Z"}),
    )
    usage = read_usage(path, PRICES)
    assert usage.turns == 2
    assert usage.cost_usd == pytest.approx(3.0 + 15.0 + 0.3 + 3.75)
    assert usage.steered_cost_usd == 0


def test_splits_spend_after_the_steer_time(tmp_path: Path) -> None:
    path = write(
        tmp_path / "t.jsonl",
        line("m1", "2026-10-23T09:00:00.000Z", output_tokens=1_000_000),
        line("m2", "2026-10-23T09:10:00.000Z", output_tokens=1_000_000),
    )
    steer = 1792746600.0  # 2026-10-23T09:10:00Z
    usage = read_usage(path, PRICES, steered_since=steer)
    assert usage.cost_usd == pytest.approx(30.0)
    assert usage.steered_cost_usd == pytest.approx(15.0)
    assert usage.agent_cost_usd == pytest.approx(15.0)


def test_unknown_models_are_priced_at_the_most_expensive_family(tmp_path: Path) -> None:
    path = write(
        tmp_path / "t.jsonl",
        line("m1", "2026-10-23T09:00:00Z", model="mystery-9", output_tokens=1_000_000),
    )
    assert read_usage(path, DEFAULT_PRICES).cost_usd == pytest.approx(
        max(p.output for p in DEFAULT_PRICES.values())
    )


def test_tolerates_missing_and_damaged_transcripts(tmp_path: Path) -> None:
    assert read_usage(str(tmp_path / "nope.jsonl"), PRICES).turns == 0
    assert read_usage(None, PRICES).cost_usd == 0
    path = write(
        tmp_path / "t.jsonl",
        "not json",
        "[1]",
        json.dumps({"type": "assistant", "message": {"id": "x"}}),
        json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 5}}}),
        line("m1", "garbage-time", output_tokens=1_000_000),
    )
    usage = read_usage(path, PRICES, steered_since=1.0)
    # Damaged lines are skipped; a bad timestamp is treated as not steered.
    assert usage.turns == 3
    assert usage.cost_usd == pytest.approx(15.0, abs=0.01)
    assert usage.steered_cost_usd == 0
