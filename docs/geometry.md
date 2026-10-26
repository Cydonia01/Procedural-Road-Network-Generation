# Road geometry (`roadgen/geometry/`)

A neutral package for road shape, shared by the generators, the maps, the 3D
scene and the OpenDRIVE builder. It never imports `scenariogeneration` or
`roadgen.opendrive`, so a generator may use it without breaking the rule that
generators never touch OpenDRIVE. Plan and reasoning:
[`plans/2026-10-23-stage-a-geometry-implementation.md`](../plans/2026-10-23-stage-a-geometry-implementation.md).

| Module | Contents |
|--------|----------|
| `planar.py` | `Pose`, `wrap_angle`, straight segments and trimming, Hermite `paramPoly3` curves |
| `junction_space.py` | `junction_trims`: where the roads meeting at a node must stop |
| `design.py` | Design-speed rules: minimum radius, advisory speed, superelevation, spiral and taper lengths |
| `primitives.py` | `Piece` (line / arc / spiral) and `Alignment` (a chain of pieces) |
| `clothoid.py` | `g2_hermite`: curvature-continuous transitions with three clothoids |
| `chains.py` | Chains: runs of edges through two-road nodes that become one road |
| `alignment.py` | Fitted reference lines of the chains: corners, room, clearance, stations |
| `profiles.py` | Along-road profiles of a chain road: lane widths and tapers, speeds, heights, bridges |
| `vertical.py` | Elevation fits (PCHIP), junction planes, chain elevation, banking, connecting roads on planes |

## Design-speed rules (`design.py`)

Parameters are in `defaults.yaml` under `design` (`DesignParams`).

- **Side friction** `f_max(v)` from the AASHTO Green Book table, falling
  with speed (0.27 at 32 km/h, 0.20 at 48 km/h, 0.14 at 80 km/h, 0.10 at
  113 km/h), capped so that `g·(e + f) ≤ a_lat_max` (3 m/s²).
- **Maximum superelevation** `e_max`: 0.04 for streets and avenues (urban
  practice), 0.06 for highways.
- **Minimum radius** `R = v² / (g·(e + f))`: about 84 m at 50 km/h on a
  street, about 252 m at 80 km/h on a highway.
- **Advisory speed** for a curve that can't have the minimum radius: the
  fastest speed it supports, rounded down to a multiple of 5 km/h like a
  curve-warning sign, at least 5 km/h.
- **Superelevation** of a curve (only with `banking: true`): AASHTO
  method 5, `e = e_max·(2q − q²)` with `q = R_min / R`.
- **Spiral length** (AASHTO): the longest of the lateral-jerk comfort length
  `v³ / (C·R)` (`C = jerk_rate` = 1.2 m/s³), the superelevation runoff
  `w·e / Δ(v)` (maximum relative gradient Δ, 0.80 % at 20 km/h down to
  0.35 % at 130 km/h) and the length that shifts the curve inward by
  `spiral_min_shift` (0.2 m), capped at the length for `spiral_max_shift`
  (1.0 m), with `L = √(24·p·R)` for a shift `p`. About 27 m into an 84 m
  curve at 50 km/h.
- **Taper length** for a lane add or drop (MUTCD): `w·S² / 155` below
  70 km/h, `w·S / 1.6` above (S in km/h): 56 m for a 3.5 m lane at 50 km/h.

## Curve pieces (`primitives.py`)

A `Piece` has a start pose, a length and a curvature that changes linearly
from `k0` to `k1`: a line (`k0 = k1 = 0`), an arc (`k0 = k1`) or a spiral.
These are exactly OpenDRIVE's `<line>`, `<arc>` and `<spiral>`. Positions
along a spiral come from the Fresnel integrals (`scipy.special.fresnel`)
after completing the square in the heading `θ0 + k0·u + c·u²/2`; tests
compare them with numerical integration to 1 µm. An `Alignment` chains
pieces and is evaluated or sampled by station `s`.

## Clothoid transitions (`clothoid.py`)

`g2_hermite(start, k0, end, k1)` returns three spiral pieces matching
position, heading and curvature at both ends (pyclothoids' `SolveG2`,
Bertolazzi & Frego 2018). The solver doesn't raise on hard cases, so every
result is checked: the pieces must join and reach the end, be at most three
times the chord long, not cross themselves and stay under an optional
curvature limit. Otherwise it returns `None` and the caller falls back to
another curve.

## Chains and fitted roads

`chains.py`: a node with exactly two roads is **mergeable** when the road
simply continues through it: `Node.kind` is not `"junction"`, the road turns
by at most `max_merge_deflection_deg` (135°) there, and every travel
direction on one side exists on the other (a one-way road meeting a two-way
road stays a junction, so no lane starts or ends mid-road). A **chain** is a
maximal path of edges through mergeable nodes. A one-way chain runs in its
travel direction; a two-way chain along its lowest-id edge. Rings of
mergeable nodes are cut at their lowest node, and a chain may not start and
end at the same node (esmini confuses the two ends of such a road): its
middle node then stays a junction, splitting it in two.

`alignment.py`, `network_alignment(graph)`: the **single source of truth for
road shape**, used by the builder, `align`, drape, the 3D ground and
buildings and the graph plot. Per chain:

1. The polyline through the chain's nodes is trimmed at its junction ends by
   `junction_trims`. Arms leaving a junction stay straight.
2. Every inner node with deflection Δ gets a **symmetric corner**: spiral,
   arc, spiral, or two spirals with a larger radius when they alone turn by Δ.
   Its design radius is the minimum radius for the design speed (the faster
   of the two edges) plus the offset to the innermost lane centre, so that
   lane gets the minimum radius; its spirals follow `spiral_length`.
3. The corner is **shrunk, keeping its shape**, by a scale λ ≤ 1 (lengths
   ×λ, curvature ÷λ, so its tangent length scales by λ too):
   - to a radius cap (`Node.radius_cap` from stage A4, or the caller's),
   - to `max_corner_offset` if set,
   - so that the corners on each straight segment fit (both shrunk in
     proportion when they don't),
   - so that the curve keeps `road_clearance` (1 m) from every other road it
     didn't already touch as a straight polyline (bisection on λ).
4. A corner below its design radius gets the **advisory speed** of its
   innermost lane radius. A corner whose innermost lane radius would be
   below `min_lane_radius` (10 m) is not fitted: its node stays a two-road
   junction, where every lane gets its own lane-centre turn (lane geometry
   offset from a very tight reference line degenerates), and the network is
   refitted.

Stations: every graph node maps to a station on its chain road (an inner node
to its corner's midpoint), and distances along the straight polyline map
piecewise linearly to stations: `edge_range`, `edge_station`, `node_station`.
`NetworkAlignment.edge_points` gives the road position for distances along a
straight edge (on the edge itself inside a junction trim), and `edge_line`
an edge's stretch of the fitted road, extended to the junction node where its
chain ends at one; drape, the scene and the plot use these.

On the example cities, about 110–160 corners per network are fitted in about
0.25 s. A corner of 60–135° (mostly grid corners) rarely has room for its
design radius: a 90° bend at 50 km/h needs about 114 m of straight road on
each side. Those get advisory speeds, typically 70 % of the design speed,
like curve-warning signs at real 90° bends.

## Along-road profiles

`profiles.py`, all in stations of a chain road:

- `lane_layout`: each lane position's width pieces along the road. Lane-count
  changes taper the outermost lanes (narrowing to 0 before the node, widening
  from 0 after it) and lane-width changes blend after the node, all as cubic
  smoothsteps over the MUTCD taper length, at most half the edge.
  `LaneLayout.sections()` gives the lane sections, `half_widths(s)` the road
  width at a station.
- `speed_profile`: edge limits, lowered to each corner's advisory speed from
  a deceleration lead (1.5 m/s²) before the corner to its end.
- `height_samples`, `bridge_spans`: the edges' heights and bridges placed by
  station.

## Heights and banking (`vertical.py`)

- `junction_planes`: per junction, `z = z0 + gx·(x − x0) + gy·(y − y0)`
  through the node's height, least-squares fitted to the approach roads'
  heights 10 m in, gradient capped at `junction_max_grade`.
- `chain_elevation`: PCHIP through a chain's height samples, ending on the
  junction planes with the plane's grade along the road (flat at dead ends).
- `chain_superelevation`: corner banking `atan(e)` with smoothstep runoff
  along the spirals (`e` from the innermost lane radius, so the design
  radius gets exactly `e_max`), plus a smoothstep ramp to each junction
  plane's cross slope over `bank_ramp_length`; overlapping parts add up.
- `path_profiles`: a connecting road's elevation and superelevation on its
  junction plane, as cubic Hermite pieces matching the plane's height,
  grade, roll and roll rate exactly at their ends.

## Junction space (`junction_space.py`)

`node_trims` builds the arms at a node from the graph and calls
`junction_trims`; see [OpenDRIVE builder → Trimming](opendrive.md#trimming-where-roads-stop).

## Grade separation

`roadgen/geometry/grade_separation.py` (stage C8; CLI `separate`, run by
`generate` and `pipeline` after the drape whenever the graph has crossings).
A RoadGraph is planar except where a generator lets two roads cross without
a shared node (lsystem's freeways). For each such crossing:

- **Which road goes over:** the higher class (highway > avenue > street), the
  lower edge id on a tie. Crossings are the pairs whose straight edges
  cross, placed where the *fitted* roads cross (a curve can move a crossing
  tens of metres).
- **How high:** enough for `grade_separation.clearance` (6 m: 5 m clearance
  plus a 1 m deck) above the lower road, from the heights already in the
  graph.
- **The bump:** a flat top over the lower road (its half width / sin of the
  crossing angle, plus `flat_margin`), then smoothstep ramps no steeper
  than `max_grade` (5 %), along the upper road through its straightest
  continuation at each node. The flat top is a bridge span (`Edge.bridges`,
  so the ground is not levelled under it).
- **Side roads:** roads joining the raised stretch ramp down from the
  height they meet it at, at the same grade, so no junction gets a step.
  The lower road is never raised; a node that ends a lower road and lies on
  a raised stretch stays down (logged), which can leave a step there.

The crossings are listed in `metadata["overpasses"]`. The [clearance
validator](validation.md#clearance) checks the result in the `.xodr`.

**Corner clearance (fixed in stage C8).** A fitted corner may no longer
cross another road's centre line that its straight corner polyline doesn't
cross. Before, a polyline passing close by a road (within its buffer) let
the curve cut across it: 2 of 10 `city` networks on maps had two roads
crossing at one height, found by the new clearance validator.
