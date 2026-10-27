# OpenDRIVE builder

`roadgen/opendrive/` converts a [RoadGraph](road-graph.md) into an ASAM
OpenDRIVE 1.6 file. It only consumes the graph and knows nothing about which
generator made it. The road *shape* (chains, fitted curves, trims, profiles)
comes from the neutral [geometry package](geometry.md), and heights come from
[Heights](heights.md). The builder turns these into XML.

```bash
python -m cli build-xodr tests/fixtures/single_4way.json
python -m cli view out/<hash>/single_4way.xodr --density 2
```

`OpenDriveBuilder(name, junction_margin=2.0, design=DesignParams(), junctions=JunctionParams())`:

- `build(graph)` validates the graph and builds the network in memory.
- `write(path)` writes the `.xodr` and the `.roadmap.json` sidecar.
- After `build()`, the following are available: `alignment` (the fitted
  `NetworkAlignment`), `edge_to_road`, `edge_s_range`, `node_to_junction` and
  `movements`.

After writing, the CLI always runs the [validators](validation.md).

## Roads

- **One road per chain.** Edges through bends and lane-count changes are
  merged into [chains](geometry.md#chains), and each chain becomes one normal
  road.
- **Plan view.** The chain's fitted `<line>`, `<spiral>` and `<arc>`
  geometries, placed at absolute (x, y, hdg). Curvature is continuous along
  the whole road. scenariogeneration's automatic geometry adjustment is
  bypassed.
- **Ends.**
  - **Dead end** (degree 1): the road simply ends.
  - **Junction** (degree ≥ 3): the road is trimmed back to the junction
    boundary, and connecting roads cover the interior.
  - **Two-arm junction:** a two-road node that can't be merged; every lane
    continues through it.
- **Speeds.** `<type>` records carry each edge's speed limit. It is lowered
  to a corner's advisory speed from a deceleration lead before the corner to
  its end ([speed profile](geometry.md#along-road-profiles)).
- **Heights.** `<elevationProfile>`, `<lateralProfile><superelevation>`,
  `<bridge>` and `<tunnel>`, only when the graph has heights ([Heights](heights.md#4-in-the-builder-vertical-fit-junction-planes-banking)).
- **Fixed fields.** The header date is fixed (`1970-01-01T00:00:00`) for
  reproducible output. Road type is `town`.

**Id scheme.** All road and junction ids are unique across the file:

1. Chain roads get ids `1..C` in chain order (by each chain's smallest edge
   id).
2. Then, for each junction node in node-id order, the junction gets the next
   id and its connecting roads the ids right after it.

## Lanes

`lanes.py`. Traffic is right-hand: right lanes (negative ids) drive along the
reference line, and left lanes (positive ids) drive against it.

- **Two-way chain.** The reference line runs along the chain's lowest-id edge,
  so a single-edge road keeps its edge's direction. Each edge's lanes along
  the chain go right, the others left.
- **One-way chain.** All lanes are on the right, and the reference line runs
  in the travel direction. A backward-only edge therefore gets a reversed
  reference line.
- **Lane sections** (`make_lane_sections`, from the
  [lane layout](geometry.md#along-road-profiles)). A new section starts
  wherever the lane count or width changes.
  - Lanes are linked by position across section boundaries.
  - A lane that narrows to 0 or widens from 0 at a boundary has no link there.
- **Road marks.** A solid centre line, broken lines between lanes (3 m dash,
  9 m gap), and a solid line on the outermost edge.

**At a contact point.** A road whose reference line **ends** at a junction
(contact `end`) brings traffic in on its right lanes and takes it out on its
left lanes. Contact `start` is the mirror image. Lane lists are ordered from
the centre line outward, i.e. from the driver's leftmost lane to the
rightmost.

## Junctions

`junctions.py`. Junction options live in `JunctionParams` (`opendrive:` in
`defaults.yaml`).

### Movements: which lane connects to which

`plan_movements` creates **one single-lane connecting road per lane
movement**:

1. **Classify exits.** Each exit of an incoming road is classified by its turn
   angle. **At most one exit is `straight`**: the one with the smallest turn,
   if under 45°. The others are `left` or `right` by the sign of the turn.
   This keeps 5-way and skewed junctions unambiguous.
2. **Standard lane rules:**
   - **straight:** lane to lane, from the centre outward. Where the exit has
     more lanes than the entry, a **lane split** feeds the extra lanes
     (JunctionArt), set by `lane_split`:
     - `outer` (default): the outermost incoming lane fans out to them;
     - `inner`: the innermost lane splits and the others shift outward, so no
       two connecting roads of one approach cross;
     - `none`: the extra lanes are left to the turns of other approaches.
   - **right turns:** rightmost lane into rightmost lane;
   - **left turns:** leftmost lane into leftmost lane.
3. **No lane may dead-end.** `fill_pair` gives every incoming lane still
   without a movement one: straight if possible, otherwise the turn on its
   half of the road, otherwise the other turn. An example is lane 2 of a
   one-way avenue at a T. Lanes merge into the outermost lane when the target
   has fewer lanes.
4. **Two-arm junctions** use `continuation_pairs`: every lane continues, and
   surplus lanes merge into the outermost outgoing lane.

**U-turns** are created only with `u_turns: true`.

- **Where:** at junctions of three or more roads, from the innermost incoming
  lane into the first outgoing lane of the same road whose centre is at least
  2 · `u_turn_radius` (5 m) away. With 3.5 m lanes, that means roads with
  three or more lanes each way.
- **Elsewhere,** roundabouts and cul-de-sac bulbs turn traffic around.
- **How:** U-turns are fitted like other turns and get their speed from their
  curvature. esmini drives them with selector angle π.

### Ramp nodes

At a `Node.kind = "ramp"` node, where interchange ramps leave or join a
highway, a junction keeps only straight-through movements, right exits and
right merges:

- no left turns across the centre line;
- no movement from one one-way road (ramp) to another;
- no lane split.

Only turns within 5° count as straight there, so a ramp joining at 15–25°
merges into the outermost lane as a right turn.

Junctions with up to 7 roads, and arms down to 30° apart, build, validate
and drive. `tests/test_junction_variety.py` covers 5-, 6- and 7-leg stars, a
30° Y, a K, skewed crossings, lane splits and offset T pairs.

### Connecting roads

- **Lane.** The reference line *is* the lane centre path. Its single lane `-1`
  is centred with a `laneOffset` of half the lane width. The width blends
  linearly if the two roads' lane widths differ.
- **Straight ahead, same heading:** a `<line>`.
- **Otherwise:** **three clothoids** (`<spiral>`, the middle one an `<arc>`
  when its curvature is constant) from [`g2_hermite`](geometry.md#curve-pieces-and-clothoids).
  They match position and heading at both ends and start and end with zero
  curvature, like the straight road ends they join. Curvature is therefore
  continuous into and out of every junction.
- **Fallback.** If no clothoid fits (this hasn't happened on the example
  networks), a cubic Hermite `paramPoly3` matches position and heading only.
  - Its tangent length is *k = chord · 2 tan(θ/4) / sin(θ/2)*, the cubic
    approximation of a circular arc.
  - Each movement records its `path` (`line`, `clothoid` or `poly3`) in the
    sidecar, and the builder logs the number of fallbacks.
- **Speed.** The lower speed limit of the two roads. If the turn's tightest
  radius can't be driven at that within 3 m/s², the radius's advisory speed is
  used instead: about 20–25 km/h for an urban right turn.
- **Links.** A connecting road links predecessor → incoming road and
  successor → outgoing road with the correct `contactPoint`. Its lane links
  point at the exact lanes, and the junction `<connection>` has a matching
  `laneLink`.

## The `.roadmap.json` sidecar

Written next to every `.xodr`. Later stages depend on it:

- the scenario builder uses it to tell normal roads from connecting roads and
  to plan routes;
- drive-check uses it to know what to drive.

Abridged output for `tests/fixtures/single_4way.json` (4 edges, 1 junction,
12 movements):

```json
{
  "xodr": "single_4way.xodr",
  "edge_to_road": {"1": 1, "2": 2, "3": 3, "4": 4},
  "node_to_junction": {"1": 5},
  "movements": [
    {
      "junction_id": 5, "connecting_road": 6, "kind": "straight",
      "turn_angle": 0.0, "path": "line",
      "from_edge": 1, "from_road": 1, "from_lane": -1,
      "to_edge": 2, "to_road": 2, "to_lane": -1
    }
  ],
  "geometry_version": 2,
  "edge_s_range": {"1": [1, 0.0, 74.5]},
  "corners": []
}
```

| Key | Meaning |
|-----|---------|
| `edge_to_road` | Each edge's chain road; several edges can map to one road |
| `edge_s_range` | Each edge's station range on its road: `[road, s0, s1]` |
| `node_to_junction` | Junction id of each junction node |
| `movements` | Every connecting road: kind, `turn_angle` (radians, counter-clockwise positive, in (−π, π]), `path`, and from / to edge, road and lane |
| `corners` | Every fitted bend: `road`, `node`, `s_start`, `s_end`, reference `radius`, innermost `lane_radius`, `design_speed`, `max_speed`, `advisory_speed` (null at design radius), in m and m/s |
| `geometry_version` | 2 for chain roads (1 was one straight road per edge) |
