# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified by the whiletrue authors: ported and refactored from
# https://github.com/google-research/harness2 (commit 1cfe254) into whiletrue.
# See third_party/harness2/PROVENANCE.md for the list of changes.
"""The editable surface of an agent framework: which files make up a harness.

A harness is the system around a model: always-on prompt files, skills, subagents, scripts and
(on some frameworks) hooks or plugins. A `SurfaceSpec` is data that says where each component
lives and which rules apply to it, so the tree logic in `tree.py` exists once for every framework.

Ported from Harness² `core/tree.py` (the c1..c8 component tree). Differences: the path patterns
are fields of the spec instead of module constants, a `restricted` list names the paths that
execute code and so always need a person's approval, and a Claude Code surface was added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: A skill, subagent or script name: lowercase letters, digits, `_` and `-`.
NAME = r"[a-z0-9][a-z0-9_\-]*"

SkillNameRule = Literal["must_match", "overrides_dir"]


@dataclass(frozen=True)
class SurfaceSpec:
    """Paths, caps and per-framework rules for one agent framework's editable files."""

    name: str
    #: Files that are in context on every turn (system prompt, guardrails, memory, tool guidance).
    always_on: tuple[str, ...]
    always_on_max: int
    always_on_cap_reason: str
    #: Directory holding `<name>/SKILL.md` skills.
    skill_dir: str
    skill_name_rule: SkillNameRule
    #: Advice shown when a skill is never named in the always-on text; `{name}` is filled in.
    trigger_hint: str
    #: The always-on file that gets an automatic trigger line, and that line's template.
    autotrigger_file: str
    trigger_line: str
    #: Full-match pattern for a subagent file; group 1 is its name. None: no subagents.
    subagent_pattern: re.Pattern[str] | None
    subagent_requires_mode: bool
    #: Full-match pattern for a helper script, and for a script path mentioned in prose.
    script_pattern: re.Pattern[str]
    script_ref_pattern: re.Pattern[str]
    #: Full-match pattern for a plugin file (group 1 is its name), when the framework has them.
    plugin_pattern: re.Pattern[str] | None = None
    hooks_file: str | None = None
    hook_events: frozenset[str] = frozenset()
    compile_skill_scripts: bool = False
    reject_empty_always_on: bool = False
    #: Files written (as empty-ish stubs) so an improver can see where components go.
    stubs: tuple[tuple[str, str], ...] = ()
    #: Paths that execute code or loosen permissions. Never adopted without approval.
    restricted: tuple[re.Pattern[str], ...] = ()

    @property
    def skill_pattern(self) -> re.Pattern[str]:
        return re.compile(rf"{re.escape(self.skill_dir)}/({NAME})/SKILL\.md")

    @property
    def skill_script_pattern(self) -> re.Pattern[str]:
        return re.compile(rf"{re.escape(self.skill_dir)}/{NAME}/scripts/[A-Za-z0-9_.\-]+")

    def is_restricted(self, rel: str) -> bool:
        """Whether a change to `rel` must be approved by a person, whatever the adopt policy."""
        return any(pattern.fullmatch(rel) for pattern in self.restricted)


_PY_SCRIPT = r"[a-z0-9_][a-z0-9_]*\.py"


def _script_ref(script_dir: str, skill_dir: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:python3?\s+)((?:{re.escape(skill_dir)}/[\w\-]+/)?{re.escape(script_dir)}/[\w.\-]+\.py)"
    )


CLAUDE_CODE_SURFACE = SurfaceSpec(
    name="claude-code",
    always_on=("CLAUDE.md", ".claude/rules/guardrails.md", ".claude/rules/tool-guidance.md"),
    always_on_max=24_000,
    always_on_cap_reason="every token here competes with the agent's own output budget",
    skill_dir=".claude/skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to CLAUDE.md or "
        '.claude/rules/guardrails.md, e.g. "When <the situation this skill handles>, use the '
        '`{name}` skill."'
    ),
    autotrigger_file="CLAUDE.md",
    trigger_line="- When {desc}, use the `{name}` skill.\n",
    subagent_pattern=re.compile(rf"\.claude/agents/({NAME})\.md"),
    subagent_requires_mode=False,
    script_pattern=re.compile(rf"\.claude/scripts/{_PY_SCRIPT}"),
    script_ref_pattern=_script_ref(".claude/scripts", ".claude/skills"),
    stubs=(),
    restricted=(
        re.compile(r"\.claude/scripts/.+"),
        re.compile(r"\.claude/skills/[^/]+/scripts/.+"),
    ),
)

CODEX_SURFACE = SurfaceSpec(
    name="codex",
    always_on=("AGENTS.md",),
    always_on_max=8_000,
    always_on_cap_reason=(
        "AGENTS.md is injected whole into EVERY turn, and codex additionally "
        "truncates it silently at 32,768 bytes"
    ),
    skill_dir=".agents/skills",
    skill_name_rule="overrides_dir",
    trigger_hint=(
        'Fix it by adding a trigger line naming `{name}` to AGENTS.md, e.g. "When '
        "<the situation this skill handles>, read .agents/skills/{name}/SKILL.md "
        'and follow it."'
    ),
    autotrigger_file="AGENTS.md",
    trigger_line="- When {desc}, read .agents/skills/{name}/SKILL.md and follow it.\n",
    subagent_pattern=None,
    subagent_requires_mode=False,
    script_pattern=re.compile(rf"scripts/{_PY_SCRIPT}"),
    script_ref_pattern=_script_ref("scripts", ".agents/skills"),
    hooks_file=".codex/hooks.json",
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "PermissionRequest",
            "PreCompact",
            "PostCompact",
            "SessionStart",
            "SubagentStart",
            "SubagentStop",
            "UserPromptSubmit",
            "Stop",
        }
    ),
    compile_skill_scripts=True,
    reject_empty_always_on=True,
    restricted=(
        re.compile(r"scripts/.+"),
        re.compile(r"\.agents/skills/[^/]+/scripts/.+"),
        re.compile(r"\.codex/hooks\.json"),
    ),
)

SURFACES: dict[str, SurfaceSpec] = {
    CLAUDE_CODE_SURFACE.name: CLAUDE_CODE_SURFACE,
    CODEX_SURFACE.name: CODEX_SURFACE,
}

#: Which surface a runner id edits. Runners not listed here cannot be improved yet.
SURFACE_FOR_RUNNER: dict[str, str] = {
    "claude-code-headless": CLAUDE_CODE_SURFACE.name,
    "claude-code": CLAUDE_CODE_SURFACE.name,
    "codex": CODEX_SURFACE.name,
}


def surface_for_runner(runner_id: str) -> SurfaceSpec | None:
    name = SURFACE_FOR_RUNNER.get(runner_id)
    return SURFACES.get(name) if name is not None else None
