from typing import Any

import pytest
from pydantic import BaseModel

from whiletrue.core import CapError, CapRegistry, create_builtin_caps


def test_registers_and_retrieves_caps() -> None:
    caps = CapRegistry()
    caps.register("Num", float)
    assert caps.has("Num")
    assert caps.names() == ["Num"]


def test_rejects_duplicate_registration_and_invalid_names() -> None:
    caps = CapRegistry()
    caps.register("Num", float)
    with pytest.raises(CapError):
        caps.register("Num", float)
    with pytest.raises(CapError):
        caps.register("bad name", float)


def test_raises_on_unknown_cap_lookups() -> None:
    caps = CapRegistry()
    with pytest.raises(CapError, match="(?i)unknown cap"):
        caps.validate("Nope", 1)
    with pytest.raises(CapError, match="(?i)unknown cap"):
        caps.to_json_schema("Nope")


def test_validates_values_and_reports_issues() -> None:
    caps = CapRegistry()
    caps.register("Num", float)
    good = caps.validate("Num", 3)
    assert good.ok
    assert good.value == 3
    bad = caps.validate("Num", "x")
    assert not bad.ok
    assert "number" in bad.errors[0]


def test_prefixes_error_paths() -> None:
    class Inner(BaseModel):
        b: int

    class Outer(BaseModel):
        a: Inner

    caps = CapRegistry()
    caps.register("Obj", Outer)
    bad = caps.validate("Obj", {"a": {"b": "x"}})
    assert not bad.ok
    assert bad.errors[0].startswith("a.b:")


def test_accepts_a_prebuilt_type_adapter() -> None:
    from pydantic import TypeAdapter

    caps = CapRegistry()
    caps.register("Num", TypeAdapter(float))
    assert caps.validate("Num", 1.5).ok


def test_emits_json_schema() -> None:
    caps = CapRegistry()
    caps.register("Num", float)
    assert caps.to_json_schema("Num")["type"] == "number"


class TestCompatibility:
    caps = create_builtin_caps()

    def test_same_cap_is_compatible(self) -> None:
        assert self.caps.compatible("Issue", "Issue")

    def test_any_accepts_and_produces_anything(self) -> None:
        assert self.caps.compatible("Issue", "Any")
        assert self.caps.compatible("Any", "Diff")

    def test_different_concrete_caps_are_incompatible(self) -> None:
        assert not self.caps.compatible("Issue", "Diff")

    def test_unknown_caps_are_incompatible(self) -> None:
        assert not self.caps.compatible("Nope", "Issue")


class TestBuiltinCaps:
    caps = create_builtin_caps()

    def test_defines_the_documented_set(self) -> None:
        assert sorted(self.caps.names()) == sorted(
            ["Any", "Diff", "Issue", "PullRequest", "ReviewFindings", "Text", "Verdict"]
        )

    def test_accepts_representative_values(self) -> None:
        samples: list[tuple[str, Any]] = [
            ("Text", "hi"),
            ("Any", {"x": 1}),
            ("Verdict", {"pass": True}),
            ("Issue", {"id": "1", "title": "t", "body": "b"}),
            ("Issue", {"id": 1, "title": "t", "body": "b", "depends_on": [2, "x"]}),
            ("Diff", {"files": [{"path": "a", "additions": 1, "deletions": 0}]}),
            ("PullRequest", {"number": 1, "url": "u", "branch": "b"}),
            ("ReviewFindings", {"findings": [{"severity": "high", "message": "m"}]}),
        ]
        for name, value in samples:
            assert self.caps.validate(name, value).ok, name

    def test_rejects_malformed_values(self) -> None:
        assert not self.caps.validate("Verdict", {"pass": "yes"}).ok
        assert not self.caps.validate("Issue", {"title": "t"}).ok
        assert not self.caps.validate("Text", 5).ok
        assert not self.caps.validate("ReviewFindings", {"findings": [{"severity": "meh"}]}).ok

    def test_verdict_round_trips_through_its_alias(self) -> None:
        result = self.caps.validate("Verdict", {"pass": True, "reason": "ok"})
        assert result.value == {"pass": True, "reason": "ok"}
        assert self.caps.to_json_schema("Verdict")["properties"]["pass"]["type"] == "boolean"
