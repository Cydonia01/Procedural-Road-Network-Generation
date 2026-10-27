# Road geometry

`roadgen/geometry/` decides the shape of every road: where it stops at a
junction, how it curves through a bend, and how wide its lanes are along it.
It is shared by the builder, the maps steps, the 3D scene and the generators.
It never imports `scenariogeneration` or `roadgen.opendrive`, so generators
can use it without touching OpenDRIVE.

Heights, banking and overpasses are in [Heights](heights.md). How the shapes
become XML is in [OpenDRIVE builder](opendrive.md). The plan and reasoning
are in
[`plans/2026-10-23-stage-a-geometry-implementation.md`](../plans/2026-10-23-stage-a-geometry-implementation.md).

| Module | Contents |
|--------|----------|
| `design.py` | Design-speed rules: minimum radius, advisory speed, superelevation, spiral and taper lengths |
| `primitives.py` | `Piece` (line / arc / spiral) and `Alignment` (a chain of pieces) |
| `clothoid.py` | `g2_hermite`: curvature-continuous transitions of three clothoids |
| `planar.py` | `Pose`, `wrap_angle`, straight segments and trimming, Hermite `paramPoly3` |
| `junction_space.py` | `junction_trims`, `node_trims`: where roads stop at a node |
| `chains.py` | Chains: runs of edges through two-road nodes that become one road |
| `alignment.py` | `network_alignment`: the fitted reference line of every chain |
| `profiles.py` | Lane widths and tapers, speeds, heights and bridges along a chain road |
| `vertical.py`, `grade_separation.py` | See [Heights](heights.md) |

## Design-speed rules

`design.py`, with parameters under `design` in `defaults.yaml`
(`DesignParams`). The rules follow the AASHTO Green Book and the MUTCD:

| Rule | Value |
|------|-------|
| **Side friction** `f_max(v)` | AASHTO table, falling with speed: 0.27 at 32 km/h, 0.20 at 48, 0.14 at 80, 0.10 at 113. Capped so that `g·(e + f) ≤ a_lat_max` (3 m/s²) |
| **Maximum superelevation** `e_max` | 0.04 for streets and avenues (urban practice), 0.06 for highways |
| **Minimum radius** | `R = v² / (g·(e + f))`: about 84 m at 50 km/h on a street, 252 m at 80 km/h on a highway |
| **Advisory speed** | For a curve below the minimum radius: the fastest speed it supports, rounded down to 5 km/h like a curve-warning sign, at least 5 km/h |
| **Superelevation** of a curve | AASHTO method 5: `e = e_max·(2q − q²)` with `q = R_min / R` (only with `banking: true`) |
| **Spiral length** | The longest of three lengths (below). About 27 m into an 84 m curve at 50 km/h |
| **Taper length** (lane add or drop) | MUTCD: `w·S² / 155` below 70 km/h, `w·S / 1.6` above (S in km/h): 56 m for a 3.5 m lane at 50 km/h |

The three spiral lengths are:

- the lateral-jerk comfort length `v³ / (C·R)`, with `C = jerk_rate` = 1.2 m/s³;
- the superelevation runoff `w·e / Δ(v)`, where Δ is the maximum relative
  gradient, 0.80 % at 20 km/h down to 0.35 % at 130 km/h;
- the length that shifts the curve inward by `spiral_min_shift` (0.2 m),
  capped at `spiral_max_shift` (1.0 m), using `L = √(24·p·R)` for a shift `p`.

## Curve pieces and clothoids

- **Pieces.** A `Piece` (`primitives.py`) has a start pose, a length and a
  curvature that changes linearly from `k0` to `k1`:
  - a line (`k0 = k1 = 0`);
  - an arc (`k0 = k1`);
  - otherwise a spiral.

  These are exactly OpenDRIVE's `<line>`, `<arc>` and `<spiral>`.
- **Spiral positions** come from the Fresnel integrals
  (`scipy.special.fresnel`), after completing the square in the heading
  `θ0 + k0·u + c·u²/2`. Tests compare them with numerical integration to
  1 µm.
- **Alignments.** An `Alignment` chains pieces and is evaluated or sampled by
  station `s`.
- **Clothoid transitions.** `g2_hermite(start, k0, end, k1)` (`clothoid.py`)
  returns three spirals matching position, heading and curvature at both
  ends (pyclothoids' `SolveG2`, Bertolazzi & Frego 2018).
  - The solver doesn't raise on hard cases, so every result is checked: the
    pieces must join and reach the end, be at most three times the chord
    long, not cross themselves, and stay under an optional curvature limit.
  - Otherwise it returns `None`, and the caller falls back to another curve.
  - Junction turns use it ([connecting roads](opendrive.md#connecting-roads)).

## Junction trims

`junction_trims` (`junction_space.py`) computes, for each arm at a node, how
far from the node its road must stop. `node_trims` builds the arms from the
graph and calls it.

- **Baseline:** the widest half cross-section at the node, which is enough
  for right-angle crossings.
- **Acute arms.** For each pair of neighbouring arms *θ* apart, with facing
  road-edge widths *a* and *b*, the edges cross at *(b + a cos θ)/sin θ*
  along the first arm and *(a + b cos θ)/sin θ* along the second.
  - Each arm is trimmed past every such crossing, so acute junctions never
    overlap.
  - A crossing counts only if it lies on both arms (both distances positive).
    Otherwise near-straight arms of different widths would get trims of
    hundreds of metres.
- **Margin.** `junction_margin` (default 2 m, `--junction-margin`) is added as
  clearance.
- **Errors.** Arms closer than **10°** raise an error. So does an edge too
  short for the trims at both its ends; generators avoid this with the
  [repair](generators.md#repair-edges-too-short-for-their-junctions).

## Chains

A node with exactly two roads is **mergeable** (`chains.py`) when the road
simply continues through it:

- `Node.kind` is not `"junction"`;
- the road turns by at most `max_merge_deflection_deg` (135°) there;
- every travel direction on one side exists on the other. A one-way road
  meeting a two-way road stays a junction, so no lane starts or ends
  mid-road.

A **chain** is a maximal path of edges through mergeable nodes. Every chain
becomes one OpenDRIVE road.

- **Direction.** A one-way chain runs in its travel direction. A two-way
  chain runs along its lowest-id edge.
- **Rings** of mergeable nodes are cut at their lowest node.
- **Same-node ends.** A chain may not start and end at the same node, since
  esmini confuses the two ends of such a road. Its middle node then stays a
  junction, splitting it in two.

## Fitted roads

`network_alignment(graph)` (`alignment.py`) is the **single source of road
shape**. It is used by the builder, `align`, the drape, the 3D ground and
buildings, and the graph plot. For each chain:

1. **Trim.** The polyline through the chain's nodes is trimmed at its
   junction ends. Arms leaving a junction stay straight.
2. **Corner.** Every inner node with deflection Δ gets a **symmetric corner**:
   spiral, arc, spiral, or two spirals with a larger radius when they alone
   turn by Δ.
   - **Design radius:** the minimum radius for the design speed (the faster
     of the two edges), plus the offset to the innermost lane centre, so that
     lane gets the minimum radius.
   - **Spirals** follow the spiral length above.
3. **Shrink.** A corner is shrunk, keeping its shape, by a scale λ ≤ 1
   (lengths ×λ, curvature ÷λ). This is done:
   - to the node's `radius_cap` ([align](heights.md#1-align-bends-that-leave-legal-ground))
     or the caller's cap;
   - to `max_corner_offset`, if set;
   - so that the corners on each straight segment fit, both shrunk in
     proportion when they don't;
   - so that the curve keeps `road_clearance` (1 m) from every road it didn't
     already touch as a straight polyline, and never crosses another road's
     centre line that the polyline didn't cross (bisection on λ).
4. **Advisory speed or junction.**
   - A corner below its design radius gets the **advisory speed** of its
     innermost lane radius.
   - A corner whose innermost lane radius would fall below `min_lane_radius`
     (10 m) is not fitted. Its node stays a two-arm junction with a turn per
     lane, since lanes offset from a very tight reference line degenerate.
     The network is then refitted.

**Stations.** Every node maps to a station on its chain road; an inner node
maps to its corner's midpoint. Distances along the straight polyline map
piecewise linearly to stations (`edge_range`, `edge_station`,
`node_station`).

- `edge_points` gives road positions for distances along a straight edge (on
  the edge itself inside a junction trim).
- `edge_line` gives an edge's stretch of the fitted road, extended to the
  junction node where its chain ends at one.

**Typical numbers.** On the example cities, 110–160 corners per network are
fitted in about 0.25 s. A 90° bend at 50 km/h needs about 114 m of straight
road on each side for its design radius, which grid corners rarely have.
Those corners get advisory speeds, typically 70 % of the design speed, like
curve-warning signs at real 90° bends.

## Along-road profiles

`profiles.py` describes a chain road by station:

- **`lane_layout`:** each lane position's width along the road.
  - **Lane-count changes** taper the outermost lanes: narrowing to 0 before the
    node, or widening from 0 after it.
  - **Width changes** blend after the node.
  - Both are cubic smoothsteps over the MUTCD taper length, at most half the
    edge.
  - `LaneLayout.sections()` gives the lane sections, and `half_widths(s)` the
    road width at a station.
- **`speed_profile`:** edge limits, lowered to each corner's advisory speed
  from a deceleration lead (1.5 m/s²) before the corner to its end.
- **`height_samples`, `bridge_spans`:** the edges' heights and bridges placed
  by station ([Heights](heights.md)).
