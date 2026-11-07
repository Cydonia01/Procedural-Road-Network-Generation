# Plans

Plans, design discussions and reviews, one Markdown file each, for
traceability. Conventions are in [`CLAUDE.md`](../CLAUDE.md#plans-and-prompts-traceability):
files are named `YYYY-MM-DD-<slug>.md`, start with the verbatim prompts that
led to them, and record decisions.

| Date | File | Status |
|---|---|---|
| 2026-10-08 | [Copilot phase prompts (Phases 0–10), the project's first build instructions](2026-10-08-copilot-phase-prompts.md) | historical |
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
| 2026-10-29 | [Stage B: HD-map completeness, discussion and decisions](2026-10-29-stage-b-discussion.md) | decided |
| 2026-10-29 | [Stage B: HD-map completeness, implementation plan](2026-10-29-stage-b-implementation.md) | done (B0–B10) |
| 2026-10-31 | [esmini issue draft: RoadManager load time (corner roads, run-to-run variance)](2026-10-31-esmini-issue-draft.md) | draft, not filed (user decides) |
| 2026-11-01 | [Stage B: summary for presentation](2026-11-01-stage-b-summary.md) | done |
| 2026-11-06 | [Refinement review: natural networks, esmini look, what to add or drop](2026-11-06-refinement-review.md) | review, decided |
| 2026-11-06 | [Stage R (refinement): implementation plan](2026-11-06-stage-r-implementation.md) | R1–R6, Q done; rest moved to the polish list |
| 2026-11-07 | [What remains after stage R, open issues from earlier stages](2026-11-07-remaining-work-and-open-issues.md) | review |
| 2026-11-07 | [Polish stage: the complete to-do list (drops, fixes, tests)](2026-11-07-polish-todo.md) | review, decided (section 1 in progress) |
| 2026-11-08 | [Polish 1: drop old implementations](2026-11-08-polish-drops.md) | done (groups 1–4) |

The review these come from is `Roadgen AV Readiness Review.pdf` in the repo
root (written in an earlier chat; its prompts were not saved).

`scripts/` holds the helpers still in use (probes for the current stage, the
esmini issue repro); helpers of finished stages were deleted on 2026-11-08,
and their numbers stay quoted in the plans. `baselines/` holds measurement
reports a plan compares against.
