from typing import Any

import pytest

from whiletrue.core import ParkRecord, Registry
from whiletrue.core.wake import (
    WakeCondition,
    WakeContext,
    WakeResult,
    evaluate_wake,
    parse_wake,
    wake_to_dict,
)


def test_parses_every_spelling() -> None:
    assert parse_wake("cli").conditions == ()
    assert parse_wake(["cli"]).mode == "any"
    spec = parse_wake({"any": ["cli", {"github.pr_review": "approved"}, "timer"]})
    assert spec.mode == "any"
    assert spec.conditions == (
        WakeCondition("github.pr_review", "approved"),
        WakeCondition("timer", None),
    )
    assert parse_wake({"all": [{"file_exists": "a.txt"}]}).mode == "all"
    assert wake_to_dict(spec)["conditions"][0] == {"name": "github.pr_review", "arg": "approved"}


@pytest.mark.parametrize(
    "bad",
    [{"any": [], "all": []}, {"some": ["cli"]}, [], 3, [["x"]], [{"a": 1, "b": 2}], None],
)
def test_rejects_malformed_specs(bad: Any) -> None:
    with pytest.raises(ValueError, match="wake|condition"):
        parse_wake(bad)


class Checker:
    def __init__(self, result: WakeResult | None) -> None:
        self.result = result
        self.args: list[Any] = []

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        self.args.append(arg)
        return self.result


def context(**checkers: Checker) -> WakeContext:
    registry = Registry()
    for name, checker in checkers.items():
        registry.register_provider("wake", name.replace("_", "."), checker)
    park = ParkRecord(1, "r", "7", "gate", "pr", {}, "waiting", 0)
    return WakeContext(".", lambda: 0, "7", park, registry, {"id": "7"})


async def test_any_returns_the_first_condition_that_fires() -> None:
    quiet, firing = Checker(None), Checker(WakeResult("approved", "lgtm", by="a"))
    ctx = context(a_quiet=quiet, a_firing=firing)
    spec = parse_wake({"any": [{"a.quiet": 1}, {"a.firing": 2}]})
    result = await evaluate_wake(spec, ctx)
    assert result == WakeResult("approved", "lgtm", by="a")
    assert quiet.args == [1] and firing.args == [2]


async def test_all_needs_every_condition_and_a_rejection_wins() -> None:
    ok, no = Checker(WakeResult("approved")), Checker(WakeResult("rejected", "nope"))
    ctx = context(a_ok=ok, a_no=no, a_quiet=Checker(None))
    both = parse_wake({"all": [{"a.ok": None}, {"a.no": None}]})
    assert (await evaluate_wake(both, ctx)) == WakeResult("rejected", "nope")
    waiting = parse_wake({"all": [{"a.ok": None}, {"a.quiet": None}]})
    assert await evaluate_wake(waiting, ctx) is None


async def test_nothing_to_check_or_nothing_registered_waits() -> None:
    assert await evaluate_wake(parse_wake("cli"), context()) is None
    assert await evaluate_wake(parse_wake({"any": [{"missing": 1}]}), context()) is None
    assert await evaluate_wake(parse_wake({"all": [{"missing": 1}]}), context()) is None
