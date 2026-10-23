from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from whiletrue.core.config import PipelineConfig, PipelineNode, SourceLoc
from whiletrue.core.module import ConfigIssue, PortSpec, ValidateContext
from whiletrue.core.registry import Registry

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    node_id: str
    loc: SourceLoc


def has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.severity == "error" for d in diagnostics)


def format_diagnostic(d: Diagnostic) -> str:
    return f"{d.loc.file}:{d.loc.line}:{d.loc.col}: {d.severity}: {d.message} [{d.node_id}]"


def _first_port(ports: list[PortSpec], preferred: str) -> PortSpec | None:
    return next((p for p in ports if p.name == preferred), ports[0] if ports else None)


def validate_pipeline(
    config: PipelineConfig, registry: Registry, *, cwd: str | None = None
) -> list[Diagnostic]:
    """Static validation: everything that can be known before any tokens are spent.

    Resolves modules, checks config schemas, wiring caps and the safety rules.
    """
    vctx = ValidateContext(cwd=cwd if cwd is not None else os.getcwd(), registry=registry)
    out: list[Diagnostic] = []

    def push(node: PipelineNode, severity: Severity, message: str) -> None:
        out.append(Diagnostic(severity, message, node.id, node.loc))

    def push_issues(node: PipelineNode, issues: list[ConfigIssue]) -> None:
        for issue in issues:
            push(node, issue.severity, issue.message)

    def declared(node: PipelineNode, direction: Literal["in", "out"]) -> PortSpec | None:
        cap = node.ports.get(direction) if node.ports else None
        return PortSpec(direction, cap) if cap is not None and registry.caps.has(cap) else None

    def in_port(node: PipelineNode) -> PortSpec | None:
        resolved = registry.resolve(node.use)
        if node.ports is not None:
            return declared(node, "in")
        if resolved is None or resolved.kind != "action":
            return None
        return _first_port(resolved.module.ports.inputs, "in")

    def out_port(node: PipelineNode) -> PortSpec | None:
        resolved = registry.resolve(node.use)
        if node.ports is not None:
            return declared(node, "out")
        if resolved is None or resolved.kind != "action":
            return None
        return _first_port(resolved.module.ports.outputs, "out")

    def visit(node: PipelineNode) -> None:
        resolved = registry.resolve(node.use)
        if resolved is None:
            known = registry.ids()
            suffix = f" (known: {', '.join(known)})" if known else ""
            push(node, "error", f'Unknown module "{node.use}"{suffix}')
        else:
            module = resolved.module
            try:
                parsed = module.config.model_validate(node.config)
            except ValidationError as error:
                for issue in error.errors():
                    loc = ".".join(str(p) for p in issue["loc"])
                    at = f' at "{loc}"' if loc else ""
                    push(node, "error", f'Invalid config for "{node.use}": {issue["msg"]}{at}')
            else:
                if module.validate is not None:
                    push_issues(node, module.validate(parsed, vctx))
                if resolved.kind == "strategy" and module.validate_node is not None:
                    push_issues(node, module.validate_node(parsed, node, vctx))

            if resolved.kind == "action":
                if node.children:
                    push(
                        node, "error", f'Action module "{node.use}" cannot have child steps (`do`)'
                    )
                if module.id.startswith("gate.") and module.effects != "deterministic":
                    push(node, "error", f'Gate "{module.id}" must be deterministic')
                if module.effects == "side-effect" and module.idempotency_key is None:
                    push(
                        node,
                        "error",
                        f'Side-effect module "{module.id}" must provide an idempotencyKey',
                    )
            elif module.chain:
                for prev, nxt in zip(node.children, node.children[1:], strict=False):
                    source = out_port(prev)
                    target = in_port(nxt)
                    if (
                        source is not None
                        and target is not None
                        and not registry.caps.compatible(source.cap, target.cap)
                    ):
                        push(
                            nxt,
                            "error",
                            f'Cannot wire "{prev.id}".{source.name} ({source.cap}) '
                            f'into "{nxt.id}".{target.name} ({target.cap})',
                        )
        if node.ports is not None:
            for name, cap in node.ports.items():
                if not registry.caps.has(cap):
                    push(node, "error", f'Composite port "{name}" declares unknown cap "{cap}"')
        for child in node.children:
            visit(child)

    visit(config.root)
    return out
