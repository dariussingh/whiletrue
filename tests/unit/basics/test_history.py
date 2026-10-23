from whiletrue.basics import (
    DistillConfig,
    HistoryConfig,
    context_history_module,
    history_distill_module,
    render_history,
)
from whiletrue.core import MemoryStore, create_step_context


def seeded() -> MemoryStore:
    store = MemoryStore()
    store.create_run(id="r", pipeline_hash="h", started_at=1)
    park = store.create_park(
        run_id="r", item_id="7", node_path="review", what="pr", wake={}, reason="w", created_at=1
    )
    store.decide_park(park.id, "rejected", decided_by="me", decided_at=2, notes="add tests")
    store.create_park(
        run_id="r", item_id="7", node_path="review", what="pr", wake={}, reason="w", created_at=3
    )  # undecided: not history yet
    comment = store.add_comment(
        item_id="7", source="github_pr", external_id="c", author="alice", body="why?", created_at=4
    )
    assert comment is not None
    store.answer_comment(comment.id, "[whiletrue] because")
    store.add_comment(
        item_id="7", source="local", external_id="d", author="bob", body="also", created_at=5
    )
    store.add_steer(run_id="r", item_id="7", step="impl", prompt="use sqlite", created_at=6)
    store.add_learning(scope="project", note="use uv", status="approved", created_at=7)
    store.add_learning(scope="project", note="pending idea", status="proposed", created_at=8)
    return store


def test_item_history_lists_decisions_comments_replies_and_steers() -> None:
    text = render_history(seeded(), "7", "item")
    assert "a person rejected: add tests" in text
    assert "alice commented: why?" in text and "answered: [whiletrue] because" in text
    assert "bob commented: also" in text
    assert "steered impl: use sqlite" in text
    assert "use uv" not in text


def test_project_history_has_only_approved_lessons() -> None:
    text = render_history(seeded(), "7", "project")
    assert "use uv" in text and "pending idea" not in text and "rejected" not in text
    assert "use uv" in render_history(seeded(), "7", "all") and "alice" in render_history(
        seeded(), "7", "all"
    )


def test_an_item_without_history_renders_nothing() -> None:
    assert render_history(MemoryStore(), "x", "all") == ""


async def test_the_context_module_emits_the_text() -> None:
    ctx = create_step_context(HistoryConfig(scope="item"), store=seeded(), item_id="7")
    result = await context_history_module.run({"in": "ignored"}, ctx)
    assert result.port == "ok" and "add tests" in result.value
    bare = create_step_context(HistoryConfig())
    assert (await context_history_module.run({}, bare)).port == "fail"


async def test_distill_proposes_lessons_with_provenance_once() -> None:
    store = seeded()
    ctx = create_step_context(DistillConfig(), store=store, item_id="7", run_id="r", now=lambda: 50)
    result = await history_distill_module.run({"in": {"pr": 1}}, ctx)
    assert result.value == {"pr": 1}  # the input passes through
    proposed = store.list_learnings(status="proposed")
    notes = [n.note for n in proposed]
    assert "A reviewer rejected work with: add tests" in notes
    assert "A person steered the agent: use sqlite" in notes
    rejection = next(n for n in proposed if "rejected" in n.note)
    assert rejection.provenance["item"] == "7" and rejection.provenance["source"] == "rejection"
    again = await history_distill_module.run({}, ctx)
    assert again.value == {"proposed": []}  # nothing new the second time
    bare = create_step_context(DistillConfig())
    assert (await history_distill_module.run({}, bare)).port == "fail"
