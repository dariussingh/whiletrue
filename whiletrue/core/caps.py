from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class CapError(Exception):
    """Caps are named schemas attached to ports (like GStreamer caps)."""


@dataclass
class CapValidation:
    ok: bool
    value: Any = None
    errors: list[str] = field(default_factory=lambda: [])


_CAP_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def format_validation_error(error: ValidationError) -> list[str]:
    """`path: message` per issue, in the order pydantic reports them."""
    out: list[str] = []
    for issue in error.errors():
        path = ".".join(str(p) for p in issue["loc"])
        out.append(f"{path}: {issue['msg']}" if path else issue["msg"])
    return out


class CapRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, TypeAdapter[Any]] = {}

    def register(self, name: str, schema: Any) -> None:
        """`schema` is anything pydantic can validate: a model class, `str`, `Any`, ..."""
        if not _CAP_NAME.match(name):
            raise CapError(f'Invalid cap name "{name}" (use PascalCase)')
        if name in self._caps:
            raise CapError(f'Cap "{name}" is already registered')
        self._caps[name] = schema if isinstance(schema, TypeAdapter) else TypeAdapter(schema)  # pyright: ignore[reportUnknownVariableType]

    def has(self, name: str) -> bool:
        return name in self._caps

    def names(self) -> list[str]:
        return list(self._caps)

    def _get(self, name: str) -> TypeAdapter[Any]:
        adapter = self._caps.get(name)
        if adapter is None:
            raise CapError(f'Unknown cap "{name}"')
        return adapter

    def validate(self, name: str, value: Any) -> CapValidation:
        adapter = self._get(name)
        try:
            parsed = adapter.validate_python(value)
        except ValidationError as error:
            return CapValidation(ok=False, errors=format_validation_error(error))
        return CapValidation(ok=True, value=adapter.dump_python(parsed, mode="json", by_alias=True))

    def to_json_schema(self, name: str) -> dict[str, Any]:
        return self._get(name).json_schema()

    def compatible(self, from_cap: str, to_cap: str) -> bool:
        """Can a value produced under `from_cap` be consumed under `to_cap`?

        Nominal: identical caps, or either side is `Any`. Unknown caps are never compatible.
        """
        if not self.has(from_cap) or not self.has(to_cap):
            return False
        return "Any" in (from_cap, to_cap) or from_cap == to_cap


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, populate_by_name=True)


class Issue(_Strict):
    id: str | int
    number: int | None = None
    title: str
    body: str
    labels: list[str] | None = None
    url: str | None = None
    spec_refs: list[str] | None = None
    depends_on: list[str | int] | None = None


class DiffFile(_Strict):
    path: str
    additions: int
    deletions: int


class Diff(_Strict):
    files: list[DiffFile]
    patch: str | None = None


class PullRequest(_Strict):
    number: int
    url: str
    branch: str
    base: str | None = None
    title: str | None = None
    repo: str | None = None


class Verdict(_Strict):
    pass_: bool = Field(alias="pass")
    reason: str | None = None


class Finding(_Strict):
    file: str | None = None
    line: int | None = None
    kind: str | None = None
    severity: Literal["low", "medium", "high", "critical"]
    message: str


class ReviewFindings(_Strict):
    findings: list[Finding]


def create_builtin_caps() -> CapRegistry:
    caps = CapRegistry()
    caps.register("Any", Any)
    caps.register("Text", str)
    caps.register("Issue", Issue)
    caps.register("Diff", Diff)
    caps.register("PullRequest", PullRequest)
    caps.register("Verdict", Verdict)
    caps.register("ReviewFindings", ReviewFindings)
    return caps
