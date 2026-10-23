from whiletrue.cli.panel import Panel, Row, render, rows
from whiletrue.core import ItemRecord, MemoryStore
from whiletrue.tmux import ExecResult, TmuxController

NOW = 10_000_000


def seeded() -> MemoryStore:
    store = MemoryStore()
    store.create_run(id="r", pipeline_hash="h", started_at=NOW - 3_700_000)
    store.upsert_item(ItemRecord("7", "r", "7", "parked"))
    store.upsert_item(ItemRecord("8", "r", "8", "running"))
    store.upsert_item(ItemRecord("9", "r", "9", "ready"))
    a = store.start_step(
        run_id="r", item_id="7", node_path="root", attempt=1, input=None, started_at=NOW - 3_600_000
    )
    store.finish_step(a, status="completed", port="needs_human", cost_usd=0.5, ended_at=NOW)
    b = store.start_step(
        run_id="r",
        item_id="7",
        node_path="review",
        attempt=1,
        input=None,
        started_at=NOW - 3_000_000,
    )
    store.finish_step(b, status="completed", port="needs_human", cost_usd=1.25, ended_at=NOW)
    store.start_step(
        run_id="r", item_id="8", node_path="impl", attempt=1, input=None, started_at=NOW - 90_000
    )
    store.create_park(
        run_id="r", item_id="7", node_path="review", what="pr", wake={}, reason="w", created_at=1
    )
    return store


def test_rows_summarize_each_item() -> None:
    table = rows(seeded(), NOW)
    assert [(r.item, r.state, r.step, r.waiting) for r in table] == [
        ("7", "parked", "review", "pr at review"),
        ("8", "running", "impl", ""),
        ("9", "ready", "", ""),
    ]
    assert table[0].cost == 1.75 and table[0].age_s == 3600
    assert table[1].age_s == 90 and table[2].age_s == 3700
    assert rows(MemoryStore(), NOW) == []


def test_render_marks_the_selection_and_formats_cost_and_age() -> None:
    text = render(
        [
            Row("7", "parked", "review", "pr at review", 1.75, 3720),
            Row("8", "running", "impl", "", 0, 90),
        ],
        1,
        "hello",
    )
    lines = text.splitlines()
    assert lines[0].split()[:3] == ["ITEM", "STATE", "STEP"]
    assert lines[1].startswith("  7") and "pr at review" in lines[1] and "$1.75" in lines[1]
    assert "1h02m" in lines[1]
    assert lines[2].startswith("> 8") and "1m30s" in lines[2]
    assert lines[-1] == "hello" and "approve" in text
    assert "(no items yet)" in render([], 0)


class FakeTmux:
    def __init__(self, listing: str = "%5 @2 8 impl\n") -> None:
        self.calls: list[list[str]] = []
        self.listing = listing

    async def __call__(self, argv: list[str]) -> ExecResult:
        self.calls.append(argv)
        if argv[0] == "list-panes":
            return ExecResult(0, self.listing, "")
        return ExecResult(0, "", "")


def panel(
    store: MemoryStore, tmux: FakeTmux | None = None, answers: list[str] | None = None
) -> Panel:
    async def ask(prompt: str) -> str:
        return (answers or [""]).pop(0)

    controller = TmuxController("wt-x", exec=tmux) if tmux is not None else None
    return Panel(store, controller, now=lambda: NOW, ask=ask, who="tester")


async def test_navigation_is_clamped_and_quit_returns_false() -> None:
    p = panel(seeded())
    await p.key("k")
    assert p.selected == 0
    await p.key("j")
    await p.key("j")
    await p.key("j")
    assert p.selected == 2
    assert "> 9" in p.view()
    assert await p.key("q") is False
    assert await p.key("x") is True


async def test_approve_decides_the_selected_items_park() -> None:
    store = seeded()
    p = panel(store)
    await p.key("a")
    [park] = store.list_parks("7")
    assert (park.decision, park.decided_by) == ("approved", "tester")
    assert "7: approved" in p.view()
    await p.key("a")
    assert "not waiting" in p.message


async def test_reject_needs_a_note() -> None:
    store = seeded()
    p = panel(store, answers=["", "add tests"])
    await p.key("r")
    assert "needs a note" in p.message and store.list_parks("7")[0].decision is None
    await p.key("r")
    assert store.list_parks("7")[0].notes == "add tests"
    quiet = Panel(seeded(), None, now=lambda: NOW)
    await quiet.key("r")
    assert "needs a note" in quiet.message


async def test_focus_selects_the_items_pane() -> None:
    tmux = FakeTmux()
    p = panel(seeded(), tmux)
    await p.key("j")  # item 8
    await p.key("f")
    assert ["select-window", "-t", "@2"] in tmux.calls and ["select-pane", "-t", "%5"] in tmux.calls
    nothing = panel(seeded(), FakeTmux("%1 @1 other\n"))
    await nothing.key("f")
    assert "no live pane" in nothing.message
    detached = panel(seeded())
    await detached.key("f")
    assert "no tmux session" in detached.message


async def test_rerun_and_cancel_are_honest_about_not_existing_yet() -> None:
    p = panel(seeded())
    await p.key("R")
    assert "milestone 3" in p.message
    await p.key("c")
    assert "milestone 3" in p.message


async def test_an_empty_panel_ignores_actions() -> None:
    p = panel(MemoryStore())
    assert await p.key("a") is True and p.message == ""
    assert "(no items yet)" in p.view()


async def test_find_pane_handles_a_missing_session() -> None:
    class Gone(FakeTmux):
        async def __call__(self, argv: list[str]) -> ExecResult:
            return ExecResult(1, "", "no session")

    assert await TmuxController("s", exec=Gone()).find_pane("7") is None
