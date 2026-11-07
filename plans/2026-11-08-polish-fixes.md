# Polish 2: fixes (section 2 of the polish list)

Branch `dev/fix-f-items`. Works through section 2 of
[2026-11-07-polish-todo.md](2026-11-07-polish-todo.md), 3–4 items per group,
one commit per group. The order follows that list's "Suggested order":
the three failing tests from stage R first (F17–F19), then F21 from the user's screenshot.

## Prompts

**2026-11-08, prompt 1**

> Now, I am in a new branch. We can start fix stage.

## Decisions

| # | Decision | By |
|---|---|---|
| 1 | F21: keep the rule. Streets in the urban and suburban profiles have no centre line (German residential practice), even though the stroke hierarchy made most two-way roads streets. Closed as by design. | user |
| 2 | F18 / F20: a road cut off by the edge of the area is not a dead end by design. The dead-end pass extends it, or else cuts it back to its last junction whatever its length, also in suburbs. The old rule ("roads leaving the area stay") is gone. | user |
| 3 | With `boundary: ring` the edge ends stay, because the ring road collects them. | Claude: trimming them first would leave the ring without roads to join. |
| 4 | F19: the generator is right, the test was wrong. The edges off the grid angle are cul-de-sac bulb loops, which are round. The test now leaves them out. | Claude |
| 5 | F17: the body-overlap validator returns no errors for fewer than two roads, because nothing can overlap. | Claude |

## Group 1: F17, F18, F19, F21

**F17:** the pipeline crashed in `check_body_overlaps_tree`. A 2 × 2 grid
with only an fbm elevation map grows no road at all. `bodies` is then
empty, and shapely's `STRtree.query` rejects the empty float array
(`TypeError: Array should be of object dtype`). It is now guarded in
`roadgen/validate/clearance.py`.

**F18:** suburb seed 1 had 15 dead ends among 458 nodes (3.28 %). 9 of them were
streets cut off at the area edge (y ≈ 10, x ≈ 6, y ≈ 1190). The dead-end
pass skipped them because it skips every suburb node, and the rule kept
roads leaving the area anyway. The other 6 are interior suburb ends where
no bulb fits, or avenues, which never get bulbs.

A node counts as "at the edge" when going on `min_segment_length` straight
ahead would leave the area. Measured with
`LSystemGenerator().generate(LSystemParams.preset(p), seed)`:

| Preset, seed | nodes before → after | dead ends before → after |
|---|---|---|
| suburb 1 | 458 → 459 | 3.28 % → 1.53 % |
| suburb 2 | 468 → 444 | 1.71 % → 1.58 % |
| paris 0 | 290 → 277 | 0 % → 0 % |
| organic_town 1, city 2, city 7, manhattan_like 0 | unchanged | 0 % |

The `organic_town` and `hill_town_maps` fingerprints changed and were
re-recorded (intended). This group handles only the edge part of F20. The
traps at water and the `--allow-traps` flag in the examples are still open.

**F19:** the 8 off-angle edges (headings mod 90° of 49.6 and 79.6, each
15.5 m) were the loops of two cul-de-sac bulbs at area corners.

**F21:** in `city` seed 7, 618 of 719 two-way roads with one lane each way
have no centre line at the start, and `organic_town` seed 7 has 93 of 117.
The cause is `road_marks.no_street_centre: [urban, suburban]`, as
suspected. Kept (decision 1).

Tests: `test_dead_ends.py` has two new tests (an edge road cut back
whatever its length, and kept with a ring). The stub and bulb tests had
avenues running to the area edge, so they now keep them off the edge.
Full suite: 740 passed, 0 failed.
