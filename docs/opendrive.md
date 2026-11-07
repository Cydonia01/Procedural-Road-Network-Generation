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
  wherever a driving lane's count or width changes. Side lanes and medians
  run the whole road, 0 m wide where they are absent, and keep their own
  width changes (curb extensions, tapers) inside a section as several
  `<width>` records, so they add no sections. Driving lanes keep one width
  record per section, which the drive-check and the scenario's lane slots
  rely on.
  - Lanes are linked by **role** across section boundaries: the same kind and
    position in the [lane layout](geometry.md#along-road-profiles). A side lane
    outside a driving lane that ends keeps its links while its id shifts
    inward.
  - A lane that narrows to 0 or widens from 0 at a boundary has no link there.
- **Side lanes** (HD maps on, [cross-sections](geometry.md#cross-sections)).
  Outside the driving lanes, as OpenDRIVE lane types `biking`, `parking`,
  `shoulder`, `curb`, `sidewalk` and `border`. Curbs and sidewalks are raised
  0.15 m with `<height>`, which esmini draws as a curb face.
  - **Ending before junctions:** parking tapers out (5 m) to end 10 m before
    a junction (a curb extension); shoulders (urban and suburban profiles,
    and at ramp nodes) and bike lanes before a roundabout entry narrow to
    0 m at the road's end.
  - **Bridges and tunnels:** parking stops over bridges; shoulders narrow to
    1 m in tunnels.
  - **One-way roads** have them on both edges: those on the reference line's
    left edge are positive lanes.
  - **Loops** (roundabout rings, cul-de-sac bulbs) have only curb and
    sidewalk outside and a curb on the island side.
- **Medians** (`median` lanes) sit next to the centre line on two-way roads
  with 2 or more lanes each way (2 m on highways). They taper out
  (MUTCD shift taper) to end 5 m before every junction. The median lane runs
  the whole road, 0 m wide where there is no median, so the driving lanes
  keep their ids (−2, −3, … and 2, 3, …) along the road. Anything that
  follows a lane by id (esmini's drive-check, the scenario's routes) thus
  stays on the same lane. Junction connections start from the driving lanes.
- **Left-turn pockets** (HD maps on, `turn_lanes` section;
  [details](#lane-use-pockets-and-banned-turns-hd-maps)): an innermost lane
  on a two-way road's approach to a junction, between the median and the
  driving lanes. It widens from 0 over 15 m (`pocket_taper`) and keeps the
  lane width over the last 25 / 40 / 60 m (`pocket_length`, street / avenue
  / highway). Like the median, its position runs the whole road: type
  `none` and 0 m wide elsewhere, so the driving lanes keep their ids.
- Only **driving** lanes get movements, scenario spawn slots and routes;
  esmini's own traffic (odrviewer) also keeps to driving-type lanes.
- **Corner roads** (`junctions.py::corner_roads`). Where two neighbouring arms
  of a junction both have side lanes at the junction, a connecting road with
  only those lanes (bike lane, curb, sidewalk, shoulder) runs around the
  corner and links to them.
  - **Reference line:** the right turn from the first arm's outermost
    incoming lane to the next arm's outermost outgoing lane (the same path as
    that movement), with a `laneOffset` of half the turn lane's width. The
    curb starts exactly at the turn lane's outer edge, so the junction
    surface has no stretch without a lane, which CARLA and OSI would see as
    a hole.
  - **Widths** blend smoothly (zero slope at both ends) from one arm's side
    lanes to the other's. A lane only one arm has narrows to 0 m and has no
    link at that end.
  - **Without that turn** (no incoming lanes on the first arm or no outgoing
    lanes on the next, as between one-way roads) the path joins the two
    carriageway edges. The surface between the curb and the movements then
    stays without a lane.
  - The sidecar lists them under `corner_roads`, with `closes` true where a
    movement drives the turn. They are not movements: drive-check, scenarios
    and esmini's traffic never use them.
  - **esmini loads them slowly:** on `city` they are 5 % of the lane length
    but 27 s of its 57 s load (`plans/scripts/b9_osi_points.py`).
    `cross_section.corner_roads: false` (`--no-corner-roads`) leaves them
    out for throughput benchmarks.
- **Road marks** (without HD maps). A solid centre line, broken lines between
  lanes (3 m dash, 9 m gap), and a solid line on the outermost driving lane's
  edge.

### Road marks (HD maps)

`marks.py`, with HD maps on (`road_marks` section; with `--no-hd` the plain
marks above). The German catalogue (StVO, RMS), all
white. A lane's mark lies on its outer border, the centre lane's is the
centre line. Marks change along a lane with the road class of the edge under
it and near junctions, as several `<roadMark sOffset>` records in one lane
section.

| Where | Mark |
|---|---|
| Centre line, two-way road | broken (Z 340): 3 m line / 6 m gap on streets and avenues, 6 / 12 m on highways |
| ... on a street in the `urban` or `suburban` profile (`no_street_centre`) | none (German residential practice); `no_street_centre: []` gives them one |
| ... with 2 or more lanes each way (`solid_centre_lanes`) | solid (Z 295) |
| ... with a median lane | none where the median is wider than 0 m (its own edge lines replace it); `solid solid` (two explicit 0.15 m lines, 0.15 m apart) where it is 0 m wide, before junctions |
| Centre lane of a one-way road | solid: the carriageway's left edge |
| Median lane | solid edge line where it is wider than 0 m |
| Between driving lanes | broken (`laneChange="both"`) |
| No-lane-change zone | lane lines and a broken centre line become solid with `laneChange="none"` over the last 20 / 30 / 50 m (street / avenue / highway, `no_change_length`) of each approach to a junction of 3 or more roads, in the travel direction; across lane-section boundaries, and over the whole road if it is shorter |
| Outermost driving lane | solid edge line, whatever lies outside it (it is also a bike lane's line on its driving side) |
| Side lanes (parking, bike, shoulder, curb, sidewalk, border) | none |

Widths: 0.12 m on streets and avenues, 0.15 m on highways (`width`).

Curbs get no `curb` road mark: esmini's OSI ground truth turns that mark
into a solid white lane boundary, not a curb boundary. The `curb` lane with
its `<height>` describes the curb.
Connecting roads, corner roads included, have no marks: junction interiors
are unmarked, which the [HD-map check](validation.md#hd-map-content) enforces.

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
     (JunctionArt): the outermost incoming lane fans out to them. At ramp
     nodes the extra lanes are left to the merging ramp.
   - **right turns:** rightmost lane into rightmost lane;
   - **left turns:** leftmost lane into leftmost lane.
3. **No lane may dead-end.** `fill_pair` gives every incoming lane still
   without a movement one: straight if possible, otherwise the turn on its
   half of the road, otherwise the other turn. An example is lane 2 of a
   one-way avenue at a T. Lanes merge into the outermost lane when the target
   has fewer lanes.
4. **Two-arm junctions** use `continuation_pairs`: every lane continues, and
   surplus lanes merge into the outermost outgoing lane.

### Lane use, pockets and banned turns (HD maps)

- **Lane use** (`lane_use`): the movement kinds each incoming lane feeds,
  per approach in the sidecar (`lane_use`, with its pocket lanes). Without
  pockets and bans the movements are those above, unchanged.
- **Pockets** (`cross_section.pocket_ends`, decided from the graph before the
  trims, which count the pocket's width): on approaches of the
  `pocket_classes` (avenues and highways) that have a left exit, at
  junctions of three or more roads that are not ramp nodes or roundabout
  entries and whose `Node.control` is not `none` or `right_before_left`, where
  the edge has room for the pocket, its taper and 40 m beyond. Where a pocket
  would leave a neighbouring block too short for its trims, the pocket is
  left out rather than the block repaired (graph metadata `no_pockets`,
  [`repair`](generators.md#repair-edges-too-short-for-their-junctions)), as real designs do on short
  blocks. A pocket feeds
  only the left turns (and a U-turn); the through lanes then feed no left
  turn. At lights each pocket gets its own light and its own protected phase
  ([lights](#traffic-lights-and-controllers-hd-maps)).
- **Banned turns** (`junction_control.ban_left_highway`, `ban_share`):
  - **right-in / right-out** where the priority road is an at-grade highway
    with 2 or more lanes each way: the minor roads turn right only, and the
    highway's left turns into them are banned;
  - **seeded:** at `ban_share` (5 %) of the other junctions with lights or
    priority control, one seeded approach loses its left turns;
  - a ban is applied only while the network's turn graph (road traversals
    linked by the movements) keeps its strongly connected components and the
    banned exit stays reachable from the approach by another way;
  - a sign 209 / 214 at the stop line says what the approach keeps (209-20
    right only, 214 straight and right, ...);
  - sidecar `banned`: junction, from / to road end, reason.
- **Lane arrows** (Z 297): `roadMark` objects drawn as white lines, 20 m
  before the stop line in each incoming lane of an approach with 2 or more
  lanes, at junctions with priority control or lights, shaped by the lane's
  movement kinds. Each is a few polylines over shared outline corners: the
  shaft running on into one stem, the other stems, and each head through
  its tip (a straight arrow: 4 corners, 2 markings). esmini draws a marking
  through all its corners (mitred), and its OSI output has one road marking
  per polyline. esmini has no sign type 297, so they are objects only. One
  closed outline per arrow would be smaller still, but esmini draws closed
  outlines in a flat grey and then exports no road markings for them.
- **Junction-surface fill:** surface inside a junction that no lane covers
  (between the curb and the movements at corners no turn follows, slivers
  between turning lanes, the middle of large junctions) becomes a flat
  `<object type="patch">` with a closed asphalt outline, on the junction's
  first connecting road, its corners on the junction plane. esmini draws it
  in a flat colour; it is an object, not a lane, for CARLA and OSI. The areas
  are also listed in the sidecar (`junction_fills`, world coordinates). The
  objects are always written, so the `.xodr` is the same with or without a
  scene; a scene [draws the same surface](scene.md#junction-surface) under
  them.

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

### Junction control and right of way (HD maps)

`control.py`, with HD maps on (`junction_control` section, presets
`default`, `dense`, `rural`). Each junction gets a control kind and a role
per approach (road end), from the graph alone. The first matching rule wins:

| Junction | Control | Approach roles |
|---|---|---|
| two roads, or a ramp node | `none` | none |
| roundabout entry (`metadata["roundabouts"]`) | `roundabout` | ring `major`, arm `yield` |
| diamond terminal (`metadata["interchanges"]`) | `signal` if the crossing road is an avenue or more, else `priority` | crossing road `major`, ramps `stop` |
| `Node.control` set (by hand or by [`annotate`](maps.md#annotate-profiles-and-control-from-the-maps)) | as set: `signal`, `stop`, `yield`, `right_before_left`, `none` | |
| the crossing road an avenue or more, or 5+ legs with an avenue | `signal` (lights) | all `signal` |
| only streets (`right_before_left`) | `right_before_left` | all `right_before_left` |
| otherwise | `priority` | priority road `major`, the others `yield`, or `stop` where the priority road is at least `stop_speed` (16.7 m/s) fast, a minor arm is skewed more than `stop_skew_deg` (30°), the junction has 5+ legs, or at the seeded `stop_share` (15 %) |

The **priority road** is the highest-ranked arm (class, then lanes, then
speed) and the highest-ranked other arm, the most nearly opposite one among
equals.

**Right of way.** Two movements from different approaches conflict where
their paths cross or end in the same lane. For each conflicting pair one has
priority: the priority road's movement over a minor road's; between equal
approaches the oncoming straight or right turn over a left turn (or
U-turn), and otherwise the movement coming from the right. At lights only
the permissive left turns get one; the phase plans separate the rest. Each
pair is a `<junction><priority high low>` record, and is listed in the
sidecar too.

**Signs and lines** (`furniture.py`), per approach with incoming lanes:

| Role | Line, 1 m before the road end (1 m behind a crosswalk) | Sign, 0.5 m outside the carriageway, facing the traffic |
|---|---|---|
| `stop` | stop line: white, 0.5 m deep, solid | 206 |
| `yield` | yield line (Z 341): 0.5 m dashes, 0.25 m gaps, 0.3 m deep | 205; at roundabout entries also 215, above it |
| `signal` | stop line | a traffic light on the roadside at the stop line, and one over the lanes on approaches with 3 or more lanes ([traffic lights](#traffic-lights-and-controllers-hd-maps)) |
| `major` | none | 301, 30 m before the junction (at most half the road), not on roundabout rings; along a priority road through two or more priority junctions 306 / 307 instead (below) |

**Crosswalks.** At the junction end of a road with sidewalks on both sides
there (junctions with control only), 0.5 m before the road end, from curb to
curb: 3 m deep on streets, 4 m on avenues and highways.

- **At lights,** on every arm: two solid edge lines 0.3 m wide
  (Fußgängerfurt), no sign.
- **Without lights:** a zebra (Z 293: 0.5 m stripes, 0.5 m gaps) and a sign
  350-10 facing each direction of travel, only where `junction_control.zebras`
  allows. German towns have few zebras at junctions without lights, so the
  default `dense_avenues` puts them only on avenue arms in the `urban`
  profile: on maps the dense areas (`annotate`, from `urban_density`),
  without maps every avenue; never on streets. `all` puts one on every arm,
  `none` on none.
- The stop or yield line moves 1 m behind the crosswalk.
- **Skipped** where the road has too little room for the crosswalk, its stop
  line and 2 m more (half the road where both ends are junctions).

A crosswalk is `<object type="crosswalk">` with an open outline across the
road and its markings along it (a zebra's stripes; at lights two outline rows,
one per edge line).

**Priority roads (306 / 307).** Where a priority road runs through two or
more consecutive priority junctions (traced along each junction's priority
road arms, per direction of travel), it gets 306 just after the first
junction and 307 30 m before the junction where it ends; the 301s at the
junctions in between go, since 306 holds there. A run through one junction
keeps its 301.

**Along a road**, per direction of travel, except on roundabout rings and
cul-de-sac loops:

- **Speed limit 274** (value in km/h: to the nearest km/h, then down to 10)
  where the posted speed changes, and 10 m after each junction on roads of
  100 m or more. An advisory corner speed is posted only where it is at least
  10 km/h below the limit.
- **Curve warning** 103-10 / 103-20 (left / right) before every advisory
  corner, or 105-10 / 105-20 (double curve) where the next one starts within
  200 m, at the corner's deceleration lead (at least 10 m), on the post above a
  274 standing there.

Codes with a DE subtype (103, 105, 350) carry it; 274 carries an empty
subtype and an integer `value` with `unit="km/h"`, the key esmini looks it up
by.

Lines are `roadMark` objects (`stopLine`, `yieldLine`) with an open outline
of two corners across the approach's driving lanes and one `<marking>` along
it, which esmini draws as paint. esmini puts every corner at the object's
height plus the corner's `z`, so each corner's `z` is the road surface under
it (elevation and superelevation) relative to the surface under the object,
plus 2 cm: lines on a banked or sloped road end lie on the surface instead of
sinking partly into it. There is no 294 signal, since esmini has no
OSI type for it and CARLA does not know it. Signs are `<signal country="DE"
subtype="-1">` with a `<validity>` over the approach lanes; esmini types
205, 206, 215 and 301 for OSI. Signals are numbered 1, 2, …; line objects
from (C + 1) · 1000 on (C chain roads), above every bridge and tunnel id.

### Traffic lights and controllers (HD maps)

`lights.py` (`traffic_lights` section). Every approach of a junction with
lights gets a light: `<signal country="OpenDRIVE" type="1000001"
dynamic="yes">` (three lamps), 3 m high on the right roadside at the stop
line, plus one 5.5 m high over the middle of the approach lanes where the
approach has `overhead_min_lanes` (3) or more lanes. The junction's fixed-time
plan:

- **Phase groups:** approaches arriving from roughly opposite directions
  (within 180 ± 30°) share a phase, the highest-ranked first, if their
  movements are compatible; the rest get a phase each. Left turns without a
  pocket are permissive: they yield to the oncoming traffic of their phase
  (the junction's `<priority>` records). **Pockets** have a light of their own
  over the pocket and a protected phase before their group's through phase.
- **Each phase:** green, yellow, then all-red (`all_red`, 2 s). Yellow
  follows ITE: `reaction_time + v / (2 deceleration)` (1 s, 3 m/s²), at
  least `min_yellow` (3 s), for the group's fastest approach: 3.3 s at
  50 km/h, 3.8 s at 60 km/h.
- **Cycle:** 60 s for two phases plus 15 s per further one, within `cycle`
  ([60, 90] s). The green time left after yellow and all-red is split by the
  groups' incoming lane counts, each at least `min_green` (10 s, which may
  stretch the cycle).
- **Pedestrian phase:** a junction whose approaches all fit in one group (an
  opposite pair, the third arm leaving only) has nothing to switch between.
  With crosswalks it keeps its lights and gets a second phase with every
  vehicle light red, long enough to cross the longest crosswalk at
  `walk_speed` (1.2 m/s), at least `min_green`. Without crosswalks it becomes
  a priority junction (the pair is the priority road).
- **Offset:** seeded per junction, a whole second within the cycle, so
  neighbouring junctions do not switch together.

In the `.xodr`: a top-level `<controller>` per junction with lights, with a
`<control signalId>` per light (between the roads and the junctions), and a
`<junction><controller>` naming it. The plans themselves go to the sidecar
(`signal_plans`); [`scenario`](scenarios-and-simulation.md#traffic-lights)
turns them into OpenSCENARIO.

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
| `hdmap_version` | HD maps on only: 1 for the stage B content (`HDMAP_VERSION`, `roadgen/opendrive/__init__.py`); absent with `--no-hd` |
| `cross_sections` | HD maps on only: each edge's cross-section profile |
| `signal_plans` | HD maps on only, per junction with lights: `junction`, `node`, `controller`, `cycle` and `offset` [s], `min_green`, `groups` (`[road, contact]` per phase), `lights` (signal id → its approach), `phases` (`group`, `kind` green / yellow / all_red / pedestrian, `duration`, `states`: signal id → `"on;off;off"` red, `"off;on;off"` yellow, `"off;off;on"` green), and the junction's `conflicts` and `permissive` (high, low) connecting-road pairs |
| `lane_use` | HD maps on only, per approach: `junction`, `road`, `contact`, `lanes` (incoming lane → movement kinds), `pockets` |
| `banned` | HD maps on only, per banned turn: `junction`, `from` and `to` (`[road, contact]`), `reason` (`right_in_right_out`, `seeded`) |
| `junctions` | HD maps on only: per junction its `control` kind, `priority_roads` (`[road, contact]`), `approaches` (`road`, `contact`, `role`), and its numbers of `conflicts`, `priorities` and `crosswalks` |
| `movement_control` | HD maps on only: per connecting road its approach's `control` role, `priority` (on the priority road) and `yields_to` (connecting roads) |
| `corner_roads` | HD maps on only: each corner road's `junction_id`, `road`, `from_road`, `to_road`, lane `kinds`, `path` and `closes` (a movement drives the turn it follows) |
