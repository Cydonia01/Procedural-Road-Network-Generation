# OpenDRIVE builder

`roadgen/opendrive/` converts a [`RoadGraph`](road-graph.md) into an ASAM
OpenDRIVE 1.6 file. It only consumes the graph and knows nothing about which
generator made it.

```bash
python -m cli build-xodr tests/fixtures/single_4way.json single_4way.xodr
python -m cli view out/<hash>/single_4way.xodr --density 2
```

`OpenDriveBuilder(name, junction_margin=2.0, design=DesignParams())`:
- `build(graph)` validates the graph and builds the network in memory.
- `write(path)` writes the `.xodr` and the `.roadmap.json` sidecar.
- After `build()`, `alignment` (the fitted
  [`NetworkAlignment`](geometry.md#chains-and-fitted-roads)), `edge_to_road`,
  `edge_s_range`, `node_to_junction` and `movements` are available.

## Network structure

The road shape comes from the neutral [geometry package](geometry.md); the
builder only turns it into XML.

- **Chains → roads.** A node with exactly two roads that the road simply
  continues through (a bend or a lane-count change) is not a junction: the
  edges on both sides are merged into one **chain**, and every chain becomes
  one normal road (see [chains](geometry.md#chains-and-fitted-roads) for the
  exact rule; `Node.kind = "junction"` keeps a node a junction).
- **Plan view:** the chain's fitted `<line>`, `<spiral>` and `<arc>`
  geometries, placed at absolute (x, y, hdg). Every bend is a symmetric
  spiral–arc–spiral corner sized for the design speed, or shrunk to fit with
  an advisory speed (see [corner fitting](geometry.md#chains-and-fitted-roads)).
  Curvature is continuous along the whole road. scenariogeneration's
  automatic geometry adjustment is bypassed.
- **Nodes of degree 1** are dead ends. The road simply ends there.
- **Chain ends with two or more roads → a junction.** Roads are **trimmed
  back** to the junction boundary, and the junction interior is covered only
  by **connecting roads**. Chain ends stay straight, so junction geometry is
  as before. Two-road nodes that can't be merged (turning more than 135°, a
  one-way meeting a two-way road, `kind = "junction"`, or the middle of a road
  that would otherwise start and end at the same junction) stay two-arm
  junctions in which every lane continues.
- **Speeds:** `<type>` records with each edge's speed limit, lowered to a
  corner's **advisory speed** from a deceleration lead before the corner
  (at 1.5 m/s²) to its end. Speeds use the innermost lane's radius.
- The header date is fixed (`1970-01-01T00:00:00`) for reproducible output.
  Road type is `town`.
- **Heights** are only written if the graph has any (`RoadGraph.has_elevation`,
  set by [drape](maps.md#heights-from-the-maps-to-esmini)). Each road's
  `<elevationProfile>` is a monotone cubic Hermite fit (PCHIP) through all
  height samples of its chain, placed by station; bend nodes are ordinary
  knots, and the road ends on its junction's sloped **junction plane**
  (`geometry/vertical.py`, see [maps](maps.md#heights-from-the-maps-to-esmini)).
  Connecting roads lie on the plane, so all roads of a junction meet at the
  same height, grade and roll.
- **Banking** (`<lateralProfile><superelevation>`, `design.banking`, on by
  default): every corner rolls towards its centre, from 0 at its tangent
  points to `atan(e)` on its arc (AASHTO method 5, `e_max` 0.04 for streets
  and avenues, 0.06 for highways), with a smoothstep runoff along its
  spirals so the roll rate is 0 where curves start and end. Within 30 m of a
  sloped junction the road rolls to the plane's cross slope. Design radii
  count on this banking. esmini evaluates it as roadgen does: a lane centre
  `t` across the road surface, which is pitched by the grade and rolled
  about its pitched tangent. `Edge.bridges` become `<objects><bridge type="concrete">` records;
  spans that touch across a bend node are merged.

### Id scheme

1. Chain roads get ids `1..C` in chain order (by each chain's smallest edge
   id).
2. Then, per junction node in node-id order, the junction gets the next id and
   its connecting roads the ids right after it.

All road and junction ids are therefore unique across the file.

## Lanes (`lanes.py`)

Right-hand traffic. Right lanes (negative ids) carry traffic along the
reference line, and left lanes (positive ids) carry traffic against it.

- **Two-way chain:** the reference line runs along the chain's lowest-id
  edge, so a single-edge road keeps its edge's direction. Each edge's lanes
  along the chain go right, the others left.
- **One-way chain:** all lanes on the right; the reference line runs in the
  travel direction, so a backward-only edge gets a reversed reference line.
- **Lane sections** (`make_lane_sections`, from
  [`geometry/profiles.py`](geometry.md#along-road-profiles)): a new section
  wherever the lane count or width changes along a chain. A dropped lane
  narrows to 0 before the node, an added lane widens from 0 after it, and a
  width change blends after it, each as a cubic smoothstep over the MUTCD
  taper length (56 m for a 3.5 m lane at 50 km/h), at most half the edge.
  Lanes are linked by position across section boundaries; a lane that
  narrows to 0 or widens from 0 at a boundary has no link there.
- **Road marks:** solid center line, broken lines (3 m dash, 9 m gap) between
  lanes, and a solid line on the outermost edge.

## Junctions (`junctions.py`)

### Trimming: where roads stop

`junction_trims` (in the neutral `roadgen/geometry/junction_space.py`, so
the ground mesh and generators can use it too) computes, for each arm, how
far from the node its road must stop:

- Baseline: the widest half cross-section at the node (enough for right-angle
  crossings).
- For each pair of neighbouring arms *θ* apart, with facing edge widths *a*
  and *b*, the road edges cross at *(b + a cos θ)/sin θ* along the first arm
  and *(a + b cos θ)/sin θ* along the second. Each arm is trimmed past every
  such crossing, so **acute junctions never overlap**. A crossing counts only
  if it lies on both arms (both distances positive); otherwise the surfaces
  overlap only within the baseline. Without that rule, near-straight arms of
  different widths got trims of hundreds of metres and failed to build.
- `junction_margin` (default 2 m, CLI `--junction-margin`) is added as
  clearance.
- Arms closer than **10°** raise an error, because they can't be separated
  sensibly. An edge too short for the trims at both ends also raises an error.

### Movements: which lane connects to which

`plan_movements` creates **one single-lane connecting road per lane
movement**. U-turns are only created with `opendrive.u_turns: true`, at
junctions of three or more roads, from the innermost incoming lane into the
first outgoing lane of the same road whose centre is at least
2 · `u_turn_radius` (5 m) away: with 3.5 m lanes, only roads with three or
more lanes each way. Elsewhere roundabouts and cul-de-sac bulbs turn traffic
around. U-turns are fitted as clothoids like the other turns, get their
speed from their curvature, and esmini drives them (selector angle π).

1. Each exit of an incoming road is classified by its turn angle. **At most
   one exit is `straight`**: the one with the smallest turn, if under 45°.
   The others are `left` or `right` by the sign of the turn. This keeps
   5-way and skewed junctions unambiguous.
2. Standard lane rules:
   - straight: lane-to-lane, from the center outward. Where the exit has
     more lanes than the entry, a **lane split** feeds the extra lanes
     (stage C5, JunctionArt): with `opendrive.lane_split: outer` (default)
     the outermost incoming lane fans out to them; `inner` makes the
     innermost one split and shifts the others outward, so no two
     connecting roads of one approach cross; `none` leaves them to the turns
     of other approaches;
   - right turns: from the rightmost lane into the rightmost lane;
   - left turns: from the leftmost lane into the leftmost lane.
3. **No lane may dead-end.** Any incoming lane still without a movement
   (e.g. lane 2 of a one-way avenue at a T, where straight is impossible) is
   given one by `fill_pair`: straight if possible, otherwise the turn on its
   half of the road, otherwise the other turn. Lanes merge into the outermost
   lane when the target has fewer lanes.
4. **Two-arm junctions** use `continuation_pairs`: every lane continues, and
   surplus lanes merge into the outermost outgoing lane.

**Ramp nodes** (`Node.kind = "ramp"`, stage C9): where interchange ramps
leave or join a highway, a junction keeps only straight-through movements,
right exits and right merges: no left turns across the centre line, no
movement from one one-way road (ramp) to another, and no lane split. Only
turns within 5° count as straight there, so a ramp joining at 15–25° merges
into the outermost lane as a right turn.

Junctions with up to 7 roads and arms down to the generators' minimum
angle (30° by default) build, validate and drive (tests in
`tests/test_junction_variety.py`: 5-, 6- and 7-leg stars, a 30° Y, a K,
skewed crossings, lane splits, offset T pairs). Junction options live in
`JunctionParams` (`opendrive:` in `defaults.yaml`), passed to
`OpenDriveBuilder(junctions=...)`.

### Connecting-road geometry

- The reference line **is the lane center path**. Its single lane `-1` is
  centered with a `laneOffset` of half the lane width. The width blends
  linearly if the two roads' lane widths differ.
- **Straight ahead with the same heading:** a `<line>`.
- **Otherwise:** **three clothoids** (`<spiral>`, the middle one an `<arc>`
  when its curvature is constant) from G2 Hermite interpolation
  ([`g2_hermite`](geometry.md#clothoid-transitions-clothoidpy), pyclothoids'
  `SolveG2`): they match position and heading at both ends and start and end
  with zero curvature, like the straight road ends they join, so the
  curvature is continuous into and out of every junction.
- **Fallback** when the solver finds no good path (it never happened on the
  example networks): a cubic Hermite `paramPoly3` matching position and
  heading only, with tangent length *k = chord · 2 tan(θ/4) / sin(θ/2)*, the
  cubic approximation of a circular arc. Each movement records its `path`
  (`line`, `clothoid` or `poly3`) in the sidecar, and the builder logs the
  number of fallbacks.
- **Speed:** the lower speed limit of the two roads, or, where the turn's
  tightest radius can't be driven at that within 3 m/s² (flat junction),
  that radius's advisory speed (about 20–25 km/h for an urban right turn).
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
  ],
  "geometry_version": 2,
  "edge_s_range": {"1": [1, 0.0, 74.5]},
  "corners": []
}
```

- `edge_to_road` maps every edge to its chain road, so several edges can map
  to one road; `edge_s_range` gives each edge's station range `[road, s0,
  s1]` on it.
- `corners` lists every fitted bend: `road`, `node`, `s_start`, `s_end`,
  reference `radius`, innermost `lane_radius`, `design_speed`, `max_speed`
  and `advisory_speed` (null when the corner has the design radius), all in
  m and m/s.
- `geometry_version` is 2 for chain roads (1, implicit, was one straight road
  per edge).
- `turn_angle` is the heading change in radians (counter-clockwise positive,
  in (−π, π]).

After writing, the CLI always runs the [validators](validation.md) on the file.
