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

## Group 2: F20, F5, F7, F3

**2026-11-08, prompt 2**

> continue to implemet f20 and f5 f7 and f3

### F20: no traps in the examples

The examples rerun without `--allow-traps` (on group 1's code) showed traps
in every map example: city 8, suburb 7, mixed_city 17, hill_town 25, and 2 in
freeways and highway_corridor. Diamond and cloverleaf had 4 each, which
stopped their drive-check before it ran. There were three kinds:

1. Interior street and avenue dead ends where no extension, trim or bulb fit.
2. Highways running off the map, and the leg ends of a lone junction.
3. Highways that stop inside the area (city seed 7 node 103, 783 m in;
   highway_corridor node 23, 552 m in).

Changes:

- **Exits** (`metadata.exits`): dead ends where a road leaves the area. lsystem
  lists the dead ends whose road would leave its area within the largest
  block size; the `junction` generator lists its leg ends. The traffic check
  links exits to one "outside" vertex, so traffic leaving the map may come
  back at any exit. They are no traps.
- **Cut** (`cut_dead_ends`, after the bulbs): every other dead end goes back
  to its junction, highways included. If that would leave a two-road node
  with a class or lane change, the bigger road steps down to the smaller
  one's class, lanes, width and speed up to its next junction. If a road is
  one-way (a ramp), the dead end stays.
- **Inner lane offset** (`_inner_offset` in `geometry/alignment.py`) now
  counts half the median on roads that will get one. Without it, an
  advisory speed on a highway curve was set for a lane centre 1 m further in
  than the real one. hill_town road 20 needed 3.09 m/s² at its posted speed
  (limit 3.00) once the network changed. This bug was latent: the median
  comes from the cross-section, which the alignment did not know about.

Two approaches were tried and dropped:

- *The bigger road continues along the smaller one* (upgrading a street to
  an avenue): the maps annotation then gives it the avenue's speed on a
  street's curves, and hill_town broke the lateral-acceleration check.
- *Skip every cut that leaves a class change*: this left 2–5 traps in each
  map example, because the stroke hierarchy makes avenue-into-street T
  junctions common.

| Example | traps before → after | drive-check |
|---|---|---|
| city seed 7 | 8 → 0 | 2779/2779 |
| manhattan_like 1, paris | 0 → 0 | 2631/2631, 1898/1898 |
| suburb 1 | 7 → 0 | 1751/1751 (the 2 earlier failures, also on group 1's code, went with the F3 fix) |
| mixed_city 1 | 17 → 0 | 3803/3803 |
| hill_town 1 | 25 → 0 | 2387/2387 |
| freeways (city 1, separated) | 2 → 0 | 135/135 |
| highway_corridor 1 | 2 → 1 (node 23: freeway stops inside the area) | not run (traps) |
| diamond, cloverleaf | 4 → 0 | 40/40, 48/48 |

`runs/examples.sh` drops `--allow-traps` except on highway_corridor. Docs
drop it from their examples (README, maps, heights, scene, lsystem).

Presets (`LSystemGenerator().generate`, no maps): every one is at 0 traps
except suburb seed 2, which has 1 (a cut there would leave a one-way or
uneven road). The suburb seed 1 dead-end share is 0.44 %, and its dead ends
are now exits.

### F5: interchange drive-check

Without the traps the drive-check runs, and it is fast. Diamond takes 1.0 s
(load 0.1 s, 40 movements 0.3 s, 38 lanes 0.6 s) and passes 40/40.
Cloverleaf takes 1.5 s and passes 48/48. The highway_corridor drive-check
passed 1823/1823 movements on group 1's code with `--allow-traps`. The
minutes-long runs of stage C are gone. Closed.

### F7: untextured roads

Not reproduced. odrviewer finds `asphalt.jpg` and `grass.jpg` in
`/opt/esmini/resources/models` (log: "Texture file asphalt.jpg located").
Only the asphalt material has a texture; esmini draws concrete (sidewalks,
curbs) and border lanes in plain colour by design. A close-up screenshot of
organic_town looks the same as esmini's own `straight_500m.xodr` from the same
camera height. The only surfaces of ours that esmini's samples don't have are
the plain junction patch polygons (D10, kept; "fix esmini later"). Closed,
unless the user has a screenshot that shows otherwise.

### F3 (and F4): roundabout spurs

The `mixed_city` sweep without HD (`metrics --sweep 200 --preset mixed_city
--no-hd`, 92 s on all CPUs) failed on 2 of 200 seeds after F20 (10 in stage
B):

- seed 10: edge 506, a 19.5 m avenue spur from a roundabout entry to a
  104° bend;
- seed 167: edge 295, an 11.7 m street spur into a 108° bend.

The roundabout transform checked the far end's trims only where it was a
junction. Where the far end only bends, the local graph it checks the
alignment on ended there, so the corner was never fitted. Now each arm whose
far end has two roads takes that bend's next road into the local graph, and
the existing smooth check rejects the roundabout if the bend becomes a
junction or does not fit. The node is left as a plain junction.

| Sweep, 200 `mixed_city` seeds | failed before → after |
|---|---|
| without HD | 2 → 0 (103 s) |
| with HD | 0 (330 s; stage B: 0 with "bare road ends", B9) |

`tests/test_roundabout.py::test_short_spur_into_a_tight_bend_gets_no_roundabout`
has a 17 m spur into a 110° bend; it fails on the old code. The fix also
removed the 2 failed suburb drive-check movements (both at a roundabout
entry).

### Decisions (group 2)

| # | Decision | By |
|---|---|---|
| 6 | Dead ends where a road leaves the area are *exits*, listed by the generator in `metadata.exits`; the traffic check treats them as connected through the outside world. This is the graph-level counterpart of a map cut out of a bigger network. | Claude: the validator stays generator-independent, and a highway that ends mid-map still counts as a trap. |
| 7 | Dead ends still left after the bulbs are cut back to their junction, highways included. A class change left behind is resolved by stepping the bigger road down, never up. | Claude: stepping down can only lower speeds, so no curve breaks the dynamics check; stepping up broke hill_town. |
| 8 | highway_corridor keeps `--allow-traps` in the examples until its freeway end is fixed. | Claude |
