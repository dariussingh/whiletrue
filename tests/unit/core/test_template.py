import pytest

from whiletrue.core import TemplateError, render_deep, render_template

SCOPE = {
    "item": {"id": 7, "title": "Fix it", "tags": ["a", "b"]},
    "steps": {"my-step": {"out": "x"}},
}


def test_substitutes_dotted_paths() -> None:
    assert render_template("#{{item.id}}: {{ item.title }}", SCOPE) == "#7: Fix it"


def test_supports_dashed_keys_and_json_encodes_objects() -> None:
    assert render_template("{{steps.my-step.out}}", SCOPE) == "x"
    assert render_template("{{item.tags}}", SCOPE) == '["a","b"]'


def test_supports_list_indexes() -> None:
    assert render_template("{{item.tags.1}}", SCOPE) == "b"
    with pytest.raises(TemplateError):
        render_template("{{item.tags.5}}", SCOPE)


def test_leaves_text_without_tokens_alone() -> None:
    assert render_template("plain", SCOPE) == "plain"


def test_raises_on_missing_variables() -> None:
    with pytest.raises(TemplateError):
        render_template("{{item.nope}}", SCOPE)
    with pytest.raises(TemplateError, match="not defined"):
        render_template("{{nope.x}}", SCOPE)
    with pytest.raises(TemplateError, match="not defined"):
        render_template("{{item.id.deeper}}", SCOPE)


def test_rejects_expressions() -> None:
    with pytest.raises(TemplateError, match="Unsupported"):
        render_template("{{ item.id == 7 }}", SCOPE)


def test_render_deep_renders_nested_strings_and_keeps_other_values() -> None:
    value = {"a": "{{item.id}}", "b": [1, "{{item.title}}"], "c": {"d": True}, "e": None}
    assert render_deep(value, SCOPE) == {"a": "7", "b": [1, "Fix it"], "c": {"d": True}, "e": None}
