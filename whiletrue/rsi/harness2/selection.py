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
"""Blind selection: choose one final output from anonymous candidates.

The selector never sees which candidate came from which harness, nor any score. Candidates are
given arbitrary ids that are shuffled with a seed derived from the item, so a rerun asks the same
question. A selector that fails never raises: the caller falls back to a delivered base output.

Ported from Harness² `core/selection.py` (the anonymisation, the pool rule and the answer parser;
the benchmark rendering moved to `context.py`).
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from whiletrue.rsi.harness2.ledger import Mode

_CHOICE = re.compile(r"```choice\s*\n?\s*([A-Za-z0-9_-]+)\s*\n?\s*```", re.DOTALL)
_BLOCK = {
    name: re.compile(rf"<{name}>(.*?)</{name}>", re.DOTALL)
    for name in ("why", "experience", "edits", "decision")
}

Evidence = Literal["deliverable", "trajectory"]


@dataclass
class Selection:
    """The outcome of choosing among finals. Never a score."""

    item_id: str
    mode: Mode
    k: int
    chosen_id: str = ""
    chosen_label: str = ""
    chosen_is_base: bool = False
    why: str = ""
    id_map: dict[str, str] = field(default_factory=lambda: {})
    dropped: list[str] = field(default_factory=lambda: [])
    n_candidates: int = 0
    ok: bool = False
    fallback: bool = False
    trivial: bool = False
    error: str = ""
    usd: float = 0.0
    evidence: Evidence = "deliverable"


def assign_ids(labels: Sequence[str], *, item_id: str, k: int, mode: str) -> dict[str, str]:
    """Anonymous id to label: a canonical sort, then a seeded shuffle, stable across reruns."""
    seed = int(hashlib.sha256(f"{item_id}|{k}|{mode}".encode()).hexdigest()[:12], 16)
    ordered = sorted(labels)
    random.Random(seed).shuffle(ordered)
    return {anon_id(i): label for i, label in enumerate(ordered)}


def anon_id(index: int) -> str:
    """A..Z, AA..AZ, BA...: always accepted by the choice parser."""
    out = ""
    n = int(index) + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def final_labels(mode: Mode, k: int) -> list[str]:
    """The k finals of a mode: `agg1..aggk` (parallel) or `c1..ck` (sequential).

    The reference rollouts are not in the pool, so abstention is impossible by construction.
    """
    prefix = "agg" if mode == "parallel" else "c"
    return [f"{prefix}{i}" for i in range(1, int(k) + 1)]


def selection_pool(mode: Mode, k: int, rollouts: Mapping[str, str]) -> dict[str, str]:
    """Exactly the k finals of `mode`, in label order, that actually produced a rollout."""
    return {label: rollouts[label] for label in final_labels(mode, k) if label in rollouts}


def is_base(label: str) -> bool:
    return label.startswith("base")


def parse_choice(reply: str, valid: Sequence[str]) -> str | None:
    """The id in the reply's last ```choice``` block, or None when it is missing or unknown."""
    matches = _CHOICE.findall(reply)
    if not matches:
        return None
    choice = matches[-1].strip()
    return choice if choice in valid else None


def parse_block(reply: str, name: Literal["why", "experience", "edits", "decision"]) -> str:
    """The text of the last `<name>...</name>` block in a reply, or an empty string."""
    matches = _BLOCK[name].findall(reply)
    return matches[-1].strip() if matches else ""
