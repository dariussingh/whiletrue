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
"""Budget accounting: R = 4k rollouts and P = 3k improvement sessions per step.

One step of k recursion runs k reference rollouts of the base harness, 2k candidate rollouts
and k final rollouts (R = 4k), and k general proposals, k domain proposals and k compositions
(P = 3k). Selection and reflection are recorded outside this identity.

Ported from Harness² `core/ledger.py`, with typed entries and our own error type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["parallel", "sequential"]


def expected_rollouts(k: int) -> int:
    """R = 4k: k shared base + 2k candidates + k finals."""
    return 4 * int(k)


def expected_sessions(k: int) -> int:
    """P = 3k: k general + k domain + k compositions."""
    return 3 * int(k)


class BudgetError(Exception):
    """The ledger did not land on the budget identity."""


@dataclass
class Entry:
    kind: Literal["rollout", "session"]
    label: str
    usd: float = 0.0
    cached: bool = False
    attempts: int = 1


@dataclass
class Ledger:
    """Everything one mode spent on one item, recorded as it happens."""

    mode: Mode
    item_id: str
    k: int
    entries: list[Entry] = field(default_factory=lambda: [])

    def rollout(self, label: str, usd: float = 0.0, *, cached: bool = False) -> None:
        self.entries.append(Entry("rollout", label, usd, cached))

    def session(
        self, label: str, usd: float = 0.0, *, cached: bool = False, attempts: int = 1
    ) -> None:
        self.entries.append(Entry("session", label, usd, cached, attempts))

    @property
    def rollouts(self) -> int:
        return sum(1 for e in self.entries if e.kind == "rollout")

    @property
    def sessions(self) -> int:
        return sum(1 for e in self.entries if e.kind == "session")

    @property
    def usd(self) -> float:
        return round(sum(e.usd for e in self.entries), 6)

    def usd_of(self, kind: Literal["rollout", "session"]) -> float:
        return round(sum(e.usd for e in self.entries if e.kind == kind), 6)

    def labels(self, kind: Literal["rollout", "session"]) -> list[str]:
        return [e.label for e in self.entries if e.kind == kind]

    def assert_identity(self) -> None:
        """Raise unless the entries show exactly R = 4k and P = 3k with no duplicate label.

        Duplicates are checked first, because a duplicate can satisfy the counts while one
        candidate silently overwrote another.
        """
        want_r, want_p = expected_rollouts(self.k), expected_sessions(self.k)
        rolls, sess = self.labels("rollout"), self.labels("session")
        dupes = sorted(
            {x for x in rolls if rolls.count(x) > 1} | {x for x in sess if sess.count(x) > 1}
        )
        if not dupes and self.rollouts == want_r and self.sessions == want_p:
            return
        detail = [
            f"{self.mode}-{self.k} {self.item_id}: R={self.rollouts} (want {want_r}), "
            f"P={self.sessions} (want {want_p})",
            f"  rollouts: {', '.join(rolls) or '(none)'}",
            f"  sessions: {', '.join(sess) or '(none)'}",
        ]
        if dupes:
            detail.append(f"  DUPLICATE labels: {dupes}: two stages wrote the same run")
        raise BudgetError("\n".join(detail))

    def summary(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "item": self.item_id,
            "k": self.k,
            "rollouts": self.rollouts,
            "expected_rollouts": expected_rollouts(self.k),
            "sessions": self.sessions,
            "expected_sessions": expected_sessions(self.k),
            "usd": self.usd,
            "rollout_usd": self.usd_of("rollout"),
            "session_usd": self.usd_of("session"),
        }
