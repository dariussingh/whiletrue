import json
from pathlib import Path

import pytest

from whiletrue.basics import JsonSource, basics_plugin
from whiletrue.core import Registry, SourceError, fetch_items, source_spec


async def test_reads_items_with_fields_and_dependencies(tmp_path: Path) -> None:
    (tmp_path / "items.json").write_text(
        json.dumps(
            [
                {"id": 1, "title": "A"},
                {"id": "b", "title": "B", "depends_on": [1]},
            ]
        )
    )
    items = await JsonSource().fetch({"path": "items.json"}, str(tmp_path))
    assert [(i.id, i.payload, i.depends_on) for i in items] == [
        ("1", {"title": "A"}, []),
        ("b", {"title": "B"}, ["1"]),
    ]


@pytest.mark.parametrize(
    ("config", "content", "message"),
    [
        ({}, None, "needs a `path`"),
        ({"path": "x.json"}, None, "Cannot read"),
        ({"path": "x.json"}, "not json", "Cannot read"),
        ({"path": "x.json"}, '{"a": 1}', "JSON list"),
        ({"path": "x.json"}, '[{"title": "no id"}]', "needs an `id`"),
        ({"path": "x.json"}, "[3]", "needs an `id`"),
    ],
)
async def test_reports_bad_sources(
    tmp_path: Path, config: dict[str, str], content: str | None, message: str
) -> None:
    if content is not None:
        (tmp_path / "x.json").write_text(content)
    with pytest.raises(SourceError, match=message):
        await JsonSource().fetch(config, str(tmp_path))


async def test_fetch_items_resolves_the_provider(tmp_path: Path) -> None:
    (tmp_path / "i.json").write_text('[{"id": "x"}]')
    registry = Registry()
    registry.use(basics_plugin)
    extra = {"source": {"json": {"path": "i.json"}}}
    assert source_spec(extra) == ("json", {"path": "i.json"})
    items = await fetch_items(registry, extra, str(tmp_path))
    assert items is not None and items[0].id == "x"
    assert await fetch_items(registry, {}, str(tmp_path)) is None
    with pytest.raises(SourceError, match="Unknown source"):
        await fetch_items(registry, {"source": {"nope": {}}}, str(tmp_path))
    with pytest.raises(SourceError, match="must be a mapping"):
        source_spec({"source": {"json": 3}})
