# Plans

Plans, design discussions and reviews, one Markdown file each, for
traceability. Conventions are in [`CLAUDE.md`](../CLAUDE.md#plans-and-prompts-traceability):
files are named `YYYY-MM-DD-<slug>.md`, start with the verbatim prompts that
led to them, and record decisions.

| Date | File | Status |
|---|---|---|
| 2026-10-19 | [Input maps and heights: walkthrough of the maps branch](2026-10-19-maps-walkthrough.md) | historical |
| 2026-10-23 | [Stage A: geometry accuracy, discussion and decisions](2026-10-23-stage-a-geometry-discussion.md) | decided |
| 2026-10-23 | [Stage A: geometry accuracy, implementation plan](2026-10-23-stage-a-geometry-implementation.md) | done (A0–A10) |
| 2026-10-24 | [Stage A: summary for presentation](2026-10-24-stage-a-summary.md) | done |
| 2026-10-25 | [One run config, hashed, and one folder per run (issue #8)](2026-10-25-run-spec-and-run-folders.md) | done |
| 2026-10-26 | [Stage C: long-tail diversity, discussion and decisions](2026-10-26-stage-c-discussion.md) | decided |
| 2026-10-26 | [Stage C: long-tail diversity, implementation plan](2026-10-26-stage-c-implementation.md) | done (C0–C10) |
| 2026-10-27 | [Stage C: summary for presentation](2026-10-27-stage-c-summary.md) | done |
| 2026-10-27 | [Slim down the benchmark](2026-10-27-slim-benchmark.md) | done |
| 2026-10-27 | [Docs refactor: less duplication, easier navigation](2026-10-27-docs-refactor.md) | done |

The review these come from is `Roadgen AV Readiness Review.pdf` in the repo
root (written in an earlier chat; its prompts were not saved).

`scripts/` holds small helpers used to produce numbers quoted in a plan;
`baselines/` holds measurement reports a plan compares against.
