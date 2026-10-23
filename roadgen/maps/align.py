"""Fit the road curves to the input maps: radius caps for bends that would leave
legal ground.

Between generation and drape (like drape, the OpenDRIVE builder never sees a
map). The fitted roads (roadgen.geometry.alignment) cut inside every bend;
the generator only checked its straight segments. For each corner this step
checks the curve against the maps:

- terrain: the curve may not cross water where the straight corner did not
  (a curve may not create a new bridge);
- elevation: the terrain grade along the curve, over ``grade_length``, may
  not exceed ``max_grade`` where the straight corner stayed within it.

A corner that fails gets the largest radius that passes (bisection on its
scale), stored as Node.radius_cap; the builder, drape and scene then fit the
same smaller curve. Capping one corner can give its neighbour more room, so
the check repeats until nothing changes. Deterministic; the input graph is
left unchanged, and nothing is written without maps.
"""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.geometry.alignment import MIN_SCALE, Corner, network_alignment
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN
from roadgen.graph.model import RoadGraph
from roadgen.maps.model import WATER, MapLayer

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "align")
BISECTION_STEPS = 20
WATER_STEP = 1.0  # [m] longest sampling step of a curve against the terrain map
MAX_PASSES = 5


@dataclass(frozen=True)
class AlignParams:
    max_grade: float = _D["max_grade"]  # max terrain rise / run along a curve; 0 = no limit
    grade_length: float = _D["grade_length"]  # grades are measured over this distance [m]

    def __post_init__(self) -> None:
        if self.max_grade < 0 or self.grade_length <= 0:
            raise ValueError("need max_grade >= 0 and grade_length > 0")


def align(graph: RoadGraph, terrain: Optional[MapLayer] = None, elevation: Optional[MapLayer] = None,
          params: AlignParams = AlignParams(), design: DesignParams = DesignParams(),
          junction_margin: float = DEFAULT_MARGIN) -> RoadGraph:
    """A copy of graph with Node.radius_cap set on bends whose curve would leave legal ground."""
    if terrain is not None and terrain.kind != "terrain":
        raise ValueError("align needs a terrain map as terrain")
    if elevation is not None and elevation.kind != "elevation":
        raise ValueError("align needs an elevation map as elevation")
    out = copy.deepcopy(graph)
    if terrain is None and elevation is None:
        return out
    capped = 0
    for _ in range(MAX_PASSES):
        try:
            na = network_alignment(out, design, junction_margin)
        except ValueError as e:  # the builder will reject the graph too
            log.warning("align: no fitted roads (%s)", e)
            return out
        changed = 0
        for ca in na.chains:
            for c in ca.corners:
                lam = _legal_scale(c, terrain, elevation, params)
                if lam is not None:  # rounded down: a cap never allows a larger curve
                    out.nodes[c.node].radius_cap = math.floor(lam / c.shape.kappa * 1000) / 1000
                    changed += 1
        capped += changed
        if not changed:
            break
    out.metadata["align"] = asdict(params)
    log.info("align: %d corner radius caps", capped)
    return out


def _legal_scale(c: Corner, terrain: Optional[MapLayer], elevation: Optional[MapLayer],
                 params: AlignParams) -> Optional[float]:
    """None if the corner is legal as built, else the largest legal scale below it.

    A corner that is illegal even when tiny is left alone: the straight
    polyline itself is the generator's responsibility.
    """
    def ok(lam: float) -> bool:
        return _legal(c, lam, terrain, elevation, params)

    if ok(c.scale) or not ok(MIN_SCALE):
        return None
    lo, hi = MIN_SCALE, c.scale
    for _ in range(BISECTION_STEPS):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def _legal(c: Corner, lam: float, terrain: Optional[MapLayer], elevation: Optional[MapLayer],
           params: AlignParams) -> bool:
    pieces = c.pieces(lam)
    t = lam * c.shape.tangent
    vx, vy = c.vertex
    h_out = c.heading_in + c.shape.deflection
    straight = np.array([[vx - t * math.cos(c.heading_in), vy - t * math.sin(c.heading_in)],
                         [vx, vy], [vx + t * math.cos(h_out), vy + t * math.sin(h_out)]])
    if terrain is not None:
        step = min(WATER_STEP, terrain.grid.cell_size / 4)
        if (_wet(_curve(pieces, step), terrain) and not _wet(_polyline(straight, step), terrain)):
            return False
    if elevation is not None and params.max_grade > 0:
        curve = _curve(pieces, params.grade_length)
        if len(curve) > 1:
            g_curve = _max_grade(curve, elevation)
            if g_curve > params.max_grade and g_curve > _max_grade(
                    _polyline(straight, params.grade_length), elevation) + 1e-9:
                return False
    return True


def _curve(pieces, step: float) -> np.ndarray:
    pts = []
    for k, p in enumerate(pieces):
        ds = np.linspace(0.0, p.length, max(2, math.ceil(p.length / step) + 1))
        x, y = p.xy(ds)
        rows = np.column_stack([x, y])
        pts.append(rows if k == 0 else rows[1:])
    return np.concatenate(pts)


def _polyline(pts: np.ndarray, step: float) -> np.ndarray:
    out = [pts[:1]]
    for a, b in pairwise(pts):
        n = max(1, math.ceil(float(np.hypot(*(b - a))) / step))
        t = np.linspace(0.0, 1.0, n + 1)[1:, None]
        out.append(a + t * (b - a))
    return np.concatenate(out)


def _wet(pts: np.ndarray, terrain: MapLayer) -> bool:
    return bool(np.any(terrain.sample(pts[:, 0], pts[:, 1]) == WATER))


def _max_grade(pts: np.ndarray, elevation: MapLayer) -> float:
    z = elevation.sample(pts[:, 0], pts[:, 1])
    run = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    keep = run > 1e-6
    return float(np.max(np.abs(np.diff(z))[keep] / run[keep])) if keep.any() else 0.0
