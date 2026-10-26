"""Static checks for `rsi.harness2`: what `validate` can say before any money is spent."""

from __future__ import annotations

from pathlib import Path

from whiletrue.core import (
    ConfigIssue,
    PipelineNode,
    ValidateContext,
    load_agent_definition,
    parse_expr,
)
from whiletrue.rsi.harness2.config import Harness2Config
from whiletrue.rsi.harness2.surfaces import SURFACE_FOR_RUNNER

#: Variables a `when:` expression may use.
WHEN_VARIABLES = frozenset({"item", "iter", "run", "input", "project_dir"})


def walk(node: PipelineNode) -> list[PipelineNode]:
    return [node, *(n for child in node.children for n in walk(child))]


def surface_for(node: PipelineNode, cwd: str) -> tuple[str | None, str | None]:
    """(runner id, problem) for an agent step."""
    uses = node.config.get("uses")
    runner: object = node.config.get("runner")
    if not isinstance(runner, str):
        try:
            runner = load_agent_definition(Path(cwd) / str(uses)).runner
        except Exception as error:  # a bad agent file is reported by the agent step itself
            return None, f"cannot read agent file {uses}: {error}"
    return str(runner), None


def check_children(
    cfg: Harness2Config, node: PipelineNode, vctx: ValidateContext
) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    steps = [n for child in node.children for n in walk(child)]
    agents = [n for n in steps if n.use == "agent"]
    if not agents:
        issues.append(
            ConfigIssue("error", "`rsi.harness2` wraps no agent step: nothing to improve")
        )
    for step in steps:
        resolved = vctx.registry.resolve(step.use)
        if resolved is None:
            continue
        if resolved.kind == "action" and resolved.module.effects == "side-effect":
            issues.append(
                ConfigIssue(
                    "error",
                    f"`{step.use}` has side effects and cannot run inside `rsi.harness2`: it "
                    "would repeat in every rollout. Move it after the rsi step.",
                )
            )
        if step.use.startswith("human."):
            issues.append(
                ConfigIssue(
                    "error",
                    f"`{step.use}` cannot run inside `rsi.harness2` (a person "
                    "would be asked in every rollout)",
                )
            )
    for step in agents:
        runner, problem = surface_for(step, vctx.cwd)
        if problem:
            continue
        mapped = SURFACE_FOR_RUNNER.get(runner or "")
        if mapped is None:
            known = ", ".join(sorted(SURFACE_FOR_RUNNER))
            issues.append(
                ConfigIssue(
                    "error",
                    f'Cannot improve the harness of runner "{runner}" yet (supported: {known})',
                )
            )
        elif mapped != cfg.surface:
            issues.append(
                ConfigIssue(
                    "warning",
                    f'Agent step "{step.id}" runs on "{runner}" but `rsi.surface` is '
                    f'"{cfg.surface}": the edits will not reach it',
                )
            )
    return issues


def validate(cfg: Harness2Config, vctx: ValidateContext) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    for name, spec in (
        ("improver", cfg.improver),
        ("selector", cfg.selector_spec()),
        ("reflector", cfg.reflector_spec()),
        ("reviewer", cfg.reviewer_spec()),
    ):
        if vctx.registry.get_runner(spec.runner) is None:
            issues.append(ConfigIssue("error", f'`rsi.{name}`: unknown runner "{spec.runner}"'))
    if cfg.when is not None:
        try:
            problems = parse_expr(cfg.when).check(WHEN_VARIABLES)
        except ValueError as error:
            problems = [str(error)]
        issues += [ConfigIssue("error", f"`rsi.when`: {p}") for p in problems]
    if cfg.budget_is_default:
        issues.append(
            ConfigIssue(
                "warning",
                f"`rsi` sets no budget, so each item may spend up to ${cfg.budget_usd:.2f}; "
                "set `rsi.budget_usd` to choose",
            )
        )
    rollouts, sessions = 4 * cfg.k, 3 * cfg.k
    issues.append(
        ConfigIssue(
            "warning",
            f"rsi runs {rollouts} rollouts and {sessions} improvement sessions per item at "
            f"k={cfg.k} (about {rollouts + sessions}x one agent run); spend is capped at "
            f"${cfg.budget_usd:.2f} per item",
        )
    )
    return issues


def validate_node(
    cfg: Harness2Config, node: PipelineNode, vctx: ValidateContext
) -> list[ConfigIssue]:
    return check_children(cfg, node, vctx)
