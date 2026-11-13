# Validation and metrics

## Checks

Independent checks run on the RoadGraph, on the written `.xodr`, and by
driving the network with esmini's own road logic. `pipeline` and
`build-xodr` run all of them except drive-check, and fail if any reports
an error. `pipeline` also runs the drive-check by default
(`--no-drive-check` leaves it out).

| Check | Runs on | When | Code |
|-------|---------|------|------|
| [Stage checks](#stage-checks) | each map, the RoadGraph after each stage, the scene | after every stage | `validate/stages.py` |
| [Graph validity](road-graph.md#roadgraph) | RoadGraph | in the generate and fit stage checks | `RoadGraph.validate()` |
| [Traffic connectivity](#traffic-connectivity) | RoadGraph | always; errors in `pipeline` unless `--allow-traps`, warnings in `build-xodr` | `validate/connectivity.py` |
| [Topology](#topology) | `.xodr` | always | `validate/topology.py` |
| [Continuity](#continuity) | `.xodr` | always | `validate/continuity.py` |
| [Curvature continuity](#curvature-continuity) | `.xodr` | always (`paramPoly3` jumps are warnings) | `validate/continuity.py` |
| [Lateral acceleration](#lateral-acceleration), resultant slope | `.xodr` | always | `validate/dynamics.py` |
| [Clearance](#clearance) | `.xodr` | always | `validate/clearance.py` |
| [HD-map content](#hd-map-content) | `.xodr` | with HD maps on | `validate/hdmap.py` |
| [XSD schema](#xsd-schema) | `.xodr` | only with `OPENDRIVE_XSD` or `--xsd` | `validate/schema.py` |
| [Drive-check](#drive-check) | `.xodr` + sidecar | `drive-check`, `pipeline` (default) | `sim/drive_check.py` |

### Stage checks

*Which step broke it?*

Every stage of the [pipeline](architecture.md) is checked right after it
runs, so a failure names its stage. The checks are cheap: they test what the
stage promises, on its output (and its input, where the stage must leave
something unchanged).

| Stage | Errors | Warnings |
|-------|--------|----------|
| `map terrain`, `map elevation`, `map population`, `map style` (each as it is built, before the next is built on it) | values not finite; unknown terrain or style classes; a generated elevation map with water above the lowest land (the generator sets water flat at its lowest height); a generated population map with people on water, or negative densities | terrain without land; for a loaded map file (`--map-file`, used as it is) the water rules |
| `maps` | | the maps on different grids |
| `generate` | `RoadGraph.validate()`; nodes without a road | nodes outside the maps |
| `align` | anything changed but `Node.radius_cap`; caps not positive | |
| `drape` | the plan view changed; heights not finite; bridges or tunnels overlapping or out of order | (steep roads: `drape` warns itself) |
| `separate` (when roads cross without a junction) | the plan view changed; a crossing less than `grade_separation.clearance` apart in height, where the fitted roads cross | |
| `annotate` (HD maps, with a population map) | the plan view or heights changed; unknown profiles or junction controls | |
| `fit` | `RoadGraph.validate()` (edges still too short for their junctions: the repair warns, the build stops) | |
| `xodr` | the file checks below, from topology to the XSD schema | body overlaps, curvature warnings |
| `scene` (with the ground) | ground vertices or normals not finite; triangles indexing missing vertices | road surface the ground covers |
| `drive-check` (`pipeline`, by default) | the drive-check failed | |

A map that fails stops the run there. A graph stage's errors are logged and
collected; the pipeline still writes its outputs, so the faulty step's result
can be looked at, and fails at the end. `pipeline` ends with one line naming
every stage and its errors and warnings:

```
validation: map terrain ok; map elevation ok; map population ok; maps ok; generate ok; align ok; drape ok; separate ok; annotate ok; fit ok; xodr ok; scene ok
```

The single-stage commands (`map`, `generate`, `align`, `drape`, `separate`,
`annotate`) run the same check on their output, and exit with 1 on its errors.

### Traffic connectivity

*Can every car get everywhere, and can no car get stuck?*

The check builds a **traversal graph**:

- its vertices are directed traversals *(edge, direction)* that have lanes in
  that direction;
- a traversal arriving at a node continues onto any *other* edge leaving it,
  with no U-turns, mirroring the movements the builder creates.

Reported as errors:

- **Traps:** traffic arriving on an edge has no way out (dead ends, one-way
  sinks), so vehicles would get stuck. A dead end listed in the graph's
  `metadata.exits` is no trap: the road leaves the generated area there (a
  freeway running on), and traffic may come back at any exit. lsystem lists
  the dead ends whose road would leave its area within one block length;
  the `junction` generator lists its leg ends.
- **Unreachable roads:** traffic leaving on an edge that nothing feeds.
- **Not strongly connected:** the node-level lane-direction graph has several
  strongly connected components. Nodes outside the main one are listed.

Groups that differ only by travel direction are logged, not reported. On a
pure loop, for example, reversing would need a U-turn, but no vehicle is
stranded.

**Fixes:**

- lsystem's `dead_ends` (on by default) extends, trims, bulbs, or else cuts
  the dead ends that are not exits;
- `--set boundary=ring` for one-way grids;
- `--allow-traps` when dead ends are intended (e.g. `--set dead_ends=false`).

### Topology

Structural integrity of the `.xodr`:

- road and junction ids are unique, and connection ids are unique within each
  junction;
- every road `predecessor` and `successor` points to an existing road or
  junction;
- road-to-road lane links name lanes that exist at the linked contact end;
- junction connections:
  - reference existing incoming and connecting roads;
  - the connecting road belongs to that junction, and the incoming road links
    to it;
  - every `laneLink` lane exists, at the end of the incoming road that the
    connecting road links to.

### Continuity

Lane-level continuity, evaluated **independently of the builder**. The check
parses the XML and evaluates `line`, `arc`, `spiral` and `paramPoly3`
geometry, `laneOffset`, polynomial lane widths, elevation and superelevation
itself. Spirals use the maths of `geometry/primitives.py`.

**Between roads**, every lane linked to another road must match it at the
contact point within:

| Quantity | Tolerance |
|----------|-----------|
| Lane-centre position | 1 cm |
| Travel heading | 0.01 rad |
| Height (0 without `<elevationProfile>`) | 1 cm |
| Grade in the travel direction | 0.01 |

A connecting-road lane without a lane link is an error, unless it is 0 m
wide at that end.

**Inside a road**, across every lane-section boundary:

- a lane linked to the next section must exist there with the same width
  (1 cm) and link back;
- a lane without a successor must have narrowed to 0;
- a lane without a predecessor must start at 0.

Lane adds and drops are therefore always tapers, never steps.

### Curvature continuity

`check_curvature`: curvature may not jump by more than **0.002 1/m**:

- across a geometry boundary inside a road; or
- across a lane link between roads, as lane-centre curvature in the travel
  direction.

A jump is an error, except where a `paramPoly3` fallback is involved. Those
are warnings, reported as a count.

### Lateral acceleration

`check_dynamics`: driving each road's lanes at the posted `<type><speed>`
may need at most `design.a_lat_max` (3 m/s²) plus 0.05 of lateral
acceleration `v²·|κ|`. The peak is checked:

- at the ends of every line, arc and spiral, where curvature is extreme;
- every 0.5 m along `paramPoly3` pieces.

`check_resultant_grade`: where a normal road is banked, the resultant slope
√(grade² + e²) may not exceed `design.max_resultant_grade` (10 %), or the
grade alone where that is steeper. It is sampled every 1 m, leaving out the
`bank_ramp_length` next to junction ends, where the cross slope blends into
the junction plane's (at most `junction_max_grade`).

### Clearance

Two normal roads (not connecting roads) whose reference lines cross in plan
view never meet, so one must pass over the other. At every such crossing,
their reference heights must differ by at least
`grade_separation.clearance` minus the 1 m deck, i.e. 5 m. This checks the
[overpasses](heights.md#3-grade-separation-overpasses).

Contacts within 1 m of a road's end don't count. A dead end reaching
another road is the generator's layout, e.g. a dead end stopping right next
to a street that grew past it later.

**Road bodies** (`check_body_overlaps`). The driving lanes of two normal
roads must not overlap by more than 1 m² unless the roads meet: one links to
the other, or both end at the same junction (its trims separate them), or one
passes over the other with the clearance above between them. Overlaps are
logged as warnings: they are the generator's layout, not a broken file.

### HD-map content

*Is the HD-map content consistent?* Runs in `pipeline` and `build-xodr`
with HD maps on.

- **No marks inside junctions:** a connecting road has no `<roadMark>`.
- **Mark records in order:** within each lane of a lane section, the road
  marks' `sOffset`s lie inside the section and strictly ascend, so every
  stretch of the lane has exactly one mark.
- **Signals and objects on their road:** within the road's length and outer
  edge (signs up to 2 m outside it), with `<validity>` lanes that exist at
  their station.
- **Unique ids:** signal ids, and object ids (bridges and tunnels included).
- **Lane types along links:** a lane linked to the next lane section, or
  across a road-to-road link (a connecting road's ends too), keeps its type;
  every junction `<laneLink>` joins two lanes of the same type, so no
  driving lane leads into a non-driving lane or the other way round; and (sidecar)
  every lane of a movement's connecting road is a driving lane.
- **Controlled approaches** (from the sidecar's `junctions`): every `stop`,
  `yield` or `signal` approach with incoming lanes has a stop or yield line
  within 8 m of the road end, and every `stop` and
  `yield` approach a 206 / 205 sign there, facing its traffic.
- **Priority records** name two connecting roads of their own junction.
- **Lights and controllers:** every light is in exactly one `<controller>`,
  every controlled signal exists, and every `<junction><controller>` names an
  existing controller. Every `signal` approach has a facing light within 8 m
  of the road end.
- **Signal plans** (sidecar): the junction's controller holds exactly the
  plan's lights; every phase sets every light, every light turns green once
  per cycle, every green lasts at least `min_green`, and no two conflicting
  movements are green in one phase unless one yields to the other (a
  permissive left turn).
- **Lane use and bans** (sidecar): every `lane_use` record matches its
  approach's movements; pocket lanes feed only left turns and U-turns; no
  movement makes a banned turn; every approach with a banned turn has a
  facing 209 / 214 sign; and the banned exit is still reachable from the
  approach through other movements.

### XSD schema

The ASAM XSD can't be bundled because of its licence. Download it from ASAM
and set `OPENDRIVE_XSD=/path/to/opendrive_16_core.xsd`, or pass `--xsd`.
Without it, the check is skipped with a warning.

### Drive-check

```bash
python -m cli drive-check out/<hash>/single_4way.xodr
```

The strongest check. It drives the network with esmini's own lane-following
logic, the same logic its traffic uses, through `libesminiRMLib` loaded with
`ctypes`.

**Every junction movement** in the `.roadmap.json` is driven:

1. **Start** 20 m before the junction in the incoming lane. Where the road is
   short, start at most half the road back, and never in a taper or where
   that lane id is not a driving lane.
2. **Drive** forward in 0.5 m steps through the connecting road and 20 m
   beyond (less where the target lane is dropped sooner, or where its id
   stops being a driving lane). The junction
   selector is aimed at the movement's `turn_angle`, so exits of the same
   kind at a 5-way junction are told apart.
3. **Pass** if all of these hold:
   - the route is incoming road → expected connecting road → expected
     target road;
   - the lane on entering the target road is the expected lane;
   - no step jumps by more than 5 cm. esmini moves 0.5 m along the reference
     line, so a lane far from it covers less ground on the inside of a curve
     and more on the outside. A step is therefore compared with the length of
     the lane-centre path between its two positions, by roadgen's own
     evaluation (onto the next road: to where the first lane ends plus from
     where the next one starts). A gap between two roads still shows;
   - esmini returns no error code. Running off the end of a dead-end target
     road just ends the drive.

At a **lane split**, esmini's selector only knows the turn angle and picks
one lane, so any connecting road of the split that ends in its own lane
passes. Failures are logged with the route esmini actually took. Selector
angles were calibrated against esmini 3.9.

**Every lane that runs a whole normal road** is then driven end to end in
1 m steps (`drive_lanes`). A lane passes if:

- esmini stays on that road and lane through all lane sections;
- esmini never returns an error;
- its position matches the continuity check's lane centre (x, y and height,
  within 5 cm).

This confirms that esmini reads the spirals, lane sections, tapers,
elevation and superelevation exactly as roadgen does.

Both passes share one load of the RoadManager (`drive_all`): on large
networks the load is most of the time (on `mixed_city` about 170 s of the
load against 66 s of driving). The log gives the load's and each pass's wall
and CPU time; a wall time far above the CPU time means the host, not esmini,
was slow. esmini's load itself varies: on some files the same load takes
up to three times as long in some runs, all of it CPU time
([development notes](development.md#esmini-load-time)).

## Metrics

Measurements, not pass/fail checks: `metrics` always succeeds. Thresholds and
parameters are in `defaults.yaml` under `metrics` and `network_metrics`.

### Geometry metrics

```bash
python -m cli metrics out/<hash>/net.xodr --json net_metrics.json
python -m cli metrics --sweep 200 --jobs 4   # lsystem city on maps, seeds 0..199, HD maps
python -m cli metrics --sweep 200 --no-hd --preset mixed_city   # without HD maps
```

How drivable and smooth a network is (`metrics/geometry.py`). It reads only
the `.xodr`, so any network is measured the same way.

- **Lateral acceleration** `v²|κ|` per road at its posted speed.
  - κ is the peak lane-centre curvature: the reference curvature turned into
    `κ / (1 − κt)` at each driving lane's offset `t`, sampled every
    `sample_step`.
  - Reported as percentiles and as the share over `comfort_lat_acc` (3 m/s²).
  - Reported separately for normal roads, connecting roads of two-road
    junctions (`bend`) and connecting roads of real junctions (`turn`).
- **Two-road junctions:** junctions linked to exactly two normal roads.
- **Curvature jumps** in the travel direction, counted above
  `curvature_jump_tol`:
  - across lane links between roads (`links`);
  - across geometry boundaries inside a road (`joins`).

  Roads that only match in position and heading (G1) show up here.
- **Build-failure sweep** (`--sweep N`): generates N networks as `pipeline
  --maps` would and builds each, reporting the ones the builder rejects
  (usually an edge shorter than its junction trims). With HD maps (the
  default) each network first goes through what `pipeline` does before the
  build (`annotate` from the maps, the cross-section fit); `--no-hd` builds
  the plain network. `--jobs N` builds N networks at a time.

### Network metrics

```bash
python -m cli metrics --graph out/<hash>/net.json --json net_network.json
```

How varied a network's layout and junctions are (`metrics/network.py`),
from the RoadGraph alone, for comparison with real-city statistics:

| Metric | Meaning |
|--------|---------|
| **Junction legs** | Nodes with 1 (dead ends), 3, 4, 5, 6 and 7+ roads; two-road nodes are counted apart |
| **Skew** | Per junction, the largest deviation of neighbouring arms from 90° or 180° (0 for T and +, 30° for an even Y, 18° for a regular 5-way star). Also the share skewed by `skew_threshold_deg` (15°) or more, and the smallest arm angle |
| **Offset T pairs** | Two T junctions joined by an edge of at most `offset_t_distance` (50 m), stems on opposite sides |
| **Connected-node ratio** | Intersections / (intersections + dead ends). Real cities: 0.6–1 (Dill; Teng & Bidarra) |
| **Densities** | Intersections and dead ends per km², street length per km², over the nodes' convex hull (Yang et al.) |
| **Orientation** | Entropy of length-weighted street bearings in `orientation_bins` (36) bins, and Boeing's order φ = 1 − ((H − H_grid) / (H_max − H_grid))²: 1 for a perfect grid, 0 for evenly spread bearings |
| **Tortuosity** | Length / chord of every road through its bends, and the share of length on roads at least `straight_tortuosity` (1.01) |
| **Block areas** | Bounded faces of the street lines |
| **Features** | Counts of the `metadata` lists: roundabouts, cul-de-sacs, overpasses, tunnels, interchanges, bridges |
| **Naturalness** | Quality gates: dead-end share; lane or class changes at two-road nodes (where a road just goes on; highway lane adds and a highway running out into an avenue aside); class changes along straight continuations (under 30°) at any node; road bodies (driving width) overlapping by more than 1 m² without a shared node, unless they pass over each other (5 m apart in height over the overlap; on a graph without heights yet, centre lines that cross count as a planned crossing) |

### HD-map metrics

```bash
python -m cli metrics NETWORK.xodr [--json OUT.json]   # HD counts unless --no-hd
```

`roadgen/metrics/hdmap.py`, from the `.xodr` (and its sidecar, if present):

| Metric | Meaning |
|---|---|
| **Lane types** | km of lane per km of normal road, per OpenDRIVE lane type |
| **Road marks** | `<roadMark>` count on normal roads by type and colour; marks on connecting roads counted apart (a junction interior has none) |
| **Signals** | By `country:type[-subtype]`, dynamic ones (lights) apart, per km of road and per junction |
| **Objects** | By object type, per km and per junction (`<bridge>` / `<tunnel>` records are not objects) |
| **Control** | `<controller>`s and their controls, junction controllers, junction `<priority>` records; from the sidecar the junction control mix, signal plans and phases, lane-use records, banned movements |

Baselines of the example networks are in `plans/baselines/`.
