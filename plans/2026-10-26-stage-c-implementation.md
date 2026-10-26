# Stage C (long-tail diversity): implementation plan

Status: **C0–C4 done** (2026-10-26), C5–C10 planned; results and deviations
under each phase. Discussion, options and decisions:
[2026-10-26-stage-c-discussion.md](2026-10-26-stage-c-discussion.md).

## Prompts

The prompt and the answers that led to this plan are quoted in the
[discussion](2026-10-26-stage-c-discussion.md#prompts), with the prompt that
started the implementation:

**2026-10-26, prompt 2**

> Implement C0-C4 at once. While removing manhattan generator, you can remove related references and artifacts to it.

**2026-10-26, prompt 3**

> You can commit this and continue with the rest of the phases. Also, add some view commands for generated examples

## Context

Stage A of `Roadgen AV Readiness Review.pdf` (geometry accuracy) is done
(`plans/2026-10-23-*`, `2026-10-24-*`). Stage C of the review is **long-tail
diversity**: today every city is a grid (straight, drifting or contour-following),
junctions are almost all 3–4-leg near-90° crossings, and the road types that
make AV driving hard (roundabouts, merges, lane drops, overpasses, tunnels,
U-turns, cul-de-sacs) do not exist. Review items:

1. **Junction variety:** 3–7 legs with skew and offset (Y, K, five-way), lane
   splits (Muktadir et al. / JunctionArt, file10).
2. **Missing road types:** roundabouts; on/off ramps, merges, lane drops/adds;
   overpasses (impossible with today's planar graph); tunnels; U-turns;
   cul-de-sacs (Yang file4, Teng & Bidarra file9).
3. **Style mixing inside one map:** grid, radial and organic blended by control
   maps; Paris radial and San Francisco contour roads; rotation fields for skewed
   junctions; one grid-to-organic knob; a connectivity knob from grid to
   cul-de-sac suburbs (Parish & Müller file1 §3.2.2, Chen file2 §5.3, TownSim
   file8 §3.3, Lechner file6 §3).

The user also asked to **remove the `manhattan` generator** and make every city
pattern, including Manhattan grids, with the lsystem generator, as the papers do.

## Shared conventions for every phase

- Generators and transforms output only a RoadGraph. The builder learns only
  the new node kinds (`merge`, `diverge`), `Edge.tunnels`, lane splits and U-turns.
- Model changes:
  - `NodeKind` gains `merge` and `diverge`;
  - `Edge` gains `tunnels: list[tuple[float, float]]`, like `bridges`;
  - new `graph.metadata` keys: `roundabouts`, `culdesacs`, `overpasses`, `interchanges`, `style`.
  - All new fields are optional and left out when unset (`roadgen/graph/io.py::_OPTIONAL`), so old JSON stays identical.
- Each new parameter goes in `roadgen/defaults.yaml` and in its params
  dataclass (`roadgen/config.py`; the config test checks they match).
  Frozen params with presets; `Optional[...]`; units in brackets; `log` via `logging`.
- Each phase ends with:
  - the full suite green (`docker compose run --rm -T dev python -m pytest -q`), apart from the known `test_monitor_memory_cap_and_peak` failure;
  - ruff clean on touched files;
  - the plan file updated if the work deviated from it;
  - the matching docs page updated.

## Phases

| Phase | Content | Review item | Effort |
|---|---|---|---|
| C0 | Network diversity metrics and baseline | measuring all of C | 1.5 d |
| C1 | Fold manhattan into lsystem; remove the generator | style mixing | 3 d |
| C2 | Pattern fields in lsystem: grid, radial, organic, contour, rotation field, organic knob | style mixing | 4 d |
| C3 | `style` control map and blending | style mixing | 2 d |
| C4 | Connectivity knob, suburbs, cul-de-sac bulbs | style mixing, road types | 2.5 d |
| C5 | Junction variety: 5–7 legs, skew, offset T, lane splits in the builder | junction variety | 3 d |
| C6 | `junction` generator (JunctionArt-style) | junction variety | 1.5 d |
| C7 | Roundabouts and U-turns | road types | 2.5 d |
| C8 | Grade separation: overpasses and tunnels | road types | 3.5 d |
| C9 | Interchanges (diamond, cloverleaf), merges, lane drops/adds | road types | 4.5 d |
| C10 | Docs, examples, final measurement, supervisor summary | all | 1.5 d |

About 30 days (6 weeks). Order: C0 → C1 → C2 → C3 → C4. C5 needs only C2; C6
needs C5; C7 needs C4 (it reuses the ring and bulb code); C9 needs C8; C10 is
last. Each of C1, C4, C5–C6, C7 and C8–C9 is usable on its own.

---

### C0 · Network diversity metrics and baseline

Stage A's A0 measured geometry. C0 does the same for topology and diversity,
so every later phase is measured (and it starts stage D).

- New `roadgen/metrics/network.py`, works on a RoadGraph:
  - leg-count histogram (1, 3, 4, 5, 6, 7+);
  - skew per junction: the largest deviation of its arm angles from the evenly spaced shape;
  - offset-T pairs: two T junctions within 50 m on opposite sides of a road;
  - CNR = intersections / (intersections + dead ends) (Teng §4.2);
  - intersections and dead ends per km², street density [km/km²] (Yang §7);
  - orientation entropy: 36 length-weighted bearing bins, normalised (TownSim §4.2);
  - tortuosity distribution;
  - block-size distribution (shapely `polygonize`);
  - counts of roundabouts, cul-de-sacs, overpasses, tunnels and interchanges from `metadata`.
- CLI: `python -m cli metrics NET.xodr --graph NET.json` adds a `network`
  section. `--sweep` reports it per preset.
- Baseline from today's examples: `plans/baselines/2026-10-xx-stage-c-before-*.json`.
- Tests: hand-built graphs with exact values (a 3×3 grid, a star, a tree).

#### C0 result (done 2026-10-26)

As planned: `roadgen/metrics/network.py` (`network_metrics`,
`NetworkMetricsParams`, `network_metrics:` section in `defaults.yaml`),
`metrics --graph JSON` (repeatable), headline numbers per seed in the sweep,
`tests/test_network_metrics.py`. Skew is measured as the largest distance of
a gap between neighbouring arms from 90° or 180° (so T and + shapes have 0,
not the "evenly spaced shape" the plan named, which would have counted every
T as skewed); offset T pairs need the joining edge to be part of both
through roads. Baseline:
[`baselines/2026-10-26-stage-c-before-network.json`](baselines/2026-10-26-stage-c-before-network.json):

| Network | Junctions (3 / 4 / 5 legs) | Dead ends | CNR | Skewed ≥ 15° | Offset T | Orientation entropy (order) |
|---|---|---|---|---|---|---|
| coast_city | 243 / 134 / 0 | 97 | 0.80 | 8 % | 12 | 2.01 (0.92) |
| city_scene (maps, seed 7) | 266 / 80 / 4 | 88 | 0.80 | 38 % | 10 | 2.68 (0.65) |
| organic (grid_organic) | 40 / 25 / 0 | 22 | 0.75 | 31 % | 0 | 2.41 (0.78) |
| organic_curved | 42 / 15 / 0 | 16 | 0.78 | 79 % | 0 | 3.23 (0.30) |
| hill_town | 222 / 92 / 5 | 117 | 0.73 | 87 % | 2 | 3.52 (0.06) |
| grid | 32 / 64 / 0 | 0 | 1.00 | 0 % | 0 | 1.25 (1.00) |
| manhattan_like (old generator) | 80 / 300 / 0 | 0 | 1.00 | 0 % | 0 | 1.28 (1.00) |

No junction had more than 5 legs, and only drifting / contour streets were
skewed; that is what C2–C5 change.

### C1 · Fold manhattan into lsystem, remove the generator

New lsystem params, all defaulting to today's behaviour:

- `oneway_pattern: [none, none]` (major, minor), values `none | alternating`.
  Each proposal carries a `line` index:
  - continuations keep it;
  - branches get `round(perpendicular local coordinate / block_size)`, which is exact on the straight grid.
  - `_to_graph` points each edge along +axis or −axis by parity and puts all lanes forward.
- `boundary: none | ring`, `ring_offset`, `ring_lanes`. After growth, free ends
  within a block of the extent edge are extended straight out to a two-way ring
  rectangle at `ring_offset`; ring nodes go where they meet. This keeps one-way
  grids strongly connected, as manhattan's `loop` mode did.
- `edge_drop_prob`: `ManhattanGenerator._drop_street_edges` moves to
  `roadgen/generators/transforms/drop.py`. It is generator-agnostic, keeps every
  travel direction reachable, and uses a salted rng.
- Helper `grid_extent(n_x, n_y, block_size)`. New preset `manhattan_like`:
  - extent 2250 × 2320, start (0, 0), rotation 29°;
  - lanes (4, 0) / (2, 0), both directions alternating;
  - 11.2 m/s, ring.
- `bench`: `roadgen/bench/benchmark.py` builds lsystem. `grid_sizes` stay
  (n_x, n_y) counts, converted with `grid_extent`, and the CSV keys are unchanged.
  `bench.network` becomes LSystemParams overrides.
- Removal:
  - `roadgen/generators/manhattan.py`, its import in `generators/__init__.py`, `generators.manhattan` in `defaults.yaml`;
  - in `cli.py`: `MANHATTAN_FLAGS`, `_add_manhattan_args`, the special error, the run-config folding;
  - `configs/bench_scalability.yaml` comment, `runs/inputs/wide_lanes.yaml`, `runs/examples.sh`, `runs/make_readme.py`;
  - the layout in `.github/copilot-instructions.md`.
  - Replaying an old manhattan run gives a clear error that names `lsystem --preset manhattan_like`.
- Tests:
  - Port the behaviour tests of `test_manhattan_realism.py` (alternating, ring, drop keeps connectivity, `manhattan_like` connectivity) to a new `tests/test_lsystem_grid.py`. Delete `test_manhattan*.py`.
  - Add a `small_grid(nx, ny)` helper in `tests/conftest.py` for the fixtures in `test_buildings`, `test_scenario`, `test_config`, `test_registry`, `test_history` (`GEN` argv), `test_elevation`, `test_maps`, `test_metrics` and `test_bench`.
  - `test_lsystem.py`'s "grid equals manhattan" test becomes an exact structure test (lattice coordinates, degree histogram).
  - **New guard:** stored hashes of the graph JSON of `grid`, `organic_curved`, `grid_organic` and `city` + maps (seed 1), in `tests/data/`. Every later phase must keep them unchanged.

#### C1 result (done 2026-10-26)

As planned, with these details:

- **Identity guard.** `tests/lsystem_identity.py` recorded fingerprints of
  six networks before any change (`default`, `grid`, `organic_curved`,
  `grid_organic`, and `city` and `hill_town` on generated maps through the
  pipeline) in `tests/data/lsystem_identity.json`;
  `tests/test_lsystem_identity.py` checks them. A fingerprint hashes the
  graph JSON **without `metadata["params"]`**: byte-identical JSON is
  impossible once new parameters exist, since `params` lists all of them.
  All six still match after C4.
- **Grid lines** are counted from the seed point's line (the plan said
  "perpendicular local coordinate"; with a seed in the middle that would
  number lines from the area's edge, which is the same parity only by
  chance). Pieces of a split edge keep their line.
- **Ring:** grid roads ending within the largest block size of an edge
  continue straight to the ring; extensions crossing a road, or ending too
  close to a corner or the previous extension, are left out. The ring
  ignores maps. `map_bounds` includes it.
- **Edge drop** moved to `roadgen/generators/transforms/drop.py` unchanged,
  on a salted random stream, so the remaining roads equal the undropped
  network's.
- `jitter` (node displacement) was not ported (decision 11).
- **Removal:** `manhattan.py`, its 18 CLI flags, `generators.manhattan` in
  `defaults.yaml`, `tests/test_manhattan*.py` (their behaviour tests ported to
  `tests/test_lsystem_grid.py`, the generic connectivity and mixed one-way
  junction tests moved there unchanged). A run or config naming `manhattan`
  stops with a message pointing to `--preset manhattan_like`
  (`REMOVED_GENERATORS` in `cli.py`, checked before the config).
- **Artifacts removed** (prompt 2): the 7 manhattan run folders in `out/`,
  the 17 manhattan runs in the legacy `runs/history.json` (its config
  snapshots stay), the manhattan commands in `runs/examples.sh` (now lsystem
  `manhattan_like` / grid with ring, rerun into new folders),
  `runs/inputs/wide_lanes.yaml`, `runs/make_readme.py`, all docs, the layout
  in `.github/copilot-instructions.md`, `CLAUDE.md`. Left as history:
  `copilot_prompts.md` and the earlier plans and baselines.
- **Bench:** `grid_sizes` stay `[n_avenues, n_streets]` and the CSV keys are
  unchanged; `network_params` builds `LSystemParams` with
  `extent = grid_extent(...)`, `start (0, 0)`, `branch_prob 1`.
- Tests that used manhattan grids as fixtures use `tests/grids.py`
  (`small_grid`, `grid_cli`); the map tests add `ON_MAPS` (no population
  limit, traps allowed), because lsystem grows on the maps where manhattan
  only got heights from them.

### C2 · Pattern fields in lsystem

New `roadgen/generators/patterns.py`: pure functions, no OpenDRIVE.

- **Field interface:** a pattern gives, at a point, a symmetric tensor (its
  major direction) and block lengths (major, minor).
  - `grid(θ, block_size)`: Chen eq. 1.
  - `radial(centre, ring_spacing, spokes)`: major along the radius, minor around it (Paris, P&M Table 1). A spoke branches when the gap between neighbouring spokes reaches 2 × block, so spoke density stays even.
  - `organic`: a grid rotated by a low-frequency fBm field (Chen §5.3 Perlin rotation; reuses `roadgen/maps/noise.py`).
  - `contour`: Chen's height-field field. Today's `contour_deviation` becomes a special case of it.
- **Blend:** T(p) = Σ wᵢ(p) Tᵢ(p). Block lengths are the weighted mean. Where
  |T| ≈ 0 (a degenerate point), the road keeps its previous heading.
- **Rotation (skew) field** (Chen R1/R2): `skew_deg`, `skew_scale`. It rotates
  the minor direction relative to the major, giving Y and K crossings.
- **Growth:** new `global_goal: field`. The `grid` code path stays as it is, for
  identical output.
  - A proposal's role (major = avenue, minor = street) is the existing `axis`.
  - Its heading is whichever eigenvector of T at the start point is closest to its previous heading (streamline following), limited by `_max_turn` so bends keep their design radius.
  - Branches take the other eigenvector.
  - `curve_mode` and `bow_streets` still apply.
- **One grid-to-organic knob** `organic ∈ [0, 1]`. It scales the rotation-noise
  amplitude (0 → 0°, 1 → ±45°). Below 1, a heading within `(1 − organic) · snap_deg`
  of the grid axis snaps to it (TownSim's snap knob `co`).
- Presets:
  - `paris`: radial at the centre, grid outside;
  - `organic_town`: organic 0.8;
  - `skewed_grid`: skew 20°.
- Tests:
  - field maths: radial eigenvectors, blend continuity across a boundary, rotation;
  - orientation entropy (C0) rises steadily with `organic`;
  - `paris` gives an orientation histogram relative to the centre that peaks at radial and circumferential;
  - the C1 hash guard.

#### C2 result (done 2026-10-26)

As planned, with these changes:

- **The suburb pattern is part of C2** (`patterns.py` has all five:
  grid, radial, organic, contour, suburb), so C3's style classes and C4's
  suburbs only had to use it.
- **`curve_mode` is for the grid goal only**; the field goal rejects it
  (organic, contour and suburb patterns replace drift and contour streets).
  `curve_tortuosity` still bows field streets. Today's contour code path is
  unchanged for identity; it now shares `_MapView.gradient()`.
- **Skew is one-sided**, in [0, `skew_deg`] (Chen's R2 example, [0, 20°]):
  with a symmetric field around 0 the skewed share stayed 0 % at
  `skew_deg` 25 because the noise averages out over a city.
- **Midpoint step and stars:** a continuation predicts its midpoint along
  its own heading, so the six spokes of a radial star don't all snap to the
  grid at the degenerate centre (first try: no star at all).
- **Organic knob snapping** compares with the field *without* the knob
  (`weights(knob=False)`); the first version undid the knob wrongly and
  never snapped (caught by `test_organic_knob_moves_grid_weight_and_snaps`).
- Presets `paris`, `organic_town`, `skewed_grid`; all build and pass the
  validators on seeds 0–2.

### C3 · `style` control map

- `MAP_KINDS` gains `style` and `MapSet.style`. Classes: GRID, RADIAL, ORGANIC, SUBURB.
- Generators in `roadgen/maps/style.py`, in the map registry:
  - **`zones`:** domain-warped Voronoi districts. On upstream maps:
    - dense core → grid (radial at the main population centre);
    - middle → grid or organic;
    - sparse → suburb;
    - steep → contour.
    - Each district draws its own grid angle, so districts have different grid orientations, as in real cities.
  - **`image`:** a palette, like the terrain image generator.
- `maps.pipeline` order: terrain → elevation → population → style. `pipeline --maps`
  builds the style map when `global_goal: field`. Map plot colours added.
- lsystem turns classes into weights with a box blur of one-hot masks over
  `blend_width` [m] (P&M's grey-scale weights). Radial centres come from the
  population map's centre metadata, otherwise from the zone centroid.
- Preset `mixed_city`.
- Docs: `docs/maps.md`.
- Tests:
  - the zones are deterministic;
  - on a flat population the result is one zone;
  - the blend weights sum to 1;
  - `mixed_city` has several orientation clusters.

#### C3 result (done 2026-10-26)

As planned, with these details:

- **Five classes** (grid, radial, organic, contour, suburb), in the order of
  `patterns.PATTERNS`, so a style map's blurred one-hot shares are the field
  weights directly.
- **District angles** go into the layer's metadata (`sites: [x, y, class,
  angle_deg]`, `radial_centers`); lsystem turns the grid by the nearest
  site's angle. A second raster for angles was not needed.
- **Only built when read.** Generators got `map_kinds(params)`; lsystem
  asks for `style` only with the field goal, so `pipeline --maps` of the
  existing presets builds exactly the maps it did before (the identity
  fingerprints include `metadata["maps"]`). The grid goal also drops a style
  map handed to it.
- `MapLayer.sample`'s bilinear code moved to `model.bilinear` (same
  arithmetic) so lsystem can sample the five weight rasters at once.
- Tuning on the default generated maps: `contour_grade` 0.08 made 47 % of
  the area contour districts, 0.12 none; **0.10** gives 6–13 %.
  `zone_size` **800** m.
- Preset `mixed_city`; `map style zones` presets `planned` and `old_town`.

### C4 · Connectivity knob, suburbs, cul-de-sacs

- **`connectivity` knob k** (Lechner §3 detour ratio). After growth, a
  connector pass samples nearby node pairs within `connect_radius`. It adds a
  segment where network distance > k × straight distance and the segment passes
  `acceptable()`. Low k gives loops and grids; high k gives tree-like suburbs.
- **SUBURB pattern:**
  - lower `branch_prob`, curved, min angle 70°;
  - Teng defaults: 50 ± 10 m segments, snap 20 m;
  - dead ends kept.
- **Cul-de-sac bulbs** (`transforms/culdesac.py`):
  - A street dead end in a suburb zone (or with `culdesac_prob`) becomes a teardrop. The stem end becomes a 3-leg junction joined to a one-way loop of 4–6 bend nodes at `culdesac_radius` (≈ 14 m, above `min_lane_radius` + lane).
  - It provides a turn-around, so these dead ends are no longer traps for the connectivity validator.
  - It is skipped if the bulb would hit a road, water or the extent.
- Tests: CNR falls steadily as k rises; bulbs build, pass drive-check and leave no traps.

#### C4 result (done 2026-10-26)

As planned, with these changes:

- **`detour_ratio`** is 0 (off) or ≥ 1. Dead ends try first, then the
  other nodes in random order (salted stream), each at most one connector to
  one of its 6 nearest nodes within `connect_radius`. On suburb networks the
  CNR goes 0.86 (off) → 0.95 (k = 3) → 0.96 (k = 1.5), and 5–6-leg
  junctions appear where connectors meet nodes.
- **Bulbs retreat.** With the bulb only ahead of the dead end, 2 of 41 dead
  ends fit one. Now the end node may move back along the street by one
  radius, up to three times, keeping 30 m of street; with a 15 m radius
  (not the 14–18 m first tried) and 2 m clearance, 40–60 % of dead ends get
  one. The loop has 6 nodes with arms 120° apart at the end node. Every
  bulb network built and passed drive-check (all movements and lanes).
- **New: `suburb_end_prob`** (0.25): a suburb street may end at a node. Without it
  the suburb pattern was a curvy grid, not tree-like.
- Preset `suburb` (no connector: a tree with bulbs).
- **Stub roads (found by the example runs).** `mixed_city` on maps (seed 1)
  failed continuity: a 0.95 m road was left between two sloped junctions
  (one of them a two-road node the builder keeps as a junction because the
  bend is too tight for its inner lane, which the repair didn't know). The
  road could not change between the two planes' banking, so its outer lanes'
  grade jumped by 0.017–0.018. `repair.stub_roads` now finds single-edge
  roads between two junctions keeping less than `MIN_ROAD_LENGTH` (5 m), on
  the builder's own alignment, and the repair merges or removes them like
  too-short edges. The identity fingerprints are unchanged.

#### After C0–C4 (2026-10-26)

[`baselines/2026-10-26-stage-c-after-c4-network.json`](baselines/2026-10-26-stage-c-after-c4-network.json),
from the example runs (`runs/examples.sh`, phase 9):

| Network | Junctions (3 / 4 / 5 / 6 legs) | Dead ends | CNR | Skewed ≥ 15° | Offset T | Orientation entropy (order) | Cul-de-sacs |
|---|---|---|---|---|---|---|---|
| manhattan_like (lsystem) | 80 / 300 / 0 / 0 | 0 | 1.00 | 0 % | 0 | 1.28 (1.00) | 0 |
| coast_city (unchanged) | 243 / 134 / 0 / 0 | 97 | 0.80 | 8 % | 12 | 2.01 (0.92) | 0 |
| paris | 141 / 79 / 1 / 1 | 26 | 0.90 | 30 % | 2 | 3.47 (0.10) | 0 |
| organic_town | 71 / 28 / 0 / 0 | 15 | 0.87 | 6 % | 4 | 2.71 (0.63) | 0 |
| skewed_grid | 25 / 51 / 0 / 0 | 1 | 0.99 | 100 % | 2 | 1.36 (1.00) | 0 |
| suburb | 130 / 79 / 0 / 0 | 30 | 0.87 | 50 % | 1 | 3.19 (0.32) | 15 |
| mixed_city (maps, seed 1) | 213 / 102 / 4 / 0 | 101 | 0.76 | 39 % | 5 | 3.48 (0.09) | 1 |

`manhattan_like` from lsystem has the same junction counts and orientation
as the old generator's. Against the exit criteria so far: entropy rises with
`organic` and `mixed_city` is well above `grid` (3.48 vs 1.25); CNR falls
with `detour_ratio` (tests) and the city presets lie within Teng's 0.6–1;
`skewed_grid` meets the skew target, `organic_town` doesn't (6 %, target
10 %): its bends are smooth, so junctions stay near 90°. 5+-leg junctions
are 1.3 % of `mixed_city`'s (target 2 %): C5's job.

### C5 · Junction variety (generator and builder)

- **Generator:**
  - `max_degree` (default unchanged);
  - star junctions: radial centres with `spokes` 5–7 (étoile);
  - per-pattern `min_angle_deg`;
  - a **stagger** knob: perpendicular branches on the two sides of a street are offset by `stagger_offset` (15–40 m), giving offset crossroads;
  - skew comes from C2's rotation field;
  - `repair.MAX_DEGREE` becomes a parameter (7).
- **Builder** (`roadgen/opendrive/junctions.py`):
  - Robust for 5–7 legs and arms down to the 25° generator minimum. Check `classify_exits`, the trims and the clothoid fit on turns of 150° and more.
  - **Lane splits and merges** (JunctionArt stage 3): when a straight exit has more lanes than its entry, one incoming lane feeds two outgoing lanes, the outer one by default. Policy `opendrive.lane_split: outer | inner`. Surplus lanes merge, as `continuation_pairs` already does for 2 arms. No two connecting roads from the same approach cross each other.
- Tests:
  - synthetic 5-, 6- and 7-leg stars;
  - a 30° Y, a K;
  - an offset-T pair 20 m apart (repair must leave it if it can be built);
  - a 1 → 2 lane split;
  - all pass topology, continuity, dynamics and drive-check.

### C6 · `junction` generator

- `roadgen/generators/junction.py`, `@register("junction")`. Params:
  - `legs` distribution over 3–7, `skew_deg`, `min_angle_deg`;
  - `offset` (lateral shift of a leg's axis from the centre [m]), `leg_length`;
  - lanes per side from JunctionArt's distribution [0.15, 0.6, 0.2, 0.05], never 0 on both sides;
  - lane width, speed, road class per leg;
  - `pair` mode (two close junctions, giving an offset T).
- Validity is checked with `geometry/junction_space` trims, never OpenDRIVE.
  Retries use a salted rng, so output stays deterministic.
- Leg ends optionally get cul-de-sac bulbs (C4), so scenarios have no traps.
- Presets: `t`, `y`, `skewed_cross`, `k`, `five_way`, `six_way`, `random`.
- Sampling helpers shared with C5 in `roadgen/generators/sampling.py`.
- CLI: `generate junction --preset k`, `pipeline junction ...`.
- Tests: every preset builds and drives; over seeds 0–199, `random` fills every leg-count bin.

### C7 · Roundabouts and U-turns

- **`transforms/roundabout.py`:**
  - Candidates are junctions with degree 3–5 and arms at least 40° apart, chosen with `roundabout_prob` per zone (higher in organic and suburb), plus optionally radial centres.
  - Inscribed radius by largest arm class (FHWA): mini 12 m, single-lane 15–20 m, two-lane 25–30 m.
  - Arms are cut back to the ring, each at an entry node.
  - The ring is one-way counter-clockwise (right-hand traffic), split into arcs with a bend node every 45° or less; the builder fits the arcs (stage A).
  - Ring lanes: 1 or 2.
  - Skipped if a cut-back arm would be shorter than its trims.
  - `metadata["roundabouts"]` holds the ring, entries and radius (stage B's yield signs use it later).
- The builder needs no roundabout logic: entries are 3-leg junctions with
  one-way rings, and turn speeds come from curvature.
- **U-turns:**
  - roundabouts and cul-de-sac bulbs give natural U-turns;
  - builder option `opendrive.u_turns` adds a U-turn movement only where the in and out lane centres are at least 2 × `min_turn_radius` (6 m) apart, i.e. on wide roads, fitted as clothoids;
  - the connectivity validator allows them there.
- Tests: rings with 3, 4 and 5 arms, one- and two-lane; esmini drive-check; a U-turn exists only on wide roads.

### C8 · Grade separation: overpasses and tunnels

- **Overpasses:**
  - New `roadgen/geometry/grade_separation.py`, a neutral graph → graph step.
  - For each listed crossing of two edges without a shared node, the upper road (the higher class) is raised to `clearance` (5.0 m) above the lower one, with ramps at most `max_grade` long and vertical curves.
  - An underpass is used if the upper road can't rise.
  - Writes `Edge.profile` and a bridge span. Recorded in `metadata["overpasses"]`.
- **Pipeline order:** generate → align → drape → **separate**. It also runs
  without maps, where a flat graph gains heights.
- **lsystem:** `highway_crossings: at_grade | separated | mixed`.
  - A street that would cross a separated highway passes under it without a split; `acceptable()` allows that crossing.
  - No node may lie within the ramp length of the crossing.
- **Tunnels:**
  - P&M §3.3: a highway segment rejected as too steep is allowed when a straight-graded tunnel keeps it at or below `max_grade`, the cover is at least `tunnel_min_cover` and the tunnel is no longer than `max_tunnel_length`.
  - New `Edge.tunnels`. The drape grades a straight line between portals inside.
  - The builder writes `<objects><tunnel>`, next to `_add_bridges`.
  - `scene/ground.py` leaves the hill over a tunnel unlevelled.
- **Validation:** new `roadgen/validate/clearance.py`:
  - roads that cross without a junction must be vertically separated by at least `clearance - tol` at the crossing (sampled from the .xodr elevation);
  - tunnels lie under the terrain.
- **Risk:** esmini position lookup on overlapping roads. Mitigated with a
  drive-check test of a single overpass before C9 builds on it.

### C9 · Interchanges, merges, lane drops and adds

- **Shared base:**
  - `NodeKind` `merge` / `diverge`. In `plan_movements`, these nodes only get movements between lanes on the same side; a diverge is a lane split from the outermost lane, a merge joins the outermost lane.
  - Auxiliary lanes are lane-count changes on the highway edge (stage A tapers): a deceleration lane before a diverge and an acceleration lane after a merge (AASHTO parallel type, about 150–250 m).
  - Ramps leave at 10–15° after the auxiliary lane, which keeps trims around 25–30 m. This is checked with a script in `plans/scripts/` before the code is written.
  - `metadata["interchanges"]` records type, ramps, merge and diverge nodes (for stage E's merge scenarios).
- **Templates** (`transforms/interchange.py`), placed at separated crossings at
  least `interchange_spacing` apart (default 1.5 km), most populated first:
  - **Diamond** (highway × avenue/street, post-pass): four one-way ramps, each ending in a 3-leg junction on the crossing road 60–100 m from the overpass. Skipped, deterministically, if a ramp would hit another road.
  - **Cloverleaf** (highway × highway): placed in the lsystem highway phase, before streets grow. It reserves its footprint as an area streets can't use (a mask in `_MapView`).
    - 4 loop ramps of about 270° at the design radius for 40 km/h (≈ 50 m), with bend nodes at most 45° apart;
    - 4 outer ramps;
    - weaving sections are auxiliary lanes between loop exit and loop entry.
  - Highway × highway T junctions stay at grade in stage C (a trumpet is a later template).
- **Mainline lane drops and adds:** `highway_lane_change_prob` changes the lane
  count by ±1 along highway chains, built as stage A tapers.
- Preset `highway_corridor`.
- Tests:
  - each template builds;
  - every ramp movement is drivable (drive-check);
  - no movement crosses the centre line at a merge or diverge;
  - auxiliary lane tapers are present;
  - clearance passes.

### C10 · Docs, examples, final measurement

- Docs:
  - `docs/generators.md`: replace the manhattan section with lsystem grids, one-way and ring; add patterns, the junction generator and the transforms.
  - `docs/maps.md`: the style map.
  - `docs/opendrive.md`: lane splits, merge/diverge, tunnels, overpasses, U-turns.
  - `docs/validation.md`: clearance.
  - `docs/cli-reference.md`, `README.md`, `docs/architecture.md`.
- Examples: `runs/examples.sh` updated.
- `plans/2026-10-xx-stage-c-summary.md`, a supervisor summary like stage A's.
- C0 metrics after, saved to `plans/baselines/…-stage-c-after-*.json`.

## Exit criteria

| Metric | Target |
|---|---|
| Existing presets (`grid`, `organic_curved`, `grid_organic`, `city` + maps) | graph JSON identical to before, apart from `metadata.params` (met after C4: `tests/test_lsystem_identity.py`) |
| Build failures over 200 seeds, `city` and `mixed_city` | 0 |
| Leg-count bins 3–7 | all filled by `junction --preset random` (200 seeds); ≥ 2 % of `mixed_city` junctions have 5 legs or more |
| Skew | ≥ 10 % of junctions skewed ≥ 15° in `skewed_grid` and `organic_town` |
| Orientation entropy | rises steadily with `organic`; `mixed_city` higher than `grid` |
| CNR | falls steadily with `connectivity`; within Teng's 0.6–1 range for city presets |
| `mixed_city` + `highway_corridor` | contain roundabouts, cul-de-sacs, overpasses, tunnels (hilly maps), diamonds and a cloverleaf |
| Validators and drive-check | pass on every new preset, plus the clearance check |

## Risks

- **Identical output.** The C2 refactor touches the growth loop. The `grid`
  path stays separate, and the C1 hash guard catches any drift.
- **esmini on overlapping roads** (C8): tested early with one overpass. The
  fallback is `at_grade` crossings.
- **Cloverleaf footprint** (≈ 500 m) in dense maps: it is reserved before
  streets grow and skipped if the area isn't free.
- **Trims at 5–7 legs** eat short edges: repair with `max_degree` 7, measured in C5.
- **Junction turns of 150° and more** at 7-leg stars may not fit a clothoid:
  `turn_path` falls back to paramPoly3, which is logged and counted.

## Verification

- `docker compose run --rm -T dev python -m pytest -q` (all green, apart from the known bench test) and `ruff check` after each phase.
- End to end:
  - `python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 --ground --buildings --drive-check -o mixed`
  - `pipeline lsystem --preset manhattan_like`
  - `pipeline junction --preset random --seed 0..`
  - `pipeline lsystem --preset highway_corridor --maps`
- `python -m cli metrics out/<hash>/mixed.xodr --graph out/<hash>/mixed.json`, compared with the C0 baseline.
- `metrics --sweep 200` for build failures.
- `view` in odrviewer for a visual check of roundabouts, interchanges and tunnels.
- `bench` with `configs/bench_scalability.yaml` still runs on lsystem grids.
