from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, ValidationError

from whiletrue.core.duration import parse_duration
from whiletrue.core.outcomes import OnFail
from whiletrue.core.runner import AgentLimits


@dataclass(frozen=True)
class SourceLoc:
    file: str
    line: int
    col: int


class ConfigError(Exception):
    def __init__(self, loc: SourceLoc, message: str) -> None:
        super().__init__(f"{loc.file}:{loc.line}:{loc.col}: {message}")
        self.file = loc.file
        self.line = loc.line
        self.col = loc.col


@dataclass
class PipelineNode:
    id: str
    #: Module or strategy id, e.g. "shell" or "loop".
    use: str
    config: dict[str, Any]
    children: list[PipelineNode]
    #: Outcome/data port -> sibling node id.
    wires: dict[str, str]
    loc: SourceLoc


@dataclass
class PipelineConfig:
    file: str
    name: str
    uses: list[str]
    on_idle: Literal["exit", "watch"]
    on_fail: OnFail
    #: Passed through for providers (source, comments, ...).
    extra: dict[str, Any]
    root: PipelineNode


Path_ = list[str | int]


class _Loader(yaml.SafeLoader):
    """YAML 1.2-ish scalars: only true/false are booleans and timestamps stay strings."""


_Loader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(  # pyright: ignore[reportUnknownMemberType]
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class _Located:
    """A parsed YAML document that can answer "where is this path?" for error messages."""

    def __init__(self, text: str, file: str, line_offset: int = 0) -> None:
        self.file = file
        self.line_offset = line_offset
        loader = _Loader(text)
        try:
            self.root: yaml.Node | None = loader.get_single_node()
            self.value: Any = (
                loader.construct_document(self.root)  # pyright: ignore[reportUnknownMemberType]
                if self.root
                else None
            )
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            problem = getattr(error, "problem", None) or str(error)
            line = (mark.line if mark else 0) + 1 + line_offset
            col = (mark.column if mark else 0) + 1
            raise ConfigError(SourceLoc(file, line, col), str(problem).split("\n")[0]) from error
        finally:
            loader.dispose()

    def _node_at(self, path: Path_) -> yaml.Node | None:
        node = self.root
        for key in path:
            if isinstance(node, yaml.MappingNode):
                node = next(
                    (v for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == key),
                    None,
                )
            elif isinstance(node, yaml.SequenceNode) and isinstance(key, int):
                node = node.value[key] if 0 <= key < len(node.value) else None
            else:
                node = None
            if node is None:
                return None
        return node

    def loc(self, path: Path_) -> SourceLoc:
        # Fall back to the nearest existing ancestor.
        for end in range(len(path), 0, -1):
            node = self._node_at(path[:end])
            if node is not None:
                return self._loc_of(node)
        return SourceLoc(self.file, 1 + self.line_offset, 1)

    def _loc_of(self, node: yaml.Node) -> SourceLoc:
        mark = node.start_mark
        return SourceLoc(self.file, mark.line + 1 + self.line_offset, mark.column + 1)

    def fail(self, path: Path_, message: str) -> NoReturn:
        raise ConfigError(self.loc(path), message)

    def validate[T: BaseModel](self, model: type[T], value: Any, base: Path_ | None = None) -> T:
        try:
            return model.model_validate(value)
        except ValidationError as error:
            issue = error.errors()[0]
            loc = list(issue["loc"])
            where = f' at "{".".join(str(p) for p in loc)}"' if loc else ""
            self.fail([*(base or []), *loc], f"{issue['msg']}{where}")


_RESERVED = {"id", "wires", "do"}


class _TopLevel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, min_length=1)
    uses: list[str] = Field(default_factory=lambda: [])
    on_idle: Literal["exit", "watch"] = "exit"
    on_fail: Literal["park", "drop", "propagate"] = "park"


def _build_node(
    located: _Located, value: Any, path: Path_, default_id: str, seen: dict[str, SourceLoc]
) -> PipelineNode:
    if not isinstance(value, dict):
        located.fail(path, "A pipeline step must be a mapping")
    step: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
    keys = [k for k in step if k not in _RESERVED]
    if len(keys) != 1:
        located.fail(
            path,
            "A step needs a module key (e.g. `shell: {...}`)"
            if not keys
            else f"A step must have exactly one module key, found: {', '.join(map(str, keys))}",
        )
    use = str(keys[0])
    raw_config = step[keys[0]]
    if raw_config is not None and not isinstance(raw_config, dict):
        located.fail([*path, use], f'Config for "{use}" must be a mapping')
    config: dict[str, Any] = dict(raw_config) if raw_config else {}  # pyright: ignore[reportUnknownArgumentType]

    # `do` may sit beside the module key or inside its config (strategies); hoist the latter.
    do_path: Path_ = [*path, "do"]
    raw_do = step.get("do")
    if raw_do is None and "do" in config:
        raw_do = config.pop("do")
        do_path = [*path, use, "do"]
    if raw_do is not None and not isinstance(raw_do, list):
        located.fail(do_path, "`do` must be a list of steps")

    explicit_id = step.get("id")
    if "id" in step and (not isinstance(explicit_id, str) or explicit_id == ""):
        located.fail([*path, "id"], "`id` must be a non-empty string")
    node_id: str = explicit_id if isinstance(explicit_id, str) else default_id
    loc = located.loc(path)
    if node_id in seen:
        located.fail(
            path, f'Duplicate step id "{node_id}" (first defined at line {seen[node_id].line})'
        )
    seen[node_id] = loc

    raw_wires = step.get("wires")
    if raw_wires is not None and not (
        isinstance(raw_wires, dict) and all(isinstance(t, str) for t in raw_wires.values())  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    ):
        located.fail([*path, "wires"], "`wires` must map port names to step ids")

    children = [
        _build_node(located, child, [*do_path, i], f"{node_id}.{i}", seen)
        for i, child in enumerate(raw_do or [])  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    ]
    return PipelineNode(
        id=node_id,
        use=use,
        config=config,
        children=children,
        wires=dict(raw_wires) if raw_wires else {},  # pyright: ignore[reportUnknownArgumentType]
        loc=loc,
    )


def _check_wires(node: PipelineNode) -> None:
    """Wire targets must be siblings."""
    siblings = {c.id for c in node.children}
    for child in node.children:
        for port, target in child.wires.items():
            if target not in siblings:
                raise ConfigError(
                    child.loc, f'Wire "{port}" targets unknown sibling step "{target}"'
                )
        _check_wires(child)


def parse_pipeline(text: str, file: str = "whiletrue.yaml") -> PipelineConfig:
    located = _Located(text, file)
    raw = located.value
    if not isinstance(raw, dict):
        located.fail([], "whiletrue.yaml must be a mapping")
    top = located.validate(_TopLevel, raw)
    if "pipeline" not in raw:
        located.fail([], "Missing required key `pipeline`")

    extra: dict[str, Any] = {k: v for k, v in (top.model_extra or {}).items() if k != "pipeline"}
    pipeline: Any = cast(dict[str, Any], raw)["pipeline"]
    seen: dict[str, SourceLoc] = {}
    if isinstance(pipeline, list):
        seen["root"] = located.loc(["pipeline"])
        children = [
            _build_node(located, child, ["pipeline", i], f"root.{i}", seen)
            for i, child in enumerate(pipeline)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        ]
        root = PipelineNode(
            id="root",
            use="sequence",
            config={},
            children=children,
            wires={},
            loc=located.loc(["pipeline"]),
        )
    else:
        root = _build_node(located, pipeline, ["pipeline"], "root", seen)
    _check_wires(root)

    return PipelineConfig(
        file=file,
        name=top.name or Path(file).resolve().parent.name,
        uses=top.uses,
        on_idle=top.on_idle,
        on_fail=top.on_fail,
        extra=extra,
        root=root,
    )


def load_pipeline(path: str | Path) -> PipelineConfig:
    return parse_pipeline(Path(path).read_text(encoding="utf-8"), str(path))


# ---- Agent modules (markdown with frontmatter) -------------------------------------------


class _LimitsFrontmatter(BaseModel):
    budget_usd: PositiveFloat | None = None
    turns: PositiveInt | None = None
    wall: str | int | float | None = None


class _AgentFrontmatter(BaseModel):
    agent: str = Field(min_length=1)
    runner: str = "claude-code-headless"
    model: str | None = None
    session: str = "fresh"
    tools: list[str] | None = None
    permission_mode: str | None = None
    inputs: dict[str, str] = Field(default_factory=lambda: {})
    outputs: dict[str, str] = Field(default_factory=lambda: {})
    context: dict[str, Any] = Field(default_factory=lambda: {})
    limits: _LimitsFrontmatter | None = None


@dataclass
class AgentDefinition:
    file: str
    name: str
    prompt: str
    runner: str = "claude-code-headless"
    model: str | None = None
    session: str = "fresh"
    tools: list[str] | None = None
    permission_mode: str | None = None
    inputs: dict[str, str] = field(default_factory=lambda: {})
    outputs: dict[str, str] = field(default_factory=lambda: {})
    context: dict[str, Any] = field(default_factory=lambda: {})
    limits: AgentLimits | None = None


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)


def parse_agent_definition(text: str, file: str) -> AgentDefinition:
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ConfigError(
            SourceLoc(file, 1, 1),
            "Agent file must start with a YAML frontmatter block delimited by ---",
        )
    located = _Located(match.group(1), file, line_offset=1)
    frontmatter: Any = located.value
    if not isinstance(frontmatter, dict):
        located.fail([], "Agent frontmatter must be a mapping")
    fm = located.validate(_AgentFrontmatter, cast(dict[str, Any], frontmatter))

    wall_ms: int | None = None
    if fm.limits is not None and fm.limits.wall is not None:
        try:
            wall_ms = parse_duration(fm.limits.wall)
        except ValueError as error:
            located.fail(["limits", "wall"], str(error))
    limits = (
        AgentLimits(budget_usd=fm.limits.budget_usd, turns=fm.limits.turns, wall_ms=wall_ms)
        if fm.limits is not None
        else None
    )
    return AgentDefinition(
        file=file,
        name=fm.agent,
        runner=fm.runner,
        model=fm.model,
        session=fm.session,
        tools=fm.tools,
        permission_mode=fm.permission_mode,
        inputs=fm.inputs,
        outputs=fm.outputs,
        context=fm.context,
        limits=limits,
        prompt=match.group(2).strip(),
    )


def load_agent_definition(path: str | Path) -> AgentDefinition:
    return parse_agent_definition(Path(path).read_text(encoding="utf-8"), str(path))
