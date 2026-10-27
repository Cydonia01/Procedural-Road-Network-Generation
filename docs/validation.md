# Validation and metrics

## Checks

Independent checks run on the RoadGraph, on the written `.xodr`, and by
driving the network with esmini's own road logic. `pipeline` and
`build-xodr` run all of them except drive-check (opt-in), and fail if any
reports an error.

| Check | Runs on | When | Code |
|-------|---------|------|------|
| [Graph validity](road-graph.md#roadgraph) | RoadGraph | always | `RoadGraph.validate()` |
| [Traffic connectivity](#traffic-connectivity) | RoadGraph | always; errors in `pipeline` unless `--allow-traps`, warnings in `build-xodr` | `validate/connectivity.py` |
| [Topology](#topology) | `.xodr` | always | `validate/topology.py` |
| [Continuity](#continuity) | `.xodr` | always | `validate/continuity.py` |
| [Curvature continuity](#curvature-continuity) | `.xodr` | always (`paramPoly3` jumps are warnings) | `validate/continuity.py` |
| [Lateral acceleration](#lateral-acceleration) | `.xodr` | always | `validate/dynamics.py` |
| [Clearance](#clearance) | `.xodr` | always | `validate/clearance.py` |
| [XSD schema](#xsd-schema) | `.xodr` | only with `OPENDRIVE_XSD` or `--xsd` | `validate/schema.py` |
| [Drive-check](#drive-check) | `.xodr` + sidecar | `drive-check`, `pipeline --drive-check` | `sim/drive_check.py` |

### Traffic connectivity

*Can every car get everywhere, and can no car get stuck?*

The check builds a **traversal graph**:

- its vertices are directed traversals *(edge, direction)* that have lanes in
  that direction;
- a traversal arriving at a node continues onto any *other* edge leaving it,
  with no U-turns, mirroring the movements the builder creates.

Reported as errors:

- **Traps:** traffic arriving on an edge has no way out (dead ends, one-way
  sinks), so vehicles would get stuck.
- **Unreachable roads:** traffic leaving on an edge that nothing feeds.
- **Not strongly connected:** the node-level lane-direction graph has several
  strongly connected components. Nodes outside the main one are listed.

Groups that differ only by travel direction are logged, not reported. On a
pure loop, for example, reversing would need a U-turn, but no vehicle is
stranded.

**Fixes:**

- `--set boundary=ring` for one-way grids;
- `--set culdesac_prob=1` for turn-around bulbs at dead ends, where they fit;
- `--allow-traps` when dead ends are intended.

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

A connecting-road lane without a lane link is an error.

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

### Clearance

Two normal roads (not connecting roads) whose reference lines cross in plan
view never meet, so one must pass over the other. At every such crossing,
their reference heights must differ by at least
`grade_separation.clearance` minus the 1 m deck, i.e. 5 m. This checks the
[overpasses](heights.md#3-grade-separation-overpasses).

Contacts within 1 m of a road's end don't count. A dead end reaching
another road is the generator's layout, e.g. a dead end stopping right next
to a street that grew past it later.

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
   short, start at most half the road back, and never in a taper.
2. **Drive** forward in 0.5 m steps through the connecting road and 20 m
   beyond (less where the target lane is dropped sooner). The junction
   selector is aimed at the movement's `turn_angle`, so exits of the same
   kind at a 5-way junction are told apart.
3. **Pass** if all of these hold:
   - the route is incoming road → expected connecting road → expected
     target road;
   - the lane on entering the target road is the expected lane;
   - no step jumps by more than 5 cm;
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

## Metrics

Measurements, not pass/fail checks: `metrics` always succeeds. Thresholds and
parameters are in `defaults.yaml` under `metrics` and `network_metrics`.

### Geometry metrics

```bash
python -m cli metrics out/<hash>/net.xodr --json net_metrics.json
python -m cli metrics --sweep 200            # lsystem city on maps, seeds 0..199
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
  (usually an edge shorter than its junction trims).

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

Baselines of the example networks are in `plans/baselines/`.
