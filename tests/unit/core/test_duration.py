import math

import pytest

from whiletrue.core import parse_duration


@pytest.mark.parametrize(
    ("text", "ms"),
    [
        ("250ms", 250),
        ("30s", 30_000),
        ("90m", 5_400_000),
        ("1h", 3_600_000),
        ("1.5s", 1500),
        (" 2s ", 2000),
    ],
)
def test_parses_units(text: str, ms: int) -> None:
    assert parse_duration(text) == ms


def test_passes_numbers_through_as_ms() -> None:
    assert parse_duration(1234) == 1234
    assert parse_duration(12.9) == 12


@pytest.mark.parametrize("bad", ["", "abc", "10", "10d", "-5s"])
def test_rejects_bad_strings(bad: str) -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(bad)


def test_rejects_negative_or_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(-1)
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(math.nan)
