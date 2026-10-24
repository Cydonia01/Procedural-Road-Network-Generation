# Stage A (geometry accuracy): implementation plan

Status: **done** (2026-10-24): A0–A10 implemented and committed (`e0a60ce` … `58d6d16`); results below. Discussion, measurements and decisions:
[2026-10-23-stage-a-geometry-discussion.md](2026-10-23-stage-a-geometry-discussion.md).

## Prompts

**2026-10-23, prompt 2** (prompt 1 is in the discussion file)

> I prefer Option 1. Also, couldn't we find a solution for the caveat of the option 1? Decide the values of e and f yourself. for 3, do which is better. Scope includes A5. Firstly, add claude.md that you should create a markdown for each plan and put them into the plans folder. This is for traceability. Also, save prompts if possible. The discussion above should also be in markdown. Then, generate a implementation plan phase by phase. You can use any stack needed (like scipy you mentioned above). Also, you can utilize papers in files (read md files, pdfs are costly). Besides papers, you can use contemporary knowledge you have to make the implementation better.

**2026-10-23, prompt 3** (start of the implementation)

> go on

**2026-10-24, prompt 4**

> Yes. commit them. Just put source code and documentation. Leave out pdf, implementation-guide and plans. Then, continue with the A2 and also A3 if A2 is not so complex.

**2026-10-24, prompt 5**

> commit and continue with the rest of the phases. Finish all phases in one turn.

## Decisions this plan builds on

- Bends are merged **in the builder** (Option 1); the RoadGraph model gains
  only one optional field, `Node.radius_cap`.
- Design speed model: AASHTO `f_max(v)` table, `e_max` 0.04 (street,
  avenue) / 0.06 (highway).
- No legacy flag; a `geometry_version` stamp in the sidecar and run history.
- Scope: all of part A of the review.
- New explicit dependencies: `scipy`, `pyclothoids`.

## Goal and exit criteria

Measured by the A0 metrics on `coast_city`, `city_maps` and `organic`,
compared with the baseline A0 records:

| Metric | Baseline (A0) | Target after stage A | After stage A (A10) |
|---|---|---|---|
| Junctions with exactly two roads (coast_city / city_maps / organic) | 24 % / 32 % / 38 % | 0 % unless `Node.kind = "junction"` or the node can't be merged | 0 % / 1 % / 2 % (loops split, bends too tight for their inner lane) |
| Lateral acceleration at the posted or advisory speed, 90th percentile, roads and connecting roads | bends 32–35 m/s², turns 47–48 m/s² | ≤ 3.0 m/s² | roads 2.0–2.2, turns 2.5–2.6; maximum 3.0 everywhere |
| Curvature jumps at road links and inside roads | 79 % / 79 % / 94 % of lane links | none above 0.002 1/m, apart from logged fallbacks | 0 at every link and join; 0 paramPoly3 fallbacks |
| lsystem `city` + maps seeds that fail to build | 9 of 200 (4.5 %) | 0 of 200 | 0 of 200 (final code, `plans/baselines/2026-10-24-stage-a-after-sweep200.json`) |
| Validators and drive-check | pass | pass | pass; esmini also drives every lane end to end on our centre lines (5 cm) |
| Validators (topology, continuity incl. curvature and superelevation, drive-check) | pass | pass |
| Buildings or ground cutting into the road surface | n/a | 0 (checked by a test) |

## Phase overview

| Phase | Content | Review item | Effort |
|---|---|---|---|
| A0 | Geometry metrics and baseline | measuring all of A | 1 d |
| A1 | Neutral `roadgen/geometry/` package: design-speed model, primitives, shared junction space | 1, 2 | 2 d |
| A2 | Chains and alignment fitting | 1 | 3 d |
| A3 | Builder: chain roads, lane tapers, advisory speeds, sidecar v2 | 1 | 3 d |
| A4 | Downstream consumers and the `align` step (the caveat solution) | 1 | 2.5 d |
| A5 | Validators for the new geometry | 1 | 1.5 d |
| A6 | lsystem trim repair | 2 | 1.5 d |
| A7 | Curvature-continuous junction turns (clothoids) | 1 (G1 → G2) | 2 d |
| A8 | Banking and sloped junctions | 4 | 3 d |
| A9 | Curved roads from the generators | 3 | 3–4 d |
| A10 | Docs, examples, final measurement | all | 1 d |

About 4.5–5 weeks in total. Every phase ends with the full test suite green
(apart from the known `test_monitor_memory_cap_and_peak` failure), ruff
clean on touched files, and this file updated if anything changed. A1–A5
ship together as one usable step (merged bends); A6–A9 are each
independently useful after that.

Dependency order: A0 → A1 → A2 → A3 → A4 → A5, then A6, A7, A8 and A9 in any
order (A8's banking prefers A7 done first; A9 depends only on A2).

---

## A0 · Geometry metrics and baseline

**Why first:** turns the review's numbers into regression checks, so every
later phase is measured, and starts stage D's metrics command.

- `roadgen/metrics/__init__.py`, `roadgen/metrics/geometry.py`: works on an
  `.xodr` plus its `.roadmap.json` only (generator-agnostic), reusing
  `RoadEval` from `validate/continuity.py`.
  - per road and connecting road: peak curvature `κ_max` (sampled every
    0.5 m), posted speed, `a_lat = v²·κ_max`; distribution percentiles;
  - share of junctions with exactly two incoming roads;
  - curvature jump at every road link and at every geometry boundary
    inside a road;
  - counts: roads, junctions, connecting roads, total length.
- `roadgen/metrics/sweep.py`: build N lsystem map + seed pairs and report
  how many fail (the review's 1-in-20 measurement, made repeatable).
- CLI: `python -m cli metrics NET.xodr [--json OUT]` and `--sweep N` for
  the build-failure sweep. Recorded in run history like every command.
- Baseline: run on the three example networks, commit the JSON under
  `plans/baselines/2026-10-xx-stage-a-before.json` and quote it in this file.
- Tests: hand-built `.xodr` fixtures (a straight road, an arc of known
  radius, a line-to-arc join with a known jump) with exact expected values.

### A0 result (done 2026-10-23)

Implemented as planned: `roadgen/metrics/geometry.py`, `roadgen/metrics/sweep.py`,
`python -m cli metrics`, `metrics:` section in `defaults.yaml`,
`tests/test_metrics.py`, docs in `cli-reference.md` and `validation.md`.
`RoadEval` in `validate/continuity.py` gained cached element lookups,
`curvature()` (line, arc, spiral, paramPoly3) and `lane_curvature()`.

Baseline (`plans/baselines/2026-10-23-stage-a-before-*.json`; the example
graphs rebuilt with the current builder into `out/baseline/`). Lateral
acceleration uses each road's **peak** lane-centre curvature, so it is
higher than the review's figures, which used the average curvature per bend.

| Network | Two-road junctions | Bend a_lat median / p90 [m/s²] | Turn a_lat median / p90 [m/s²] | Share over 3 m/s² (bend / turn) | Lane links with a curvature jump |
|---|---|---|---|---|---|
| coast_city | 119 / 489 (24 %) | 12.7 / 35.2 | 11.4 / 48.5 | 77 % / 68 % | 6282 / 7974 |
| city_maps | 167 / 517 (32 %) | 12.6 / 31.7 | 11.2 / 47.8 | 75 % / 65 % | 5998 / 7588 |
| organic | 39 / 104 (38 %) | 12.5 / 32.2 | 10.9 / 47.2 | 63 % / 61 % | 1388 / 1480 |
| manhattan_like | 4 / 384 (1 %) | 14.6 / 33.7 | 0.0 / 45.0 | 100 % / 27 % | 1592 / 5832 |

Normal roads are all `<line>`, so they have zero curvature and no joins.
Turns at real junctions are as bad as bends because connecting roads carry
the lower speed limit of the two roads unchanged; A7's speed from
curvature addresses that.

**Build failures:** seeds 0–19 all built, but over 200 seeds 9 failed
(seeds 20, 49, 59, 60, 73, 94, 113, 149, 197), the review's rate. Inspecting
them (`plans/scripts/trim_failures.py`) showed that **8 of the 9 are a bug in
`junction_trims`, not short edges**: at a 3-way junction, two arms almost
straight across (178–184° apart) with different widths (2+2 and 1+1 lanes)
get a trim of `(b + a·cos θ)/sin θ ≈ (b − a)/sin θ`, 94–1136 m. That counts
a crossing of the two facing edge *lines* that lies behind the start of the
other arm, where the road surfaces never overlap. Only seed 60 is a
genuinely short edge (42 m between two junctions needing 21 m each).
Consequences for the plan: the trim formula is fixed in A1, and A6 only
has to handle genuinely short edges.

---

## A1 · Neutral geometry package

New package `roadgen/geometry/`, importable by generators, maps, scene and
the builder; it must never import `scenariogeneration` or
`roadgen.opendrive`.

1. **`design.py`: design-speed model**, parameters in `defaults.yaml`
   under a new `design:` section with a frozen `DesignParams` dataclass.
   - `f_max(v)`: AASHTO Green Book side friction, linear interpolation
     [km/h → f]: 16→0.38, 24→0.32, 32→0.27, 40→0.23, 48→0.20, 56→0.18,
     64→0.16, 72→0.15, 80→0.14, 89→0.13, 97→0.12, 105→0.11, 113→0.10,
     121→0.09, 129→0.08; clamped outside the table.
   - `e_max` by road class: street 0.04, avenue 0.04, highway 0.06.
   - Comfort cap: `f(v, e) = min(f_max(v), a_lat_max/g − e)` with
     `a_lat_max = 3.0 m/s²`, so slow streets (where the table allows about
     3.2 m/s² at 30 km/h) stay within the same limit the validator checks.
   - `min_radius(v, e)`: `v² / (g·(e + f(v, e)))`.
   - `advisory_speed(R, e)`: largest v with `v²/R ≤ g·(e + f(v, e))`
     (bisection, `f_max` depends on v), rounded **down** to a multiple of
     5 km/h like a real advisory sign.
   - `superelevation(R, v, e_max)`: AASHTO method-5 shape,
     `e = e_max·(2·R_min/R − (R_min/R)²)`, clamped to [0, e_max] (used from
     A8; returns 0 when banking is disabled).
   - `spiral_length(v, R, e, width)`: the longest of
     (a) lateral-jerk comfort (Shortt): `v³ / (C·R)` with `C = 0.6 m/s³`;
     (b) superelevation runoff: `w·e / Δ(v)`, `w` the rotated width,
         `Δ(v)` the AASHTO maximum relative gradient (metric table: 0.80 %
         at 20 km/h down to 0.50 % at 80 km/h and 0.35 % at 130 km/h);
     (c) `spiral_min_length` (default 10 m).
   - `taper_length(v, w)`: MUTCD metric merging taper,
     `L = w·S²/155` for S < 70 km/h, `L = w·S/1.6` above (S in km/h, w in
     m): 56 m for one 3.5 m lane at 50 km/h.
2. **`primitives.py`: geometry pieces** `Line`, `Arc`, `Spiral` (linear
   curvature change) with a common interface: `length`, `pose(s)`,
   `curvature(s)`, `sample(step)`, `end_pose()`. Spirals use
   `scipy.special.fresnel` for the standard clothoid and a rotation /
   scaling for general start curvature (closed form via shifted Fresnel
   arguments; Gauss–Legendre fallback when the curvature rate is tiny).
   An `Alignment` is an ordered list of pieces with cumulative stations.
3. **`clothoid.py`: wrapper around `pyclothoids`**
   - `g2_hermite(start_pose, k0, end_pose, k1)` →
     three `Spiral` pieces via `pyclothoids.SolveG2` (Bertolazzi & Frego's
     G2 interpolation with three clothoids; already verified in the dev
     container: a 90° turn gives spiral / arc / spiral with curvature 0 at
     both ends).
   - Quality checks on the result: total length ≤ 3 × chord, no
     self-intersection, `|κ| ≤ κ_limit`. Returns `None` on failure so the
     caller can fall back.
4. **`junction_space.py`**: move `Arm`, `junction_trims`,
   `MIN_ARM_ANGLE_DEG` from `opendrive/junctions.py`, and `Pose`,
   `wrap_angle`, `offset_point`, `hermite_poly3`, `poly3_length` from
   `opendrive/geometry.py` (re-export from the old modules for one release
   so imports keep working). These are already pure geometry.
   **Fix found in A0:** a neighbouring-arm constraint applies only when the
   facing edges cross *on both arms*, i.e. both distances
   `(b + a cos θ)/sin θ` and `(a + b cos θ)/sin θ` are positive; otherwise
   the surfaces meet only within the base trim. This removes the 94–1136 m
   trims at near-straight arms of different widths (8 of 9 sweep failures).
   Test: the arm configurations of the failing seeds give trims close to
   the base trim, and the 200-seed sweep drops to at most 1 failure.
5. `pyproject.toml`: add `scipy` and `pyclothoids` to `dependencies`.

Tests (`tests/test_geometry_design.py`, `tests/test_geometry_primitives.py`):
- `min_radius(50 km/h, 0.04)` ≈ 84 m, `min_radius(80 km/h, 0.06)` ≈ 252 m;
  advisory speed round trip; monotonic in R.
- Spiral end pose matches high-resolution numerical integration (< 1 mm);
  pose and heading continuity between consecutive pieces; curvature is
  linear along a spiral.
- `g2_hermite` reproduces the end pose and curvatures within 1e-6 and
  rejects a loop-forming configuration.
- `junction_trims` tests move along unchanged.

### A1 result (done 2026-10-23)

Implemented: `roadgen/geometry/{design,primitives,clothoid,junction_space,planar}.py`,
`design:` section in `defaults.yaml` (`DesignParams`), `scipy` and
`pyclothoids` in `pyproject.toml`, `tests/test_geometry.py` (design rules,
spiral end poses against `scipy.integrate.quad` to 1 µm, alignment
continuity, G2 solver checks), three regression tests for the trim fix in
`tests/test_junctions.py`, `docs/geometry.md`.

Deviations from the plan above:
- **No re-export shims.** `opendrive/geometry.py` was moved to
  `geometry/planar.py` and `Arm` / `junction_trims` to
  `geometry/junction_space.py`, and every import (builder, junctions,
  tests) was updated directly; shims would only have added code to delete
  later in an internal project.
- **Line, arc and spiral are one `Piece` class** with linear curvature
  (`kind` tells them apart), instead of three classes; the evaluation is
  shared and the builder maps `kind` to the OpenDRIVE element.
- `side_friction` applies the 3 m/s² comfort cap (added during planning).
- The relative-gradient table uses the metric AASHTO values (see above).

**Trim fix result:** the 200-seed sweep went from 9 failures to 1
(seed 60, the genuinely short edge); report in
`out/baseline/sweep200_trimfix.json`. This changes junction size, and so
the `.xodr` output, wherever a junction has near-straight arms of
different widths: those junctions get smaller, since the old trims were
spurious. Every other network is unchanged.

---

## A2 · Chains and alignment fitting

1. **`geometry/chains.py`**
   - A node is **mergeable** when its degree is 2, `kind != "junction"`,
     the deflection is below `design.max_merge_deflection` (default 135°;
     sharper is a hairpin better kept as a junction), and the travel
     directions are compatible (not two one-way edges that both point into
     or both point out of the node).
   - `find_chains(graph)` → list of `Chain(nodes, edges, closed)`: maximal
     paths through mergeable nodes, ending at junctions, dead ends or
     non-mergeable nodes. Closed rings of mergeable nodes are allowed.
   - Orientation: the chain's reference direction follows its first
     one-way edge if there is one (so all lanes of one-way stretches sit on
     the right, as `oriented_lanes` does today), otherwise from the lower
     end-node id. Each edge records whether it runs with or against the
     chain, so forward and backward lanes are swapped consistently.
   - Deterministic order: sorted by smallest edge id.
2. **`geometry/alignment.py`**, `align_chain(graph, chain, trims,
   params) -> ChainAlignment` and `network_alignment(graph, params) ->
   NetworkAlignment` (cached per graph object).
   - Start from the polyline through the chain's nodes, trimmed at real
     junctions by `junction_trims` (arms leaving a junction stay straight,
     so the junction trim geometry is unchanged).
   - At each interior node with deflection Δ, fit a **symmetric
     spiral–arc–spiral** corner: target radius
     `R* = min(min_radius(v_design, e_max), Node.radius_cap)`, where
     `v_design` is the larger speed limit of the two edges. Spiral length
     from `spiral_length`; when the two spirals would cover the whole
     deflection (`L_s/R ≥ Δ`), use spiral–spiral with no arc. Tangent
     length `T(R)` and corner offset `E(R)` come from exact Fresnel values,
     not the small-angle series.
   - **Room allocation:** for each straight segment, the corners at its two
     ends must satisfy `T_i + T_{i+1} ≤ L_segment − gap` (and at chain ends
     `T ≤ L_edge − trim`). Iterate: where a segment is over-subscribed,
     split it in proportion to the desired tangents, solve each corner's
     largest R that fits (bisection on the monotone `T(R)`), and keep the
     smallest R per corner. The radii only decrease, so it converges; it
     is deterministic.
   - **Clearance to other roads:** query a shapely `STRtree` of the other
     chains' straight edges; if a corner curve, buffered by its half-width
     plus `design.road_clearance` (default 1.0 m), touches another road's
     buffer, reduce that corner's R by bisection until clear. Straight
     edges of a planar graph never cross, and cutting a corner only moves
     the road towards the inside, so this terminates.
   - Optional `design.max_corner_offset` [m] (default 0 = off) to keep
     curves close to the generator's layout.
   - Per corner record: node id, Δ, R, spiral length, design speed,
     **advisory speed** (`advisory_speed(R, e_built)` if lower than the
     limit, else none).
   - **Stations:** each graph node gets a station `s` on its chain road (at
     an interior node: the curve's midpoint; at an end: the trimmed end).
     Each edge maps to `[s_from, s_to]`; `edge_station(edge, d)` maps a
     distance along the straight edge to a station by projection onto the
     fitted curve (used for profiles and bridges until A4 resamples
     along the curve).
   - **Closed rings** are split into two roads at the ring's lowest-id node
     and the node about halfway round, linked road-to-road in A3.
   - Sampled polyline per road (`sample(step=1.0)`) for scene, plot and
     metrics.

Tests (`tests/test_alignment.py`):
- L-shaped chain with long legs: R equals the design radius, curvature is
  continuous, endpoints match the trimmed straight ends.
- Short legs: R shrinks to fit, advisory speed is set and below the limit.
- Two corners sharing one short segment: tangents fit; deterministic for a
  shuffled edge order.
- Corner next to a parallel road: R reduced until clearance holds.
- One-way chain stored in mixed edge directions; closed ring; a node with
  `kind="junction"` and a 160° hairpin stay unmerged.
- Property test over random polylines: pose continuity < 1 mm, heading
  < 1e-6 rad, no curvature jump at piece joins above 1e-6.

### A2 result (done 2026-10-24)

Implemented: `roadgen/geometry/chains.py`, `roadgen/geometry/alignment.py`,
`node_trims` / `oriented_lanes` moved to `geometry/junction_space.py`, new
`design:` keys `max_merge_deflection_deg`, `road_clearance`,
`max_corner_offset`, `tests/test_alignment.py` (24 tests incl. random
polylines), helper `plans/scripts/alignment_summary.py`.

Deviations from the plan above, and why:
- **Spiral length follows AASHTO**, not the plan's `C = 0.6 m/s³` with a
  10 m minimum: `C = 1.2 m/s³` and the length is bounded by the lateral
  shift it causes, `√(24·p·R)` with p between 0.2 and 1.0 m (`design:
  jerk_rate`, `spiral_min_shift`, `spiral_max_shift`). The first version's
  53 m spirals into an 84 m curve used so much room that only 16 % of
  corners kept their design shape.
- **The design radius uses the banking actually built** (e = 0 until A8),
  not `e_max`; otherwise every design-radius curve on a flat road would get
  an advisory speed. When A8 switches banking on, radii are refitted.
- **Corners shrink by uniform scaling** (radius and spirals together, λ),
  which keeps the shape and makes tangent length linear in λ, instead of
  solving a radius for each tangent budget with recomputed spirals (whose
  tangent length is not monotone in the radius).
- **Speeds use the innermost lane centre**: the design radius is
  `min_radius(v) + offset to the farthest lane centre`, and advisories use
  the lane radius. Without it, outer-edge lanes reached 3.5 m/s² at the
  posted speed.
- **Clearance compares against the straight polyline**: the curve may not
  touch any road the straight corner did not already touch, so contact
  caused by the generator itself does not shrink corners.
- **Rings and loops are split by keeping a node a junction**, not by
  road-to-road links: a chain may not start and end at the same node. esmini
  mixed up the two ends of a road joining one junction twice (drive-check
  jumps of 8–14 m), so the middle node of such a chain stays a two-road
  junction. Rare (one case in the organic example).
- **Two-way chains run along their lowest-id edge** (not from the lower node
  id), so single-edge roads keep their old direction and lane ids.

**Correction of the discussion's estimate.** The discussion said about half
of the bends fit the design radius. With the design speed taken as the faster
edge's limit (the estimate used the slower), flat roads and spirals, on
`coast_city` 112 corners are fitted and **88 get advisory speeds, median
70 % of the design speed** (35 km/h on a 50 km/h road). By deflection: 0 of
15 corners under 5° and 2 of 7 at 5–15° need one, but all 73 corners of
60–135° do: a 90° bend at 50 km/h needs about 114 m of straight road on each
side, more than lsystem blocks have. Room, not clearance, limits them (the
clearance check shrank 1 of 112). These advisories are realistic (real 90°
bends carry curve warnings around that speed); the structural fix is A9.

---

## A3 · Builder: chain roads

`roadgen/opendrive/builder.py` rewritten around `NetworkAlignment`;
`junctions.py` keeps junction logic only.

1. **Roads:** one `<road>` per chain (per half for split rings). Plan view
   from the alignment pieces: `xodr.Line`, `xodr.Arc`,
   `xodr.Spiral(curvstart, curvend, length=...)`, placed with
   `add_fixed_geometry` at absolute poses (scenariogeneration's automatic
   adjustment stays bypassed).
2. **Junctions:** only at unmerged nodes of degree ≥ 2 (real junctions plus
   the unmergeable two-road nodes). Junction trims and `RoadEnd` poses are
   unchanged because chain ends are straight.
3. **Lane sections and tapers** in a new `opendrive/lanes.py` function
   `make_lane_sections(profile)`:
   - lane counts per edge, per direction, along the chain;
   - at a node where a direction gains lanes, the extra **outermost** lane
     grows from width 0 to full width after the node; where it loses lanes,
     the outermost lane narrows to 0 before the node. Width polynomial is a
     cubic smoothstep `w(t) = W·(3t² − 2t³)` over `taper_length(v, W)`, so
     the lane edge is tangent-continuous; the taper is shortened (logged)
     when the edge is too short;
   - lane sections at every lane-count change, with `<link>` predecessor /
     successor between sections for every continuing lane (ids renumber
     from the centre outward, so links are explicit);
   - road marks: broken between lanes, solid outermost; the mark of the
     lane being dropped switches to solid over the taper (no lane change
     into a closing lane).
4. **Speeds:** `<type s=…>` records at every speed-limit change along the
   chain and an advisory-speed record over each corner that needs one
   (from the start of its entry spiral to the end of its exit spiral, plus
   a deceleration lead of `v²/(2·1.5 m/s²)` before it, capped at the
   previous corner). Advisory speeds also go into the sidecar.
5. **Elevation:** `road_elevation` gets the chain's height samples (node
   heights at their stations, edge profiles mapped with `edge_station`).
   Flat ends only at real junctions; interior nodes are ordinary PCHIP
   knots, so the flat step at every bend disappears.
6. **Bridges:** each edge's spans mapped to stations and merged when they
   touch.
7. **Road-to-road links** (split rings, and chains meeting at a
   non-junction end) via `add_successor(xodr.ElementType.road, …)`.
8. **Ids:** chain roads 1..C in chain order (smallest edge id), then
   junctions and their connecting roads as today.
9. **Sidecar v2** (`.roadmap.json`), backwards-compatible keys kept:
   `edge_to_road` (every edge, values may repeat), new
   `edge_s_range` `{edge: [road, s0, s1]}`, `corners`
   `[{road, node, s0, s1, radius, design_speed, advisory_speed}]`,
   `lane_sections` per road, `geometry_version: 2`.

Tests: rewrite the affected assertions in `test_opendrive.py` and
`test_junctions.py`; new cases for a lane-drop chain (taper widths, lane
links, marks), a speed change along a chain, advisory speed records, a
split ring, a chain with bridges and heights. esmini smoke test on one
generated network (`odrviewer --headless` loads, `drive-check` passes).

### A3 result (done 2026-10-24)

Implemented: builder rewritten around `network_alignment`
(`opendrive/builder.py`), `roadgen/geometry/profiles.py` (lane layout with
tapers, speed profile, height samples, bridge spans),
`opendrive/lanes.make_lane_sections`, sidecar keys `geometry_version`,
`edge_s_range`, `corners`, `tests/test_chain_roads.py`. The CLI passes the
`design:` config to the builder.

Work moved forward from later phases because A3 needed it:
- **Continuity check reads `<spiral>`** (from A5), using
  `geometry.primitives`.
- **Scenario lane slots are section-aware** (from A4): spawns only on
  full-width stretches that run on to the road end in the travel direction;
  with `explicit_routes` only on lanes that run the whole road, and the route
  graph only uses those (esmini's route planner logged "Lane id -2 not
  available in lane section" for a route starting on a partial lane).
  Waypoints sit where the lane exists at entry. Verified first that esmini
  merges a car whose lane ends ("No valid connecting lane ... looking for
  closest valid lane" warning) rather than failing.
- **Topology check disambiguates roads joining one junction at both ends**,
  using the connecting road's link back to the incoming road.

Deviations: the plan's "dropped lane's mark switches to solid over the
taper" was left out, since traffic must be able to merge out of a closing
lane; marks stay broken. Lane-width changes on continuing lanes get a
smoothstep transition after the node.

Results on the example networks (`out/a3/`, rebuilt from their graphs):

| Network | Roads (before) | Junctions (before) | Two-road junctions | Road a_lat p90 / max [m/s²] | Curvature jumps inside roads | drive-check |
|---|---|---|---|---|---|---|
| coast_city | 647 (766) | 370 (489) | 0 | 1.6 / 2.8 | 0 of 363 joins | 3585 / 3585 |
| city_maps | 615 (780) | 352 (517) | 2 (loops split) | 1.8 / 2.8 | 0 of 517 | 3250 / 3250 |
| organic | 122 (160) | 66 (104) | 1 (loop split) | 1.9 / 2.5 | 0 of 120 | 619 / 619 |
| coast_city_draped | 647 | 370 | 0 | – | – | 3585 / 3585 |

Junction turns are unchanged (A7). The 200-seed sweep still fails only
seed 60 (A6). Build time on `coast_city` is 0.8 s (alignment 0.3 s).
Full test suite: 426 passed (the known benchmark-memory test deselected).

**Known gap until A4:** `scene/ground.py`, `scene/buildings.py` and
`maps/drape.py` still use straight edges and flat bend nodes. With
`--ground` on hilly maps the ground is levelled along the straight edges, and
buildings are cut out along them, so at fitted corners the ground may poke
through the road and buildings may sit on the corner cut.

---

## A4 · Downstream consumers and the `align` step

This is the solution to the Option 1 caveat (see discussion file).

1. **`Node.radius_cap: Optional[float] = None`** in `graph/model.py`,
   added to `io._OPTIONAL` so unset caps are not written (JSON of graphs
   without caps stays identical); documented in `docs/road-graph.md`.
2. **`roadgen/maps/align.py`** + CLI `python -m cli align NET.json
   --map-file …` → `NET.aligned.json`; `pipeline` runs it before `drape`
   when maps exist. For each fitted corner it samples the curve against:
   - the terrain map: no water where neither straight edge had water (a
     curve may not create a new bridge);
   - the elevation map: grade along the curve within lsystem's
     `max_grade`, cross slope under the curve within `max_slope_deg`.
   A failing corner gets the largest radius that passes (bisection on the
   cap), written to `Node.radius_cap`. Deterministic, input graph
   unchanged, map-independent output when there are no maps.
3. **`maps/drape.py`** samples heights and finds water **along the
   alignment** (`network_alignment(...).sample`) instead of along the
   straight edge. `Edge.profile` keeps its meaning of evenly spaced
   samples, now along the edge's aligned station range; this is identical
   for straight edges.
4. **`scene/buildings.py`**: block faces are polygonized from the sampled
   alignment polylines; footprints subtract the alignment buffered by the
   half-width plus setback. **`scene/ground.py`** levels along the
   alignment (station and lateral offset from the sampled polyline,
   heights from the chain road's elevation records), and junction areas as
   today.
5. **`graph/plot.py`**: draws fitted curves (option `--straight` keeps the
   raw edges for debugging).
6. **`scenario/xosc_builder.py`**: lane slots per lane section (a dropped
   lane only spawns where it exists, at least `END_MARGIN` before its
   taper); lane graph nodes keep `(road, lane)` at road ends and add
   section-to-section lane links so routes follow merges.
7. **Run history:** `geometry_version` stored with each run; replaying a run
   recorded with an older version logs a warning.

Tests: buildings and ground never intersect the road surface for a curved
chain (shapely check against the alignment buffer); drape along a curve
over a synthetic elevation map; `align` caps a corner that would enter a
synthetic lake and leaves other corners alone; scenario spawn never on a
non-existent lane section; graph JSON byte-identical without maps.

### A4 result (done 2026-10-24, commit `607b911`)

Implemented as planned: `Node.radius_cap`, `roadgen/maps/align.py` + CLI
`align` (run by `pipeline` when it has maps), drape sampling under the
fitted road, buildings and ground along the fitted roads, plots of fitted
roads, `geometry_version` in run history with a replay warning.

Additions and deviations:
- **Bend node heights come from the corner midpoint.** On the fitted road a
  bend node sits at its corner's midpoint, which on a ramp was 1.8 m higher
  than the vertex; drape now samples there.
- `align` checks water and grade only (not cross slope), samples curves
  every 1 m or less and rounds caps down, so a cap never lets a curve back
  into water. Capping one corner can give a neighbour more room, so it
  repeats until nothing changes (at most 5 passes).
- The elevation code moved to `geometry/vertical.py` and gained end slopes,
  junction planes and per-chain fitting, ready for A8.
- New helpers `Alignment.points`, `NetworkAlignment.edge_points` /
  `edge_line`, used by drape, scene and plot.

---

## A5 · Validators for the new geometry

1. `validate/continuity.py`:
   - evaluate `<spiral>` (shared code from `geometry/primitives.py`; the
     validator stays independent of the builder by parsing the XML);
   - check lane links **between lane sections** inside a road, not only
     across road links;
   - **curvature continuity** at road links and geometry joins (lane
     centre curvature, tolerance 0.002 1/m), reported as warnings for
     paramPoly3 fallbacks and errors otherwise; becomes all-errors after A7;
   - lane-centre height including superelevation (from A8 on:
     `z_lane = z_ref + t·tan(roll)`).
2. New `validate/dynamics.py`: `a_lat` per road at posted / advisory speed
   ≤ `design.a_lat_max` (default 3.0 m/s²); a road above it is an error.
3. `sim/drive_check.py`: besides junction movements, drive every lane of
   every chain road end to end through its lane sections in esmini and
   compare the travelled positions with the expected lane centres.
4. CLI `validate` gains the new checks; `docs/validation.md` updated.

Tests: fixtures with a deliberate curvature jump, a missing section link
and an over-fast curve each produce exactly one error.

### A5 result (done 2026-10-24, commit `6995aa4`)

Implemented: section-boundary checks in `check_continuity`,
`check_curvature` (errors; paramPoly3 jumps are warnings),
`validate/dynamics.py` (on connecting roads too from A7), and
`drive_lanes`: esmini drives every whole lane of every road and its
position is compared with our evaluation (5 cm).
- esmini moves along the lane centre, not the reference line, so lane
  drives stop by station.
- The dynamics check first sampled every lane every 0.5 m (validation
  1.1 → 6.3 s on `coast_city`); peaks are now evaluated at piece ends
  (curvature is linear on lines, arcs and spirals) and only paramPoly3 is
  sampled: 1.8 s, same results.
- Result: esmini and roadgen agree within 5 cm on 2172 / 2172 lanes of
  `coast_city` and 2194 / 2194 of a maps city.

---

## A6 · lsystem trim repair (review item 2)

After A2–A3, only real junctions trim, and after the A1 trim fix the only
known failure left is a genuinely short edge between two real junctions
(sweep seed 60), so the remaining case is narrow: an edge whose two
**real-junction** trims add up to more than its length.

- Rejecting short segments during growth would change lsystem output even
  for networks that build fine today, breaking the "identical without maps"
  rule. Instead, add a **deterministic post-growth repair pass**
  (`generators/repair.py`, usable by any generator) that only touches
  edges that would fail to build, so every buildable network stays
  byte-identical:
  1. compute trims with `geometry.junction_space` (the same code the
     builder uses: no OpenDRIVE dependency);
  2. for each failing edge, in edge-id order: **collapse** it (merge its
     two nodes at the midpoint) if every resulting arm pair keeps at least
     `min_angle_deg` and no edge gets shorter than its own trims; otherwise
     **remove** it if the graph stays connected; otherwise extend the trims
     check to report a clear error naming the node and edge;
  3. repeat until no edge fails (each step removes an edge, so it ends).
- Explicit vertex-degree rule (Teng & Bidarra §3.3): collapse never creates
  a node of degree above `max_degree` (default 5).
- Called at the end of `lsystem.generate` and `manhattan.generate`.

Tests: sweep seed 60 builds; a
synthetic short edge between two junctions is collapsed; networks without
failures are unchanged (hash comparison on the examples); the A0 sweep
reports 0 of 200 failures.

### A6 result (done 2026-10-24, commit `9620a30`)

Implemented as planned (`generators/repair.py`, run last by lsystem and
manhattan). Edge removal requires its two nodes to stay connected (not the
whole network, which may have several components). The 200-seed sweep:
**0 of 200** networks fail (9 before A1, 1 after the trim fix).

---

## A7 · Curvature-continuous junction turns

Muktadir et al. (Stage 3) build connecting lanes as parametric cubics, as
roadgen does today; a cubic cannot reach zero curvature at both ends while
matching both poses, so the jump at junction entries stays. Clothoids fix
it:

1. In `junctions._connecting_road`, build the lane-centre path with
   `geometry.clothoid.g2_hermite(start, k0, end, k1)`. `k0`, `k1` are the
   lane-centre curvatures at the contact points (0 for straight chain ends,
   which A2 guarantees). Emit the three pieces as `<spiral>` geometries.
2. Fallback to today's `hermite_poly3` when the solver fails its checks
   (very short S-shifts, near U-turns); each fallback is logged and counted
   by the metrics.
3. **Turning speed from curvature:** connecting-road speed
   `min(limit_in, limit_out, advisory_speed(1/κ_max, e_junction))` instead
   of the plain minimum of the limits, so real turns get realistic speeds
   (about 20–25 km/h for an urban right turn).
4. Lane width taper on connecting roads stays linear in s (it is
   independent of the reference-line shape).

Tests: every movement of a 4-way and a skewed 3-way junction has curvature
0 at both ends and no jump between pieces; position/heading continuity
unchanged; fallback path covered; drive-check passes; esmini loads all
example networks.

### A7 result (done 2026-10-24, commit `d9029ef`)

Implemented as planned. On the example cities no turn needed the paramPoly3
fallback; each movement records its `path`. Turn lateral acceleration p90
fell from 47–48 to 2.5–2.6 m/s², curvature jumps at lane links from
79–94 % of links to 0. Build time on `coast_city` 0.8 → 2.2 s (3585
clothoid fits).

---

## A8 · Banking and sloped junctions (review item 4)

`xodr.Road.add_superelevation` exists in scenariogeneration, and the
esmini libraries in the container parse `lateralProfile`/`superelevation`
(found in `libesminiRMLib.so`). Whether esmini's vehicles and OSI output
honour the roll is **checked first** (half a day spike with odrviewer and
an OSI dump); if not, banking is still written for other consumers (CARLA,
RoadRunner) and the advisory speeds keep using e = 0.

1. **Banking on chain roads:** on each arc, `e = superelevation(R, v,
   e_max)`, rolled towards the curve centre; along the spirals `e` changes
   linearly with curvature (the runoff, which `spiral_length` already sized
   for the relative-gradient limit); 0 on tangents. Written as
   `<superelevation>` cubic records (linear per spiral, constant per arc).
   Advisory speeds are recomputed with the built `e`. Parameter
   `design.banking: true`.
2. **Sloped junctions:** replace the flat junction area by a **junction
   plane** `z = z0 + g·(p − p0)`:
   - fitted by least squares to the node height and the approach roads'
     heights at the trim distance, with `|g|` capped at
     `design.junction_max_grade` (default 0.04, AASHTO's usual intersection
     limit);
   - each approach road ends at the plane height, with grade
     `g·heading` and cross slope `g·normal` at its contact, ramped from its
     own profile and banking over the last segment (PCHIP end slopes
     become the plane grade instead of 0);
   - connecting roads get elevation and superelevation sampled from the
     plane along their clothoid path, fitted with cubic records per piece
     (max error checked < 5 mm);
   - `scene/ground.py` levels junction areas to the plane.
3. Continuity validator compares lane-centre heights including roll (A5
   hook) and superelevation at road links.

Tests: banked arc has the expected roll and runoff length; lane-centre
heights continuous at every link on a sloped synthetic map; flat graph
stays flat (no `<lateralProfile>` written when nothing is banked);
drive-check on a hilly example.

### A8 result (done 2026-10-24, commit `8ad379d`)

The spike came first: esmini (3.9) honours `<superelevation>`; a lane
centre `t` across the surface lies at `t·sin(roll)` height and is measured
across the rolled surface. Banking is **on by default**, junction planes
capped at 4 %.

Deviations and findings:
- **Smoothstep runoff instead of AASHTO's linear one**, along each spiral:
  a linear runoff ending where a curve meets a junction left the outer
  lane's grade discontinuous (8.75 m × roll rate) — the continuity check
  caught it.
- **End ramps to the junction cross slope overlap corner banking** (they
  add up) instead of being squeezed in before the first corner, which gave
  a 1 mm ramp with a huge roll rate.
- **Banking uses the innermost lane radius**, like the speeds, so a design
  corner gets exactly `e_max` (otherwise it fell just short and got a
  50 km/h advisory on a 50 km/h road).
- **Lane positions are evaluated on the pitched and rolled surface**: esmini
  rolls the lateral axis about the pitched tangent, so on a 16 % grade with
  0.06 rad of roll a lane centre 5.25 m out moves 5 cm along the road. The
  validator and drive-check use the same vector, `(−sin r sin p, cos r,
  sin r cos p)`; after that all lanes agree within 5 cm.

---

## A9 · Curved roads from the generators (review item 3)

Since the builder now turns any polyline into a smooth road, a generator
makes curved roads simply by emitting two-road "shape" nodes along a curve.
Full tensor fields (Chen et al. §5–6) belong with stage C's style mixing,
so here:

1. **lsystem `curve_mode`** parameter: `none` (default, output unchanged),
   `drift`, `contour`.
   - A street proposal advances in sub-steps of `curve_step` (default
     20 m) instead of one straight segment; the heading changes per
     sub-step by at most `curve_step / R_min(v_class)` (so the polyline is
     drivable by construction) following a smooth random process
     (Ornstein–Uhlenbeck on heading, seeded from the generator's RNG).
   - `drift` sets the process strength from a **tortuosity target** per
     road class (length ÷ chord, Aliaga et al. §3.1.1): street 1.03,
     avenue 1.01, highway 1.005 by default; presets for organic towns use
     larger values.
   - `contour` (Parish & Müller's San Francisco rule, Chen's height-field
     basis): with an elevation map, the heading blends the goal direction
     with the contour direction (perpendicular to the gradient), weighted
     by local slope, so roads on hills follow the terrain.
   - Snap, crossing and legality checks run on every sub-segment;
     `min_segment_length` and `min_angle_deg` apply between real
     junction candidates, not between shape nodes; shape nodes are never
     snap targets.
2. **Highways:** their existing fan turns are limited per step by the
   highway design radius, so fitted curves reach design speed.
3. Presets: `organic_curved` (drift) and `hill_town` (contour).
4. manhattan stays a grid (its `ring` variant could follow later).

Tests: `curve_mode=none` gives byte-identical graphs for all presets
(project rule); `drift` reaches the tortuosity target within ±20 % on
average; every vertex deflection fits the design radius; `contour` roads on
a synthetic cone are closer to contour-parallel than `none`; all generated
networks build and validate.

### A9 result (done 2026-10-24, commit `6778161`)

Implemented `curve_mode` `drift` and `contour` with presets `organic_curved`
and `hill_town`; `none` draws no extra random numbers (output unchanged).

Deviations:
- **No sub-step growth.** Instead of growing each street in 20 m sub-steps
  (which would need snapping, crossing and min-length rules per
  sub-segment), the deviation from the grid axis evolves per segment, and a
  post-pass gives every street a **bow node** for the tortuosity target
  where the bow is legal. The builder then fits the curves.
- The tortuosity target is a direct parameter (`curve_tortuosity`) rather
  than tests on achieved tortuosity within ±20 %; the tests check bows
  exist, turns stay within the design limit (snapping can add a little) and
  the networks validate and drive.
- **Added `design.min_lane_radius` (10 m).** `hill_town` produced bends
  whose inner lane centre turned at about 5 m; esmini's lane geometry
  degenerates there. Such bends now stay two-road junctions (each lane gets
  its own lane-centre turn) and the network is refitted.
- **drive-check refinements**: approaches start, and drives end, on
  full-width lane stretches (a short edge with a lane add started the
  approach before the lane existed; a drive on into a dropped lane made
  esmini merge, which looked like a jump).

---

## A10 · Documentation, examples, final measurement

- Docs: `opendrive.md` (chains, lane sections, advisory speeds, banking,
  junction planes, id scheme, sidecar v2), `road-graph.md`
  (`radius_cap`, shape nodes), `maps.md` (`align` step; banking moved from
  "next steps" to done), `generators.md` (repair pass, `curve_mode`),
  `validation.md`, `architecture.md` (the neutral `geometry/` package),
  `cli-reference.md` (`metrics`, `align`), README.
- Regenerate `out/examples/` and the replayable examples in `runs/`.
- Run A0 metrics, write `plans/baselines/…-stage-a-after.json`, fill in the
  "after" column of the exit-criteria table in this file, set Status to
  done.

### A10 result (done 2026-10-24, commit `58d6d16`)

Docs updated phase by phase (`docs/geometry.md` new; `opendrive.md`,
`maps.md`, `validation.md`, `generators.md`, `road-graph.md`,
`scenarios-and-simulation.md`, `cli-reference.md`, `architecture.md`,
`development.md`, README). `runs/examples.sh` gained phase 8 (recorded in
`runs/history.json`, `runs/README.md` regenerated); phases 1–7 keep their
version 1 runs, which replay with a warning.

Final measurement, the example graphs rebuilt with the final code
(`plans/baselines/2026-10-24-stage-a-after-metrics.json`):

| Network | Roads (before) | Junctions (before) | Two-road junctions | Road a_lat p90 / max | Turn a_lat p90 / max | Curvature jumps | drive-check movements / lanes |
|---|---|---|---|---|---|---|---|
| coast_city | 647 (766) | 370 (489) | 0 | 2.0 / 2.9 | 2.6 / 3.0 | 0 | 3585 / 2172, all pass |
| city_maps | 615 (780) | 352 (517) | 2 | 2.2 / 3.0 | 2.5 / 3.0 | 0 | 3250 / 1956, all pass |
| organic | 122 (160) | 66 (104) | 1 | 2.2 / 2.8 | 2.6 / 3.0 | 0 | 619 / 342, all pass |
| manhattan_like | 720 (724) | 380 (384) | 0 | 0.0 / 2.6 | 2.4 / 2.8 | 0 | 2900 / 2220, all pass |

Bends with advisory speeds (banked): coast_city 87 of 112 (median 70 % of
the design speed), city_maps 116 of 161 (70 %), organic 25 of 38 (80 %),
almost all 60–135° grid corners (see the A2 correction). Junction turns: 3063
clothoids + 522 lines on coast_city, no paramPoly3 fallback anywhere.
Curved presets: `organic_curved` (496 movements / 286 lanes) and `hill_town`
on maps (3091 / 1826) pass every validator and drive-check.

Test suite: 461 tests, all passing except the known
`test_monitor_memory_cap_and_peak`. ruff findings are unchanged apart from
the project's `Optional[...]` style. Build time on `coast_city` 0.8 s → 2.2 s
(clothoid turns); validation 1.1 s → about 2–4 s.

**Open points for later stages:** the advisory share on grid corners only
falls with curved generation (A9 presets) or longer blocks; tensor-field
layouts (Chen et al.) belong to stage C; `align` does not yet check the cross
slope under a curve; esmini warns (and merges the car) when a car in a
dropped lane reaches its end, since its default controller never changes
lanes.

## Risks and how they are handled

| Risk | Handling |
|---|---|
| `SolveG2` produces loops or long detours on odd junction geometry | Quality checks + paramPoly3 fallback, counted by metrics (A7) |
| Room allocation leaves many sharp corners in dense grids | Advisory speeds keep them honest; A9 removes the cause |
| esmini ignores superelevation | Spike first in A8; banking still written, advisory speeds use e = 0 |
| Corner cuts clash with buildings, water or other roads | Shared alignment (A2, A4), clearance check (A2), map caps (A4) |
| Changed outputs break comparisons with old benchmark runs | `geometry_version` in sidecar and history |
| Lane-section renumbering confuses the scenario builder | Section-aware lane slots and links (A4), drive-check over chains (A5) |
| Performance on large networks (STRtree queries, Fresnel calls) | Vectorised sampling, one STRtree per network, benchmark sweep before and after A3 |

## Paper references used

- Cura et al., StreetGen §2.4–2.5, §3.1: corner radius from road type and
  speed, shrinking the radius when the axis is too short, variable-width
  transitions for lane-count changes.
- Muktadir et al., JunctionArt Stages 3–4: incident points moved outwards
  until lanes fit, parametric-cubic connection lanes (the limit A7
  removes), lane split/merge repair along roads.
- Parish & Müller §3.3: local constraints (prune, rotate, snap), and
  subdivision smoothing of high-curvature roads.
- Chen et al. §6.1: streamline tracing with step control (basis for the
  sub-step growth in A9); tensor fields deferred to stage C.
- Aliaga et al. §3.1.1: tortuosity (length ÷ chord) per street as a
  generation target.
- Song & Whitehead, TownSim §4.5: curvature distribution as an evaluation
  metric (A0).
- Teng & Bidarra §3.3: explicit vertex-degree and angle rules (A6).

Outside the papers: AASHTO Green Book (side friction, superelevation
distribution method 5, relative gradients, intersection grades), MUTCD
taper lengths, Shortt's lateral-jerk spiral length, Bertolazzi & Frego G2
clothoid interpolation (`pyclothoids`), ASAM OpenDRIVE 1.6 spiral,
lane-section, `lateralProfile` and road-link semantics.
