from pathlib import Path

import pytest

from whiletrue.basics import MarkdownTasksSource
from whiletrue.core import SourceError

TASKS = """# Plan

- [x] Already done
- [ ] Set up the schema {#schema}
* [ ] Add the API {#api after=schema}
- [ ] Write the login page {#login after=schema,api}
- [ ] Polish the README
not a task
  - [ ] Nested task
"""


async def test_unchecked_tasks_become_items_with_ids_and_dependencies(tmp_path: Path) -> None:
    (tmp_path / "TASKS.md").write_text(TASKS)
    items = await MarkdownTasksSource().fetch({"path": "TASKS.md"}, str(tmp_path))
    assert [(i.id, i.depends_on) for i in items] == [
        ("schema", []),
        ("api", ["schema"]),
        ("login", ["schema", "api"]),
        ("polish-the-readme", []),
        ("nested-task", []),
    ]
    assert items[0].payload == {"title": "Set up the schema", "line": 4}


async def test_ticking_a_box_removes_the_item(tmp_path: Path) -> None:
    path = tmp_path / "TASKS.md"
    path.write_text("- [ ] A {#a}\n- [ ] B {#b}\n")
    source = MarkdownTasksSource()
    assert [i.id for i in await source.fetch({"path": "TASKS.md"}, str(tmp_path))] == ["a", "b"]
    path.write_text("- [x] A {#a}\n- [ ] B {#b}\n")
    assert [i.id for i in await source.fetch({"path": "TASKS.md"}, str(tmp_path))] == ["b"]


@pytest.mark.parametrize(
    ("config", "content", "message"),
    [
        ({}, None, "needs a `path`"),
        ({"path": "T.md"}, None, "Cannot read"),
        ({"path": "T.md"}, "- [ ] Same\n- [ ] Same\n", "duplicate task id"),
    ],
)
async def test_bad_files_are_reported(
    tmp_path: Path, config: dict[str, str], content: str | None, message: str
) -> None:
    if content is not None:
        (tmp_path / "T.md").write_text(content)
    with pytest.raises(SourceError, match=message):
        await MarkdownTasksSource().fetch(config, str(tmp_path))


async def test_a_title_made_only_of_symbols_still_gets_an_id(tmp_path: Path) -> None:
    (tmp_path / "T.md").write_text("- [ ] ???\n")
    [item] = await MarkdownTasksSource().fetch({"path": "T.md"}, str(tmp_path))
    assert item.id == "task"
