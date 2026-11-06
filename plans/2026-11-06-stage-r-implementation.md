# Stage R (refinement): implementation plan

Status: **in progress: R1–R6, Q and part of P done (2026-11-06); R7, the R4 footprint cut and the rest of P open.** Review, measurements and decisions:
[2026-11-06-refinement-review.md](2026-11-06-refinement-review.md) (online:
"Roadgen Refinement Review", source `artifacts/refinement-review.html`).

## Prompts

**2026-11-06, prompt 1**

> Read the plans, repo and start to implement the tasks in the refinement-review.html file. running all tests take too much time. So just run the affected tests. also, for example generation, use only examples in the runs folder. overwriting is not a problem.

## Decisions

Decisions 1–6 of the review hold (role-based hierarchy, fix in esmini too,
re-record the lsystem fingerprints, junction fill in the scene, ground fixes
then footprint cut, order R1 → R7 with P alongside). Decisions taken while
implementing:

| # | Question | Decision | By |
|---|---|---|---|
| R-1 | Tests | Only the tests a change affects are run, not the whole suite. | user |
| R-2 | Example networks | Only the examples of `runs/examples.sh`; overwriting earlier run folders is fine. | user |
| R-3 | Names of the class levels | The graph keeps its three classes (`highway`, `avenue`, `street`), which the builder, cross-sections, marks, pockets and junction control all read. Hierarchy levels map onto them: **arterial = avenue** with `major_lanes`/`major_speed`, **collector = avenue** with the new `collector_lanes`/`collector_speed`, **local = street** with `minor_lanes`/`minor_speed`. A fourth class would ripple through every HD module for no visible gain. (The level is not stored separately: later transforms renumber and split edges, and class + lanes already tell it.) | Claude |
| R-4 | Highways in the ranking | Highways stay what the population phase grows; the ring road keeps `ring_lanes`. Strokes rank only the other roads. Turning a grid line into a highway (22 m/s, shoulders) in a map-less town would look wrong. | Claude |
| R-5 | Keep the axis rule as an option? | Yes, as `hierarchy: axis` (default `strokes`). Two reasons: `tests/test_hdmap_identity.py` must stay byte-identical with `--no-hd`, which needs the graphs it was recorded on; and runs recorded before stage R replay the same with `--set hierarchy=axis`. It is documented as legacy; no code is shaped around it. | Claude |
| R-6 | Parameter names | `major_*` / `minor_*` keep their names (they now mean arterial / local) so configs and run.yaml files keep working. | Claude |
| R-7 | Fingerprints | Re-recorded at the end of R1–R3 together (R2 and R3 change growth too), not once in R1 only as the review planned. | Claude |
| R-8 | Body widths while growing | Classes come after growth, so every non-highway road is taken as a local road (driving lanes + `side_width` 2.5 m), highways as lanes + 3 m shoulder. Wider arterials are caught by the body-overlap validator and the naturalness gate. | Claude |
| R-9 | Near-parallel proposals | Rejected (with the `min_block_depth` gap) instead of snapped onto the other road; a proposal passing within `snap_radius` of a node now ends at that node, which turned most would-be parallels into junctions. | Claude |
| R-10 | Body overlaps in validation | Logged as warnings, not errors: an overlap is a layout flaw, the file is still valid; the naturalness metric counts them. | Claude |
| R-11 | Dead ends at the area's edge | Go through extend → trim → bulb like the others (the review excluded "roads leaving the map"): otherwise they stay traffic traps. | Claude |
| R-12 | Roundabout fit | Checked with the default (urban) cross-section's trims and with the far junction's real trims after the arm turns: collectors are avenues now, and their side lanes left a 1.2 m arm in suburb seed 1 that failed the drive-check. | Claude |
| R-13 | P1 | No adjacency index (transforms change edges in place, which an index would miss); `incident_edges` scans once and sorts only the edges at the node. | Claude |
| R-14 | Junction fill colour | Mean of esmini's own `resources/models/asphalt.jpg` (0.317 grey), instead of a screenshot measurement. esmini's model and texture resources are installed in the dev image (`/opt/esmini/resources/models`: asphalt.jpg, grass.jpg, ...). | Claude |

## Work items

Each item ends with: affected tests green, ruff clean on touched files,
the examples in `runs/examples.sh` regenerated where the item changes them,
this file updated where the work deviated.

### R1 Road hierarchy and lane continuity

- New pure step `roadgen/generators/hierarchy.py`:
  - strokes: at a two-road node the two edges always join (a bend is the same
    road); at a node with three or more roads, pairs join greedily by smallest
    deflection under `stroke_max_deflection_deg` (35°);
  - score = Σ edge length × sampled edge betweenness (networkx, seeded,
    `betweenness_samples`), so length × mean betweenness of the stroke;
  - classes by share of the ranked length: `arterial_share`, then
    `collector_share`, the rest local;
  - lanes per class; asymmetric lane tuples are oriented along the stroke,
    one-way edges (alternating pattern) keep their direction with all lanes
    forward.
- `lsystem._to_graph` calls it (`hierarchy: strokes`); `axis` keeps the old rule.
- Re-record `tests/data/lsystem_identity.json`; `tests/hdmap_identity.py`
  builds with `hierarchy=axis`.
- Docs: `docs/lsystem.md`, CLAUDE.md rule on lsystem identity.

Done when the probe finds no lane jump and no class change at two-road nodes
(intended highway lane adds aside) on the example presets.

### R2 Width-aware growth

- `acceptable()`: reject a proposal whose body (buffer by the outer half
  width, side lanes included) touches the body of a road it does not connect
  to; spacing between near-parallel roads; proposals running alongside an
  existing road under 20° snap onto it; minimum angle grows with width.
- Body-overlap check in `validate/clearance.py`.

### R3 Dead-end policy

- After growth: extend a free end to the nearest road within ~1.5 blocks,
  else trim a stub shorter than a block, else a turning circle.
- Connector pass on in map presets; dead-end share as a metric; examples
  without `--allow-traps`.

### R4 Ground under the roads

- `scene/ground.py`: level out to margin + cell·√2, clamp banking at the road
  edge, keep land under bridges below the deck, smooth the class colours;
  then the footprint cut; simple decks and piers.

### R5 Junction colour

- Junction fill in the scene mesh in asphalt colour; `patch` objects only
  when no scene is written.

### R6 Buildings

- Parcels by recursive oriented-box splits, one building per parcel with
  setbacks, heights from population.

### R7 Materials and Blender export

- glTF road mesh per lane type with UVs, material ids, CC0 texture fetch
  script.

### Q Quality gates

- Probe metrics in `roadgen/metrics/network.py`: dead-end share, lane jumps,
  class changes, body overlaps.

### P Performance (alongside)

- P1 adjacency index on RoadGraph, P2 parse once in validation, P3 vectorize
  RoadEval / lane polygons, P4 alignment once, P5 local repair checks,
  P6 lane plot opt-in.

## Results

Measured with `plans/scripts/refinement_probe.py`, `plans/scripts/hierarchy_check.py`
and `plans/scripts/poke_through.py` (map presets: `pipeline --maps --no-hd`).

### R1 (done)

`roadgen/generators/hierarchy.py`, `hierarchy: strokes` default. Lane or class
changes at two-road nodes: **0** on every preset, highway lane adds aside
(highway_corridor). Class changes along straight continuations remain only at
junctions with 3+ roads (city 11, mixed_city 5, hill_town 10), mostly
highway ↔ street. The MUTCD taper step was not needed: no lane count changes
at two-road nodes any more except the intended highway lane adds, which keep
their existing taper. `hierarchy: axis` reproduces the old graphs exactly
(fingerprints of the four map-less cases equal the pre-R values).

Found on the way: `tests/test_hdmap_identity.py` already fails on HEAD
(3ae2780) for manhattan_like, organic_town and city_maps (checked with
`git stash`); not caused by stage R. Left as is.

### R2 (done)

Body check in `acceptable()`, node pass-by snap in `resolve()`, width-aware
minimum angle, `check_body_overlaps` in `validate/clearance.py` (warnings).
Cul-de-sac bulbs check other roads' driving width too.

| Body overlaps (probe) | before | after |
|---|---|---|
| city seed 7 | 10 | 0 |
| mixed_city seed 1 | 4 | 0 |
| hill_town seed 1 | 5 | 0 |
| paris / organic_town | 3 / 2 | 0 / 0 |

highway_corridor's 12 are all grade-separated crossings (the probe does not
know heights; the naturalness metric leaves bridges out). The worst cases
before were roads passing 0.2 m from a dead-end node that later continued
alongside, and highways running 8 m apart.

### R3 (done)

`dead_end_pass` (extend → trim) in the growth, bulbs for the rest, connector
pass on in city, hill_town, highway_corridor.

| Dead-end share | before | after |
|---|---|---|
| city seed 7 | 14.5 % | 1.5 % |
| mixed_city seed 1 | 11.2 % | 2.2 % |
| hill_town seed 1 | 11.3 % | 2.5 % |
| highway_corridor seed 1 | 18.8 % | 2.5 % |
| paris / organic_town | 9.8 % / 7.9 % | 0.6 % / 0 % |

### R4 (done, footprint cut open)

Quick fixes, a final press-down of triangles over sampled road surfaces, and
`poke_through()` (logged by the pipeline).

| Road samples under the ground | before | after |
|---|---|---|
| city | 1626 of 149 732 (worst 0.62 m) | 0 |
| hill_town | 1766 of 174 082 (0.79 m) | 0 |
| mixed_city | 1631 of 186 802 (0.99 m) | 0 |

Not done: the footprint cut with constrained triangulation, decks, piers
and portals. With the press-down the ground no longer covers roads; the cut
is still worth doing for the look of the edges (Blender, R7).

### R5 (done)

Builder keeps the fill areas (`fill_areas`, sidecar `junction_fills`);
`hdmap.junction_patches` (default true) writes the patch objects; the
pipeline turns it off when it writes a scene and draws the areas with
`roadgen/scene/junctions.py` (`scene:` section).

### R6 (done)

`roadgen/scene/parcels.py`; `buildings.parcels` (default true), `frontage`,
`depth`, `inset`, `height_density`. Blocks split into two rows of parcels
back to back and across into 15–40 m frontages; parcels without frontage stay
courtyards; one inset building per parcel, none in water, parks or forest;
heights from the population map. city seed 7: 1936 buildings (was one per
block area). Kept `corner_radius` rounding of the block area (junction
clearance); the buildings themselves have sharp corners.

### Q (done)

`naturalness` in `metrics/network.py` (and in the sweep headline);
`tests/test_network_metrics.py` gates organic_town, paris, grid_organic and
suburb (no two-road changes, no overlaps, dead ends ≤ 3 %).

### P

- P1: `incident_edges` without the full sort (R-13).
- P6 (part): the lane plot `PREFIX_hd.png` is opt-in (`pipeline --lane-plot`,
  about 4.5 s on city); `runs/examples.sh` passes it so the examples keep it.
- P2–P5, rest of P6: open.

### Found on the way

- **Buildings on turn pockets.** Building footprints never accounted for
  left-turn pockets. Old grids had no pockets on avenues (their 80 m edges
  were too short), but the hierarchy puts arterials and collectors on the long
  edges, which get them: on the HD grid 7 300 m² of footprints stood on
  pocket lanes. `footprints` now widens each pocket approach by a lane from
  the junction trim over pocket length + taper (`tests/test_corners.py`
  checks against the build's cross-section, pockets included).
