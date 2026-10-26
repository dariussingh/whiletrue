# Provenance: Harness²

| | |
| --- | --- |
| Upstream | https://github.com/google-research/harness2 |
| Pinned commit | `1cfe254e8d25b15f37048c3730134686abe60335` (2026-10-13, "Fix arXiv link") |
| License | Apache License 2.0 (`LICENSE` in this directory is a verbatim copy) |
| Copyright | Copyright 2026 Google LLC (kept in each ported file's header) |
| Paper | Xu et al., "Harness²: Recursive Agent Harnessing for an Open World", arXiv:2610.14224 |

## What is (and is not) taken

Method code is ported and refactored into `whiletrue/rsi/harness2/` (todo item 80). It is not a verbatim copy: it is retyped, restructured and tested to this repo's conventions. Each file keeps the upstream header and carries a modification notice.

| Upstream path | whiletrue path | Role |
| --- | --- | --- |
| `harness2/core/tree.py` | `whiletrue/rsi/harness2/tree.py`, `surfaces.py` | The c1..c8 editable component tree and per-framework surfaces |
| `harness2/core/ledger.py` | `whiletrue/rsi/harness2/ledger.py` | Budget identity R = 4k rollouts, P = 3k improvement stages |
| `harness2/core/library.py` | `whiletrue/rsi/harness2/library.py` | Per-domain experience library |
| `harness2/core/runmark.py` | `whiletrue/rsi/harness2/runmark.py` | Rollout completion marker and harness fingerprint |
| `harness2/core/selection.py` | `whiletrue/rsi/harness2/selection.py` | Blind, answer-free selection |
| `harness2/core/context.py` | `whiletrue/rsi/harness2/context.py` | Evidence tree and answer-leak tripwires |
| `harness2/core/prompts.py` | `whiletrue/rsi/harness2/prompts.py` | Improver, composer, selector and reflector prompts |
| `harness2/core/recursion.py`, `proposer.py`, `spec.py` | `whiletrue/rsi/harness2/strategy.py` and the runtime adapters | Orchestration is rebuilt on whiletrue's runtime, following the same stage order |

Not taken: `harness2/benchmarks/`, `harness2/substrate/`, `harness2/cli/`, `harness2/core/schedule.py`, `judge.py`, `report.py`, upstream `third_party/` (the JobBench, LAB and WorkBuddy runners and patches), and all datasets. Those keep their own upstream licenses, and the WorkBuddy-derived code carries a territorial limitation. whiletrue does not copy or redistribute any of it.

## Rules

1. Ported files keep the upstream license and copyright header, and say they were modified (Apache-2.0 section 4). `LICENSE` in this directory is a verbatim copy.
2. `scripts/check_provenance.py` (todo item 78) checks the header and the table above for every file under `whiletrue/rsi/harness2/`.
3. Do not port benchmark code or data.

## Modifications

Listed per file in its module docstring. Nothing has been ported yet.
