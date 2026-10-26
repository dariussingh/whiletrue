from pathlib import Path

import pytest

from whiletrue.rsi.harness2 import runmark
from whiletrue.rsi.harness2.ledger import BudgetError, Ledger, expected_rollouts, expected_sessions


def test_fingerprint_is_stable_and_sensitive() -> None:
    assert runmark.fingerprint(None) == "base"
    a = runmark.fingerprint({"CLAUDE.md": "x", "b": "y"})
    assert a == runmark.fingerprint({"b": "y", "CLAUDE.md": "x"})
    assert a != runmark.fingerprint({"CLAUDE.md": "x", "b": "z"})
    assert a != runmark.fingerprint({"CLAUDE.md": "xb", "": "y"})


def test_a_finished_rollout_is_reusable_only_by_the_same_harness(tmp_path: Path) -> None:
    where = tmp_path / "r"
    assert not runmark.is_complete(where)
    assert "never finished" in runmark.why_not(where)
    runmark.mark_complete(where, run_id="r1", harness={"CLAUDE.md": "x"}, extra={"k": 1})
    mark = runmark.read_mark(where)
    assert mark is not None and mark.run_id == "r1" and mark.extra == {"k": 1}
    assert runmark.is_complete(where, harness={"CLAUDE.md": "x"})
    assert not runmark.is_complete(where, harness={"CLAUDE.md": "y"})
    assert runmark.is_complete(where, harness={"CLAUDE.md": "y"}, match_harness=False)
    assert runmark.why_not(where, harness={"CLAUDE.md": "x"}) == "reusable"
    assert "different configuration" in runmark.why_not(where, harness=None)


def test_a_marker_of_another_version_or_a_broken_one_is_ignored(tmp_path: Path) -> None:
    where = tmp_path / "r"
    where.mkdir()
    (where / runmark.MARK).write_text("not json")
    assert runmark.read_mark(where) is None
    (where / runmark.MARK).write_text(
        '{"version": 99, "run_id": "x", "harness": "base", "at": 1.0}'
    )
    assert not runmark.is_complete(where)
    assert "marker version 99" in runmark.why_not(where)


def full_ledger(mode: str = "parallel", k: int = 2) -> Ledger:
    ledger = Ledger(mode, "item-1", k)  # type: ignore[arg-type]
    for i in range(k):
        ledger.rollout(f"base{i + 1}", 0.1)
        ledger.rollout(f"inv{i + 1}", 0.1, cached=True)
        ledger.rollout(f"dom{i + 1}", 0.1)
        ledger.rollout(f"agg{i + 1}", 0.1)
        ledger.session(f"inv{i + 1}", 0.2)
        ledger.session(f"dom{i + 1}", 0.2, attempts=2)
        ledger.session(f"agg{i + 1}", 0.2)
    return ledger


def test_the_budget_identity_is_four_k_and_three_k() -> None:
    assert (expected_rollouts(2), expected_sessions(2)) == (8, 6)
    ledger = full_ledger()
    ledger.assert_identity()
    assert (ledger.rollouts, ledger.sessions) == (8, 6)
    assert ledger.usd == pytest.approx(0.8 + 1.2)
    assert ledger.usd_of("rollout") == pytest.approx(0.8)
    assert ledger.labels("session")[0] == "inv1"
    summary = ledger.summary()
    assert summary["expected_rollouts"] == 8 and summary["sessions"] == 6


def test_a_missing_rollout_breaks_the_identity() -> None:
    ledger = full_ledger()
    ledger.entries.pop(0)
    with pytest.raises(BudgetError, match=r"R=7 \(want 8\)"):
        ledger.assert_identity()


def test_a_duplicate_label_is_caught_even_when_the_counts_match() -> None:
    ledger = full_ledger(k=1)
    ledger.entries[0].label = "inv1"
    with pytest.raises(BudgetError, match="DUPLICATE"):
        ledger.assert_identity()
