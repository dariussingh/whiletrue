"""Configuration for the `rsi.harness2` strategy and the top-level `rsi:` setting."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, ValidationError

from whiletrue.rsi.harness2.ledger import Mode
from whiletrue.rsi.harness2.prompts import Profile
from whiletrue.rsi.harness2.snapshot import DEFAULT_EXCLUDE

#: What `rsi: true` spends per item when the config does not say. It is a hard cap, and `validate`
#: warns that it is a default.
DEFAULT_BUDGET_USD = 10.0
IMPROVER_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"]


class SessionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Where the runner can enforce a spend cap. The strategy also caps it by what is left.
    budget_usd: PositiveFloat | None = None
    turns: PositiveInt | None = 40
    wall: str | int | float | None = "20m"


class SessionSpec(BaseModel):
    """How one improvement session (propose, compose, select, reflect) is run."""

    model_config = ConfigDict(extra="forbid")

    runner: str = "claude-code-headless"
    model: str | None = None
    permission_mode: str = "acceptEdits"
    tools: list[str] = Field(default_factory=lambda: list(IMPROVER_TOOLS))
    limits: SessionLimits = Field(default_factory=SessionLimits)


class ProfileConfig(BaseModel):
    """Overrides for the prompt profile; unset fields keep the software-work defaults."""

    model_config = ConfigDict(extra="forbid")

    work: str | None = None
    domain_guidance: str | None = None
    deliverable_word: str | None = None
    domain_word: str | None = None
    inv_levers: str | None = None
    dom_levers: str | None = None
    yardstick: str | None = None

    def build(self) -> Profile:
        base = Profile()
        given = {k: v for k, v in self.model_dump().items() if v is not None}
        return Profile(**{**base.__dict__, **given})


class Harness2Config(BaseModel):
    """The `rsi.harness2` strategy: improve the harness around the steps it wraps."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["harness2"] = "harness2"
    #: Task-level recursion budget. One step costs R = 4k rollouts and P = 3k sessions.
    k: PositiveInt = 1
    mode: Mode = "parallel"
    #: The area items belong to; later items read earlier items' adopted harnesses. Empty: the
    #: project directory name.
    domain: str = ""
    #: What happens to the chosen harness: ask a person, apply it, or only record it.
    adopt: Literal["approve", "auto", "never"] = "approve"
    #: Hard cap on what one item may spend on rsi (rollouts and sessions together).
    budget_usd: PositiveFloat = DEFAULT_BUDGET_USD
    #: Which framework's files are the harness.
    surface: Literal["claude-code", "codex"] = "claude-code"
    improver: SessionSpec = Field(default_factory=SessionSpec)
    selector: SessionSpec | None = None
    reflector: SessionSpec | None = None
    #: Rollouts run at once inside one item.
    concurrency: PositiveInt = 2
    #: Where the experience library lives, relative to the project.
    library: str = ".whiletrue/rsi/library"
    #: Names left out of the workspace snapshots, and whether `.git` is copied.
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    copy_git: bool = False
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    #: A CEL expression over the item and iteration; when false the steps run once, plainly.
    when: str | None = None
    #: Set by `rsi: true`, which does not name a budget; `validate` says so.
    budget_is_default: bool = False

    def selector_spec(self) -> SessionSpec:
        return self.selector or self.improver

    def reflector_spec(self) -> SessionSpec:
        return self.reflector or self.improver


class RsiSetting(BaseModel):
    """The top-level `rsi:` key: `true`, `false`, or the strategy's settings plus a scope."""

    model_config = ConfigDict(extra="forbid")

    harness2: Harness2Config
    #: Ids of the top-level steps to wrap. Empty: the leading steps that contain an agent and no
    #: side effects.
    scope: list[str] = Field(default_factory=lambda: [])
    #: True when the user wrote `rsi: true`, so the budget is a default.
    default_budget: bool = False


class SettingError(ValueError):
    """The `rsi:` key is not valid."""


def parse_setting(raw: Any) -> RsiSetting | None:
    """Read the top-level `rsi:` value. None means off (absent or `false`)."""
    if raw is None or raw is False:
        return None
    if raw is True:
        return RsiSetting(harness2=Harness2Config(budget_is_default=True), default_budget=True)
    if not isinstance(raw, dict):
        raise SettingError("`rsi` must be true, false, or a mapping of settings")
    values: dict[str, Any] = {str(k): v for k, v in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    scope: Any = values.pop("scope", [])
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in cast("list[Any]", scope)):
        raise SettingError("`rsi.scope` must be a list of step ids")
    try:
        config = Harness2Config.model_validate(values)
    except ValidationError as error:
        issue = error.errors()[0]
        where = ".".join(str(p) for p in issue["loc"])
        raise SettingError(f"`rsi.{where}`: {issue['msg']}") from error
    config.budget_is_default = "budget_usd" not in values
    return RsiSetting(
        harness2=config,
        scope=[str(s) for s in cast("list[Any]", scope)],
        default_budget="budget_usd" not in values,
    )
