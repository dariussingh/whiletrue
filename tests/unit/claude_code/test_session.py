import json
from pathlib import Path
from typing import Any

import pytest

from whiletrue.claude_code.learnings import LearningsStore
from whiletrue.claude_code.session import SessionOutcome, SessionSpec, StepSession
from whiletrue.core import AgentLimits, CapValidation, create_builtin_caps


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def verdict_validator(value: Any) -> CapValidation:
    return create_builtin_caps().validate("Verdict", value)


def make(tmp_path: Path, **over: Any) -> tuple[StepSession, list[dict[str, Any]], Clock]:
    events: list[dict[str, Any]] = []
    clock = Clock()
    values: dict[str, Any] = {
        "key": "run:item:node",
        "item": {"id": "7", "title": "T"},
        "on_event": events.append,
        "clock": clock,
        "learnings": LearningsStore(tmp_path / "learnings.jsonl"),
        "provenance": {"run": "r1", "item": "7", "step": "node"},
    }
    values.update(over)
    return StepSession(SessionSpec(**values)), events, clock


async def start(session: StepSession, transcript: str | None = None) -> None:
    await session.on_hook(
        "SessionStart",
        {"session_id": "sid", "transcript_path": transcript, "source": "startup"},
    )
    await session.on_hook("UserPromptSubmit", {"prompt": "initial prompt"})


def outcome(session: StepSession) -> SessionOutcome:
    assert session.outcome.done()
    return session.outcome.result()


def transcript(tmp_path: Path, *, output_tokens: int, count: int = 1) -> str:
    path = tmp_path / "t.jsonl"
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-10-23T09:00:00Z",
            "message": {
                "id": f"m{i}",
                "model": "claude-sonnet-5-5",
                "usage": {"output_tokens": output_tokens},
            },
        }
        for i in range(count)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


# ---- tools ---------------------------------------------------------------------------------


async def test_advertises_the_four_tools_and_submit_carries_the_cap_schema(tmp_path: Path) -> None:
    schema = create_builtin_caps().to_json_schema("Verdict")
    session, _, _ = make(tmp_path, result_schema=schema, result_cap="Verdict")
    tools = {t["name"]: t for t in session.tools()}
    assert set(tools) == {
        "whiletrue_submit",
        "whiletrue_learn",
        "whiletrue_ask",
        "whiletrue_context",
    }
    submit = tools["whiletrue_submit"]["inputSchema"]
    assert submit["required"] == ["result"]
    assert submit["properties"]["result"] == schema
    assert "Verdict" in tools["whiletrue_submit"]["description"]


async def test_an_untyped_submit_accepts_anything(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    submit = session.tools()[0]["inputSchema"]["properties"]["result"]
    assert "type" not in submit
    answer = await session.on_tool("whiletrue_submit", {"result": "done"})
    assert not answer.is_error


async def test_submit_validates_against_the_cap_and_lets_the_agent_retry(tmp_path: Path) -> None:
    session, events, _ = make(tmp_path, validate=verdict_validator, result_cap="Verdict")
    bad = await session.on_tool("whiletrue_submit", {"result": {"pass": "yes"}})
    assert bad.is_error and "pass" in bad.text
    assert not session.submitted
    good = await session.on_tool("whiletrue_submit", {"result": {"pass": True}})
    assert not good.is_error and session.submitted
    assert {"kind": "submit"} in [{"kind": e["kind"]} for e in events]
    await start(session)
    await session.on_hook("Stop", {"last_assistant_message": "all done"})
    done = outcome(session)
    assert (done.status, done.value, done.text) == (
        "submitted",
        {"pass": True, "reason": None},
        "all done",
    )


async def test_submit_requires_a_result_argument(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    answer = await session.on_tool("whiletrue_submit", {})
    assert answer.is_error and "result" in answer.text


async def test_learn_appends_a_note_with_provenance(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    answer = await session.on_tool("whiletrue_learn", {"note": "use uv, not pip"})
    assert not answer.is_error
    rows = [json.loads(line) for line in (tmp_path / "learnings.jsonl").read_text().splitlines()]
    assert rows[0]["note"] == "use uv, not pip"
    assert rows[0]["run"] == "r1" and rows[0]["item"] == "7" and "at" in rows[0]
    assert (await session.on_tool("whiletrue_learn", {})).is_error


async def test_learn_without_a_store_still_answers(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path, learnings=None)
    assert (await session.on_tool("whiletrue_learn", {"note": "x"})).is_error


async def test_ask_parks_the_step_for_a_human(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    await start(session)
    answer = await session.on_tool("whiletrue_ask", {"question": "Which db?"})
    assert not answer.is_error
    await session.on_hook("Stop", {})
    done = outcome(session)
    assert (done.status, done.reason) == ("parked", "needs_human")
    assert done.detail == "Which db?"
    assert (await session.on_tool("whiletrue_ask", {})).is_error


async def test_context_returns_the_item_and_recent_learnings(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path, extra_context={"spec": "specs/a.md"})
    await session.on_tool("whiletrue_learn", {"note": "first"})
    answer = await session.on_tool("whiletrue_context", {})
    body = json.loads(answer.text)
    assert body["item"] == {"id": "7", "title": "T"}
    assert [n["note"] for n in body["learnings"]] == ["first"]
    assert body["spec"] == "specs/a.md"


async def test_unknown_tools_are_errors(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    answer = await session.on_tool("nope", {})
    assert answer.is_error and "nope" in answer.text


# ---- hooks: binding, events, stop ----------------------------------------------------------


async def test_session_start_binds_the_session_and_records_events(tmp_path: Path) -> None:
    session, events, _ = make(tmp_path)
    await start(session, "/t.jsonl")
    assert session.session_id == "sid" and session.transcript_path == "/t.jsonl"
    await session.on_hook("PostToolUse", {"tool_name": "Bash"})
    kinds = [e["kind"] for e in events]
    assert kinds == ["session_start", "tool_use"]
    assert events[1]["name"] == "Bash"
    assert session.tool_calls == 1


async def test_stop_nudges_until_a_result_is_submitted(tmp_path: Path) -> None:
    session, events, _ = make(tmp_path, max_nudges=2)
    await start(session)
    first = await session.on_hook("Stop", {"last_assistant_message": "hmm"})
    assert first is not None and first["decision"] == "block"
    assert "whiletrue_submit" in first["reason"]
    assert not session.outcome.done()
    await session.on_tool("whiletrue_submit", {"result": "ok"})
    assert await session.on_hook("Stop", {}) is None
    assert outcome(session).status == "submitted"
    assert [e["kind"] for e in events].count("nudge") == 1


async def test_stop_parks_the_step_after_the_nudges_run_out(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path, max_nudges=1)
    await start(session)
    assert (await session.on_hook("Stop", {})) is not None
    assert await session.on_hook("Stop", {"last_assistant_message": "still no"}) is None
    done = outcome(session)
    assert (done.status, done.reason) == ("parked", "needs_human")
    assert "1 reminder" in (done.detail or "")


async def test_a_steered_session_is_never_nudged(tmp_path: Path) -> None:
    session, events, clock = make(tmp_path)
    await start(session)
    clock.now += 5
    await session.on_hook("UserPromptSubmit", {"prompt": "actually, use sqlite"})
    assert session.steered
    assert await session.on_hook("Stop", {}) is None
    assert not session.outcome.done()
    assert next(e for e in events if e["kind"] == "steer")["prompt"] == "actually, use sqlite"
    await session.on_tool("whiletrue_submit", {"result": 1})
    await session.on_hook("Stop", {})
    assert outcome(session).status == "submitted"


async def test_the_session_ending_without_a_result_is_reported(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    await start(session)
    await session.on_hook("SessionEnd", {"reason": "other"})
    done = outcome(session)
    assert done.status == "ended" and "without submitting" in (done.detail or "")


async def test_the_session_ending_after_a_result_still_succeeds(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    await start(session)
    await session.on_tool("whiletrue_submit", {"result": "r"})
    await session.on_hook("SessionEnd", {})
    assert (outcome(session).status, outcome(session).value) == ("submitted", "r")


async def test_notifications_flag_the_pane(tmp_path: Path) -> None:
    flagged: list[str] = []
    cleared: list[bool] = []
    session, events, _ = make(
        tmp_path, on_attention=flagged.append, on_attention_cleared=lambda: cleared.append(True)
    )
    await session.on_hook("Notification", {"message": "Claude needs permission to use Bash"})
    assert flagged == ["Claude needs permission to use Bash"]
    assert session.needs_attention
    assert events[-1]["kind"] == "attention"
    await session.on_hook("UserPromptSubmit", {"prompt": "x"})
    assert not session.needs_attention and cleared == [True]


async def test_unknown_hook_events_are_ignored(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    assert await session.on_hook("SomethingNew", {}) is None


async def test_a_finished_session_ignores_late_events(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    await session.on_tool("whiletrue_submit", {"result": 1})
    await session.on_hook("Stop", {})
    await session.on_hook("SessionEnd", {})
    assert outcome(session).status == "submitted"


# ---- limits ---------------------------------------------------------------------------------


def denied(reply: dict[str, Any] | None) -> str:
    assert reply is not None
    out = reply["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


async def test_budget_exceeded_denies_tools_and_parks(tmp_path: Path) -> None:
    stops: list[str] = []
    path = transcript(tmp_path, output_tokens=1_000_000)  # ~ $15 on sonnet
    session, _, _ = make(tmp_path, limits=AgentLimits(budget_usd=1.0), on_limit=stops.append)
    await start(session, path)
    reason = denied(await session.on_hook("PreToolUse", {"tool_name": "Bash"}))
    assert "budget" in reason
    assert stops == ["budget_exceeded"]
    await session.on_hook("Stop", {})
    done = outcome(session)
    assert (done.status, done.reason) == ("parked", "budget_exceeded")
    assert done.cost_usd > 1.0


async def test_turn_limit_denies_tools(tmp_path: Path) -> None:
    # Three model calls were allowed; the fourth tool request is over the limit.
    path = transcript(tmp_path, output_tokens=1, count=4)
    session, _, _ = make(tmp_path, limits=AgentLimits(turns=3))
    await start(session, path)
    assert "turn" in denied(await session.on_hook("PreToolUse", {"tool_name": "Read"}))
    assert session.limit_hit == "turns_exceeded"


async def test_wall_limit_denies_tools(tmp_path: Path) -> None:
    session, _, clock = make(tmp_path, limits=AgentLimits(wall_ms=60_000))
    await start(session)
    assert await session.on_hook("PreToolUse", {"tool_name": "Read"}) is None
    clock.now += 61
    assert "wall" in denied(await session.on_hook("PreToolUse", {"tool_name": "Read"}))
    assert session.limit_hit == "wall_exceeded"


async def test_steering_lifts_every_limit_and_splits_the_cost(tmp_path: Path) -> None:
    path = transcript(tmp_path, output_tokens=1_000_000)
    session, events, clock = make(tmp_path, limits=AgentLimits(budget_usd=1.0, turns=1))
    await start(session, path)
    clock.now += 100
    await session.on_hook("UserPromptSubmit", {"prompt": "carry on"})
    assert await session.on_hook("PreToolUse", {"tool_name": "Bash"}) is None
    clock.now += 100
    await session.on_tool("whiletrue_submit", {"result": 1})
    await session.on_hook("Stop", {})
    done = outcome(session)
    assert done.status == "submitted" and done.steered
    assert done.cost_usd == pytest.approx(15.0)
    assert [e for e in events if e["kind"] == "steer"]


async def test_no_limits_means_tools_are_never_denied(tmp_path: Path) -> None:
    session, _, _ = make(tmp_path)
    await start(session)
    assert await session.on_hook("PreToolUse", {"tool_name": "Bash"}) is None


async def test_limit_hit_stops_further_denials_from_re_notifying(tmp_path: Path) -> None:
    stops: list[str] = []
    session, _, clock = make(tmp_path, limits=AgentLimits(wall_ms=1), on_limit=stops.append)
    await start(session)
    clock.now += 10
    await session.on_hook("PreToolUse", {})
    await session.on_hook("PreToolUse", {})
    assert stops == ["wall_exceeded"]


def test_learnings_store_returns_the_most_recent(tmp_path: Path) -> None:
    store = LearningsStore(tmp_path / "x" / "l.jsonl")
    assert store.recent(5) == []
    for i in range(4):
        store.append(f"n{i}", run="r")
    assert [n["note"] for n in store.recent(2)] == ["n2", "n3"]
    (tmp_path / "x" / "l.jsonl").open("a").write("garbage\n")
    assert len(store.recent(10)) == 4
