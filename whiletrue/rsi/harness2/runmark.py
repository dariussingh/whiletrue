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
"""Rollout identity: was this rollout finished, and by which harness?

Completion is recorded explicitly by the code that observed the run return; the presence of
output is not enough. The marker also fingerprints the harness that produced the rollout, so a
rollout from a different configuration is never reused (this is what makes resume safe).

Ported from Harness² `core/runmark.py`. Differences: typed `Mark`, a fingerprint over a `Tree`
(a mapping) as well as a directory, SHA-256 instead of SHA-1, and the marker file is renamed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from whiletrue.rsi.harness2.tree import Tree

MARK = ".rsi_complete"
VERSION = 1
BASE = "base"


@dataclass(frozen=True)
class Mark:
    run_id: str
    harness: str
    at: float
    version: int = VERSION
    extra: dict[str, Any] = field(default_factory=lambda: {})


def fingerprint(harness: Tree | None) -> str:
    """A stable digest of a harness tree. `None` means the inherited base harness."""
    if harness is None:
        return BASE
    digest = hashlib.sha256()
    for rel in sorted(harness):
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(harness[rel].encode())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def mark_complete(
    where: Path, *, run_id: str, harness: Tree | None = None, extra: dict[str, Any] | None = None
) -> Path:
    """Record that `run_id` finished. Call only after the rollout returned normally."""
    where.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": VERSION,
        "run_id": run_id,
        "at": time.time(),
        "harness": fingerprint(harness),
        "extra": extra or {},
    }
    temp = where / f"{MARK}.{os.getpid()}.tmp"
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    final = where / MARK
    os.replace(temp, final)
    return final


def read_mark(where: Path) -> Mark | None:
    try:
        raw: Any = json.loads((where / MARK).read_text(encoding="utf-8"))
        return Mark(
            run_id=str(raw["run_id"]),
            harness=str(raw["harness"]),
            at=float(raw["at"]),
            version=int(raw["version"]),
            extra=dict(raw.get("extra") or {}),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_complete(where: Path, *, harness: Tree | None = None, match_harness: bool = True) -> bool:
    """Whether the artifacts at `where` may be reused: a current marker for the same harness."""
    mark = read_mark(where)
    if mark is None or mark.version != VERSION:
        return False
    return not match_harness or mark.harness == fingerprint(harness)


def why_not(where: Path, *, harness: Tree | None = None) -> str:
    """A sentence for a log line explaining why `where` is or is not reusable."""
    mark = read_mark(where)
    if mark is None:
        return f"no {MARK}: never finished, or finished before completion was recorded"
    if mark.version != VERSION:
        return f"marker version {mark.version} != {VERSION}"
    want = fingerprint(harness)
    if mark.harness != want:
        return (
            f"harness fingerprint {mark.harness} != {want}: produced by a different configuration"
        )
    return "reusable"
