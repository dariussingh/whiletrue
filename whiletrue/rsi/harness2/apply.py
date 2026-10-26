"""The top-level `rsi:` setting: wrap a pipeline's steps in the `rsi.harness2` strategy.

`rsi: false` (or no key) changes nothing. `rsi: true` or a mapping rewrites the pipeline at load
time, so `plan` and `validate` see the real steps: the chosen steps are wrapped in `rsi.harness2`
and, depending on `adopt`, followed by a review gate, an adoption step and a discard branch.
"""

from __future__ import annotations

from dataclasses import replace

from whiletrue.core import PipelineConfig, PipelineNode, Registry
from whiletrue.rsi.harness2.config import RsiSetting, SettingError, parse_setting

IMPROVE_ID = "rsi"
REVIEW_ID = "rsi_review"
ADOPT_ID = "rsi_adopt"
DISCARD_ID = "rsi_discard"
_RESERVED = (IMPROVE_ID, REVIEW_ID, ADOPT_ID, DISCARD_ID)


def _walk(node: PipelineNode) -> list[PipelineNode]:
    return [node, *(n for child in node.children for n in _walk(child))]


def _unsafe(node: PipelineNode, registry: Registry) -> str | None:
    """Why this subtree cannot run inside rollouts, or None."""
    for step in _walk(node):
        resolved = registry.resolve(step.use)
        if resolved is None:
            continue
        if resolved.kind == "action" and resolved.module.effects == "side-effect":
            return f"`{step.use}` has side effects"
        if step.use.startswith("human."):
            return f"`{step.use}` asks a person"
    return None


def _has_agent(node: PipelineNode) -> bool:
    return any(step.use == "agent" for step in _walk(node))


def _auto_scope(children: list[PipelineNode], registry: Registry) -> tuple[int, int]:
    """The run of safe steps around the first agent, ending at the last one that holds an agent.

    Steps with side effects before it (a checkout, say) stay outside; so do gates after it.
    """
    first = next((i for i, c in enumerate(children) if _has_agent(c)), None)
    if first is None:
        raise SettingError("`rsi` found no agent step to improve; name the steps with `rsi.scope`")
    reason = _unsafe(children[first], registry)
    if reason is not None:
        raise SettingError(
            f"`rsi` cannot wrap step {children[first].id}: {reason}; name other steps with "
            "`rsi.scope`"
        )
    start = first
    while start > 0 and _unsafe(children[start - 1], registry) is None:
        start -= 1
    end = first + 1
    for i in range(first + 1, len(children)):
        if _unsafe(children[i], registry) is not None:
            break
        if _has_agent(children[i]):
            end = i + 1
    return start, end


def _named_scope(
    children: list[PipelineNode], ids: list[str], registry: Registry
) -> tuple[int, int]:
    index = {child.id: i for i, child in enumerate(children)}
    missing = [name for name in ids if name not in index]
    if missing:
        raise SettingError(f"`rsi.scope` names unknown top-level step(s): {', '.join(missing)}")
    positions = sorted(index[name] for name in ids)
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise SettingError("`rsi.scope` steps must be consecutive")
    chosen = children[positions[0] : positions[-1] + 1]
    for child in chosen:
        reason = _unsafe(child, registry)
        if reason is not None:
            raise SettingError(f"`rsi.scope` includes step {child.id}: {reason}")
    if not any(_has_agent(child) for child in chosen):
        raise SettingError("`rsi.scope` contains no agent step")
    return positions[0], positions[-1] + 1


def _check_wires(children: list[PipelineNode], start: int, end: int) -> None:
    inside = {c.id for c in children[start:end]}
    for i, child in enumerate(children):
        here = start <= i < end
        for port, target in child.wires.items():
            if (target in inside) != here:
                raise SettingError(
                    f'The wire "{port}" of step {child.id} crosses the `rsi` scope boundary; '
                    "widen or narrow `rsi.scope`"
                )


def _adoption_nodes(setting: RsiSetting, template: PipelineNode) -> list[PipelineNode]:
    loc = template.loc
    adopt = setting.harness2.adopt
    if adopt == "never":
        return []
    adopt_node = PipelineNode(ADOPT_ID, "rsi.adopt", {}, [], {}, loc)
    if adopt in ("auto", "model"):
        return [adopt_node]
    gate = PipelineNode(
        f"{REVIEW_ID}_gate",
        "human.approve",
        {
            "what": "harness",
            "show": "Review the proposed harness change: whiletrue rsi diff {{item.id}}",
        },
        [],
        {},
        loc,
    )
    review = PipelineNode(REVIEW_ID, "rsi.review", {}, [gate], {"rejected": DISCARD_ID}, loc)
    return [review, adopt_node, PipelineNode(DISCARD_ID, "rsi.discard", {}, [], {}, loc)]


def apply_rsi(config: PipelineConfig, registry: Registry) -> PipelineConfig:
    """Return `config` with the `rsi:` setting applied. Raises `SettingError` when it is invalid."""
    setting = parse_setting(config.extra.get("rsi"))
    if setting is None:
        return config
    root = config.root
    taken = {step.id for step in _walk(root)}
    clash = [name for name in _RESERVED if name in taken]
    if clash:
        raise SettingError(f"step id(s) {', '.join(clash)} are reserved for `rsi`")
    if root.use == "sequence" and root.id == "root" and not root.config:
        children = list(root.children)
        start, end = (
            _named_scope(children, setting.scope, registry)
            if setting.scope
            else _auto_scope(children, registry)
        )
    else:
        reason = _unsafe(root, registry)
        if reason is not None:
            raise SettingError(f"`rsi` cannot wrap the pipeline: {reason}")
        if not _has_agent(root):
            raise SettingError("`rsi` found no agent step to improve")
        children, start, end = [replace(root, id="rsi_scope")], 0, 1
    _check_wires(children, start, end)
    wrapper = PipelineNode(
        IMPROVE_ID,
        "rsi.harness2",
        setting.harness2.model_dump(exclude_defaults=True),
        children[start:end],
        {},
        root.loc,
    )
    rebuilt = [*children[:start], wrapper, *_adoption_nodes(setting, root), *children[end:]]
    new_root = PipelineNode("root", "sequence", {}, rebuilt, {}, root.loc)
    return replace(config, root=new_root)
