"""G2 Hermite interpolation with clothoids (curvature-continuous transitions).

Wraps pyclothoids.SolveG2 (Bertolazzi & Frego, "On the G2 Hermite
interpolation problem with clothoids", 2018): three clothoid pieces that
match position, heading and curvature at both ends. The solver does not
raise on hard cases (it returns NaN or long detours), so every result is
checked before use and None is returned when it is unusable; callers fall
back to another curve.
"""

from __future__ import annotations

import logging
import math
from itertools import pairwise
from typing import Optional

import numpy as np
from pyclothoids import SolveG2
from shapely.geometry import LineString

from roadgen.geometry.planar import Pose, wrap_angle
from roadgen.geometry.primitives import Piece

log = logging.getLogger(__name__)

POS_TOL = 1e-3  # [m] allowed end-position error of the solution
HDG_TOL = 1e-4  # [rad]
CURV_TOL = 1e-5  # [1/m]
MAX_LENGTH_RATIO = 3.0  # solution length / chord above this is a detour
SAMPLE_STEP = 0.5  # [m] for the self-intersection check


def g2_hermite(start: Pose, k0: float, end: Pose, k1: float,
               k_limit: Optional[float] = None,
               max_length_ratio: float = MAX_LENGTH_RATIO) -> Optional[list[Piece]]:
    """Three spiral pieces from start (curvature k0) to end (curvature k1), or None.

    Rejected when the solver fails, the pieces don't join or don't reach end,
    the path is longer than max_length_ratio times the chord, it crosses
    itself, or (with k_limit) any curvature exceeds k_limit [1/m].
    """
    chord = math.hypot(end.x - start.x, end.y - start.y)
    if chord < POS_TOL:
        return None
    try:
        sol = SolveG2(start.x, start.y, start.hdg, k0, end.x, end.y, end.hdg, k1)
    except (RuntimeError, ValueError) as e:  # pragma: no cover - the solver normally doesn't raise
        log.debug("SolveG2 failed: %s", e)
        return None
    values = [(c.XStart, c.YStart, c.ThetaStart, c.KappaStart, c.KappaEnd, c.length) for c in sol]
    if not all(math.isfinite(v) for row in values for v in row):
        return None
    pieces = []
    for x, y, h, ka, kb, length in values:
        if length <= 0:
            continue  # a zero-length middle piece is fine to drop
        pieces.append(Piece(Pose(x, y, h), length, ka, kb))
    if not pieces or not _joins(pieces, start, k0, end, k1):
        return None
    total = sum(p.length for p in pieces)
    if total > max_length_ratio * chord:
        return None
    if k_limit is not None and max(max(abs(p.k0), abs(p.k1)) for p in pieces) > k_limit:
        return None
    if not _is_simple(pieces):
        return None
    return pieces


def _joins(pieces: list[Piece], start: Pose, k0: float, end: Pose, k1: float) -> bool:
    first, last = pieces[0], pieces[-1]
    if (math.hypot(first.start.x - start.x, first.start.y - start.y) > POS_TOL
            or abs(wrap_angle(first.start.hdg - start.hdg)) > HDG_TOL or abs(first.k0 - k0) > CURV_TOL):
        return False
    for a, b in pairwise(pieces):
        e = a.end
        if (math.hypot(e.x - b.start.x, e.y - b.start.y) > POS_TOL
                or abs(wrap_angle(e.hdg - b.start.hdg)) > HDG_TOL or abs(a.k1 - b.k0) > CURV_TOL):
            return False
    e = last.end
    return (math.hypot(e.x - end.x, e.y - end.y) <= POS_TOL
            and abs(wrap_angle(e.hdg - end.hdg)) <= HDG_TOL and abs(last.k1 - k1) <= CURV_TOL)


def _is_simple(pieces: list[Piece]) -> bool:
    pts = []
    for k, p in enumerate(pieces):
        ds = np.linspace(0.0, p.length, max(2, math.ceil(p.length / SAMPLE_STEP) + 1))
        x, y = p.xy(ds)
        rows = np.column_stack([x, y])
        pts.append(rows if k == 0 else rows[1:])
    return LineString(np.concatenate(pts)).is_simple
