from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from whiletrue.core.config import PipelineConfig, PipelineNode, SourceLoc
from whiletrue.core.module import ConfigIssue, PortSpec, ValidateContext
from whiletrue.core.registry import Registry
from whiletrue.core.source import SourceError, source_spec

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


class _Validator:
    """Walks the pipeline tree and collects diagnostics; each rule is its own method."""

    def __init__(self, config: PipelineConfig, registry: Registry, cwd: str) -> None:
        self._config = config
        self._registry = registry
        self._vctx = ValidateContext(cwd=cwd, registry=registry)
        self.out: list[Diagnostic] = []

    def _push(self, node: PipelineNode, severity: Severity, message: str) -> None:
        self.out.append(Diagnostic(severity, message, node.id, node.loc))

    def _push_issues(self, node: PipelineNode, issues: list[ConfigIssue]) -> None:
        for issue in issues:
            self._push(node, issue.severity, issue.message)

    def run(self) -> list[Diagnostic]:
        self._check_source()
        self._visit(self._config.root)
        return self.out

    def _check_source(self) -> None:
        root = self._config.root
        try:
            source = source_spec(self._config.extra)
        except SourceError as error:
            self.out.append(Diagnostic("error", str(error), root.id, root.loc))
            return
        if source is not None and self._registry.get_provider("source", source[0]) is None:
            self.out.append(Diagnostic("error", f'Unknown source "{source[0]}"', root.id, root.loc))

    # ---- ports -------------------------------------------------------------------------

    def _declared(self, node: PipelineNode, direction: Literal["in", "out"]) -> PortSpec | None:
        cap = node.ports.get(direction) if node.ports else None
        return (
            PortSpec(direction, cap) if cap is not None and self._registry.caps.has(cap) else None
        )

    def _port(self, node: PipelineNode, direction: Literal["in", "out"]) -> PortSpec | None:
        if node.ports is not None:
            return self._declared(node, direction)
        resolved = self._registry.resolve(node.use)
        if resolved is None or resolved.kind != "action":
            return None
        ports = resolved.module.ports
        return _first_port(ports.inputs if direction == "in" else ports.outputs, direction)

    # ---- rules -------------------------------------------------------------------------

    def _visit(self, node: PipelineNode) -> None:
        resolved = self._registry.resolve(node.use)
        if resolved is None:
            known = self._registry.ids()
            suffix = f" (known: {', '.join(known)})" if known else ""
            self._push(node, "error", f'Unknown module "{node.use}"{suffix}')
        else:
            self._check_config(node, resolved)
            if resolved.kind == "action":
                self._check_action_rules(node, resolved.module)
            elif resolved.module.chain:
                self._check_chain_wiring(node)
        self._check_declared_ports(node)
        for child in node.children:
            self._visit(child)

    def _check_config(self, node: PipelineNode, resolved: Any) -> None:
        module = resolved.module
        try:
            parsed = module.config.model_validate(node.config)
        except ValidationError as error:
            for issue in error.errors():
                loc = ".".join(str(p) for p in issue["loc"])
                at = f' at "{loc}"' if loc else ""
                self._push(node, "error", f'Invalid config for "{node.use}": {issue["msg"]}{at}')
            return
        if module.validate is not None:
            self._push_issues(node, module.validate(parsed, self._vctx))
        if resolved.kind == "strategy" and module.validate_node is not None:
            self._push_issues(node, module.validate_node(parsed, node, self._vctx))

    def _check_action_rules(self, node: PipelineNode, module: Any) -> None:
        if node.children:
            self._push(node, "error", f'Action module "{node.use}" cannot have child steps (`do`)')
        if module.id.startswith("gate.") and module.effects != "deterministic":
            self._push(node, "error", f'Gate "{module.id}" must be deterministic')
        if module.effects == "side-effect" and module.idempotency_key is None:
            self._push(
                node, "error", f'Side-effect module "{module.id}" must provide an idempotencyKey'
            )

    def _check_chain_wiring(self, node: PipelineNode) -> None:
        for prev, nxt in zip(node.children, node.children[1:], strict=False):
            source, target = self._port(prev, "out"), self._port(nxt, "in")
            if (
                source is not None
                and target is not None
                and not self._registry.caps.compatible(source.cap, target.cap)
            ):
                self._push(
                    nxt,
                    "error",
                    f'Cannot wire "{prev.id}".{source.name} ({source.cap}) '
                    f'into "{nxt.id}".{target.name} ({target.cap})',
                )

    def _check_declared_ports(self, node: PipelineNode) -> None:
        for name, cap in (node.ports or {}).items():
            if not self._registry.caps.has(cap):
                self._push(node, "error", f'Composite port "{name}" declares unknown cap "{cap}"')


def validate_pipeline(
    config: PipelineConfig, registry: Registry, *, cwd: str | None = None
) -> list[Diagnostic]:
    """Static validation: everything that can be known before any tokens are spent.

    Resolves modules, checks config schemas, wiring caps and the safety rules.
    """
    return _Validator(config, registry, cwd if cwd is not None else os.getcwd()).run()
