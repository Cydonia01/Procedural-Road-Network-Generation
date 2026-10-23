# Validation

There are five independent checks: one runs on the RoadGraph, three run on
the written `.xodr`, and one drives the network with esmini's own road logic.
`pipeline` runs all of them and fails if any reports an error.

| Check | Runs on | When | Module |
|-------|---------|------|--------|
| Graph validity | RoadGraph | always (`pipeline`, `build-xodr`) | `graph/model.py` → `RoadGraph.validate()` |
| Traffic connectivity | RoadGraph | always. Errors in `pipeline` unless `--allow-traps`, warnings in `build-xodr` | `validate/connectivity.py` |
| Topology | `.xodr` | always | `validate/topology.py` |
| Continuity | `.xodr` | always | `validate/continuity.py` |
| XSD schema | `.xodr` | only if `OPENDRIVE_XSD` / `--xsd` is set | `validate/schema.py` |
| Drive-check | `.xodr` + sidecar | `drive-check`, or `pipeline --drive-check` | `sim/drive_check.py` |

## Traffic connectivity

*Can every car get everywhere, and can no car get stuck?*

The check builds a **traversal graph**. Its vertices are directed traversals
*(edge, direction)* that have lanes in that direction. A traversal arriving
at a node continues onto any *other* edge leaving it, with no U-turns. This
mirrors the movements the builder creates.

Reported as errors:

- **Traps:** traffic arriving on an edge has no way out (dead ends, one-way
  sinks). Vehicles would get stuck.
- **Unreachable roads:** traffic leaving on an edge that nothing ever feeds.
- **Not strongly connected:** the node-level lane-direction graph has several
  strongly connected components. The nodes outside the main component are
  listed.

Groups that differ only by direction of travel are logged but not reported.
For example, on a pure loop, reversing would need a U-turn, which strands no
vehicle.

Typical fixes are `--boundary loop` for one-way Manhattan grids, or
`--allow-traps` when dead ends are intended (organic L-system networks).

## Topology

Structural integrity of the `.xodr`:

- road and junction ids are unique, and connection ids within each junction
  are unique;
- every road `predecessor`/`successor` points to an existing road or junction;
- road-to-road lane links name lanes that exist at the linked contact end;
- junction connections reference existing incoming and connecting roads, the
  connecting road belongs to that junction, the incoming road actually links
  to the junction, and every `laneLink` from/to lane exists.

## Continuity

Lane-level geometric continuity, evaluated **independently of the builder**.
The check parses the XML and evaluates `line`, `arc` and `paramPoly3`
geometry, `laneOffset` and polynomial lane widths itself.

For every lane with a predecessor or successor link to another road, the lane
center must match at the contact point within **1 cm in position** and
**0.01 rad in travel heading**. Its **height** (from `<elevationProfile>`;
0 without one) must match within 1 cm, and its **grade** in the travel
direction within 0.01. A connecting-road lane without a lane link is also an
error.

## XSD schema

The ASAM XSD can't be bundled because of its license. Download it from ASAM
and set `OPENDRIVE_XSD=/path/to/opendrive_16_core.xsd` or pass `--xsd`.
Without it, the check is skipped with a warning.

## Drive-check (esmini RoadManager)

```bash
python -m cli drive-check out/single_4way.xodr
```

This is the strongest check. It uses esmini's own lane-following logic, the
same logic its traffic uses, through `libesminiRMLib` loaded with `ctypes`.

For every movement in the `.roadmap.json`:

1. Place a position 20 m before the junction in the incoming lane (at most
   half the road, as roads between close junctions can be shorter).
2. Move forward in 0.5 m steps through the connecting road and 20 m beyond.
   The junction selector is aimed at the movement's `turn_angle`, so exits of
   the same kind at a 5-way junction are told apart.
3. The movement **passes** if:
   - the route is incoming road → expected connecting road → expected target
     road;
   - the lane on entering the target road is the expected lane;
   - no step jumps by more than 5 cm;
   - esmini never returns an error code. Running off the end of a dead-end
     target road only ends the drive early.

Failures are logged with the route esmini actually took. Selector angles were
calibrated empirically against esmini 3.9.

## Geometry metrics

```bash
python -m cli metrics out/net.xodr --json out/net_metrics.json
python -m cli metrics --sweep 200            # lsystem city + maps, seeds 0..199
```

Not a pass/fail check but a measurement of how realistic the geometry is
(`metrics/geometry.py`), used to track the stage A work in
`plans/2026-10-23-stage-a-geometry-implementation.md`. It reads only the
`.xodr`, so any network is measured the same way.

- **Lateral acceleration** `v² |κ|` per road at its posted `<type><speed>`,
  with `κ` the peak lane-centre curvature (reference curvature `κ` turned
  into `κ / (1 − κ t)` at each driving lane's offset `t`, sampled every
  `sample_step`). Reported as percentiles and the share over
  `comfort_lat_acc` (3 m/s²), separately for normal roads, connecting roads
  of junctions that join only two roads (`bend`: bends and lane-count
  changes) and connecting roads of real junctions (`turn`).
- **Two-road junctions:** junctions linked to exactly two normal roads.
- **Curvature jumps** in the travel direction across every lane link between
  roads (`links`) and across geometry boundaries inside a road (`joins`),
  counted above `curvature_jump_tol`. Roads that meet with only matching
  position and heading (G1) show up here.
- **Build-failure sweep** (`--sweep N`): generates N networks as
  `pipeline --maps` would and runs the builder on each, reporting the ones
  it rejects (typically an edge shorter than the junction trims at its ends).
