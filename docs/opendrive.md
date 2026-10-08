# OpenDRIVE builder

`roadgen/opendrive/` converts a [`RoadGraph`](road-graph.md) into an ASAM
OpenDRIVE 1.6 file. It only consumes the graph and knows nothing about which
generator made it.

```bash
python -m cli build-xodr tests/fixtures/single_4way.json out/single_4way.xodr
python -m cli view out/single_4way.xodr --density 2
```

`OpenDriveBuilder(name, junction_margin=2.0)`:
- `build(graph)` validates the graph and builds the network in memory.
- `write(path)` writes the `.xodr` and the `.roadmap.json` sidecar.
- After `build()`, `edge_to_road`, `node_to_junction` and `movements` are
  available.

## Network structure

- **Every edge → one normal road** with a single straight `<line>`, placed at
  absolute (x, y, hdg). scenariogeneration's automatic geometry adjustment is
  bypassed.
- **Nodes of degree 1** are dead ends. The road simply ends there.
- **Nodes of degree ≥ 2 → a junction.** Roads are **trimmed back** to the
  junction boundary, and the junction interior is covered only by
  **connecting roads**. A degree-2 node (a bend or a lane-count change) is a
  two-arm junction in which every lane continues.
- The header date is fixed (`1970-01-01T00:00:00`) for reproducible output.
  Road type is `town` with the edge's speed limit.

### Id scheme

1. Edge roads get ids `1..E` in edge-id order.
2. Then, per junction node in node-id order, the junction gets the next id and
   its connecting roads the ids right after it.

All road and junction ids are therefore unique across the file.

## Lanes (`lanes.py`)

Right-hand traffic. Right lanes (negative ids) carry traffic along the
reference line, and left lanes (positive ids) carry traffic against it.

- **Two-way edge:** `lanes_forward` on the right, `lanes_backward` on the left.
- **One-way edge:** all lanes on the right. A backward-only edge gets a
  **reversed reference line** (`oriented_lanes`), so its lanes become forward
  lanes.
- **Road marks:** solid center line, broken lines (3 m dash, 9 m gap) between
  lanes, and a solid line on the outermost edge.

## Junctions (`junctions.py`)

### Trimming: where roads stop

`junction_trims` computes, for each arm, how far from the node its road must
stop:

- Baseline: the widest half cross-section at the node (enough for right-angle
  crossings).
- For each pair of neighbouring arms *θ* apart, with facing edge widths *a*
  and *b*, the road edges cross at *(b + a cos θ)/sin θ* along the first arm
  and *(a + b cos θ)/sin θ* along the second. Each arm is trimmed past every
  such crossing, so **acute junctions never overlap**.
- `junction_margin` (default 2 m, CLI `--junction-margin`) is added as
  clearance.
- Arms closer than **10°** raise an error, because they can't be separated
  sensibly. An edge too short for the trims at both ends also raises an error.

### Movements: which lane connects to which

`plan_movements` creates **one single-lane connecting road per lane
movement**. U-turns are never created.

1. Each exit of an incoming road is classified by its turn angle. **At most
   one exit is `straight`**: the one with the smallest turn, if under 45°.
   The others are `left` or `right` by the sign of the turn. This keeps
   5-way and skewed junctions unambiguous.
2. Standard lane rules:
   - straight: lane-to-lane, from the center outward;
   - right turns: from the rightmost lane into the rightmost lane;
   - left turns: from the leftmost lane into the leftmost lane.
3. **No lane may dead-end.** Any incoming lane still without a movement
   (e.g. lane 2 of a one-way avenue at a T, where straight is impossible) is
   given one by `fill_pair`: straight if possible, otherwise the turn on its
   half of the road, otherwise the other turn. Lanes merge into the outermost
   lane when the target has fewer lanes.
4. **Two-arm junctions** use `continuation_pairs`: every lane continues, and
   surplus lanes merge into the outermost outgoing lane.

### Connecting-road geometry

- The reference line **is the lane center path**. Its single lane `-1` is
  centered with a `laneOffset` of half the lane width. The width blends
  linearly if the two roads' lane widths differ.
- **Straight ahead with the same heading:** a `<line>`.
- **Otherwise:** a **cubic Hermite `paramPoly3`** matching position and
  heading at both ends. Its tangent length is
  *k = chord · 2 tan(θ/4) / sin(θ/2)*, the cubic approximation of a circular
  arc. This keeps the parameter speed nearly uniform even for sharp turns.
  Arc length is computed with 16-point Gauss–Legendre quadrature.
- Each connecting road links predecessor → incoming road and successor →
  outgoing road with the correct `contactPoint`. Its lane links point at the
  exact lanes, and the junction `<connection>` has a matching `laneLink`.

### Lane conventions at a contact point

A road whose reference line **ends** at the junction (contact `end`) brings
traffic in on its right lanes and takes it out on its left lanes. Contact
`start` is the mirror image. Lane lists are ordered from the center line
outward, i.e. from the driver's leftmost lane to the rightmost.

## The `.roadmap.json` sidecar

Written next to every `.xodr` (`sidecar_path()`). Later stages depend on it:
the scenario builder uses it to tell normal roads from connecting roads and
to plan routes, and drive-check uses it to know what to drive.

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
      "turn_angle": 0.0,
      "from_edge": 1, "from_road": 1, "from_lane": -1,
      "to_edge": 2, "to_road": 2, "to_lane": -1
    }
  ]
}
```

`turn_angle` is the heading change in radians (counter-clockwise positive,
in (−π, π]).

After writing, the CLI always runs the [validators](validation.md) on the file.
