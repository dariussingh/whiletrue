# Third-party notices

whiletrue is MIT licensed (see [LICENSE](LICENSE)). Third-party code under `third_party/`, benchmarks and datasets retain their upstream licenses.

## Harness²

The optional recursive self-improvement feature (`rsi`) is built on the method and code of Harness² by Google Research. The method code is ported and refactored into this repository as `whiletrue.rsi.harness2`; users install no extra dependency.

- Source: https://github.com/google-research/harness2 (pinned commit `1cfe254e8d25b15f37048c3730134686abe60335`)
- License: Apache License 2.0. The license text is at [third_party/harness2/LICENSE](third_party/harness2/LICENSE).
- Copyright 2026 Google LLC.
- Each ported file in `whiletrue/rsi/harness2/` keeps its upstream copyright header and carries a notice that it was modified. The files, their upstream paths and the modifications are listed in [third_party/harness2/PROVENANCE.md](third_party/harness2/PROVENANCE.md). Those files are licensed under Apache-2.0, not MIT.
- Not taken: the benchmark runners, patches and datasets in the upstream repository (JobBench, LAB, WorkBuddy-Bench). They have their own licenses, and the WorkBuddy-derived code includes a territorial limitation on use. whiletrue does not copy, bundle or redistribute them.

Please cite the paper if you use `rsi` in research:

```bibtex
@misc{xu2026harness2,
  title         = {Harness$^2$: Recursive Agent Harnessing for an Open World},
  author        = {Xu, Ruiyao and Chen, Yanfei and CuiZhu, Zhongying and Dalvi Mishra, Bhavana and Ming, Yifei and Yu, Han and Han, Rujun and Lee, Chen-Yu and Pfister, Tomas},
  year          = {2026},
  eprint        = {2610.14224},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  doi           = {10.48550/arXiv.2610.14224},
  url           = {https://arxiv.org/abs/2610.14224}
}
```

whiletrue is an independent project. It is not affiliated with or endorsed by Google or the Harness² authors.
