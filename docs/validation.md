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
**0.01 rad in travel heading**. A connecting-road lane without a lane link is
also an error.

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

1. Place a position 20 m before the junction in the incoming lane.
2. Move forward in 0.5 m steps for 60 m. The junction selector is aimed at
   the movement's `turn_angle`, so exits of the same kind at a 5-way junction
   are told apart.
3. The movement **passes** if:
   - the route is incoming road → expected connecting road → expected target
     road;
   - the lane on entering the target road is the expected lane;
   - no step jumps by more than 5 cm;
   - esmini never returns an error code.

Failures are logged with the route esmini actually took. Selector angles were
calibrated empirically against esmini 3.9.
