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
- **Spiral length**: the longest of the lateral-jerk comfort length
  `v³ / (C·R)` (Shortt, `C = jerk_rate` = 0.6 m/s³), the superelevation
  runoff `w·e / Δ(v)` (AASHTO maximum relative gradient Δ, 0.80 % at
  20 km/h down to 0.35 % at 130 km/h) and `spiral_min_length`.
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

## Junction space (`junction_space.py`)

See [OpenDRIVE builder → Trimming](opendrive.md#trimming-where-roads-stop).
