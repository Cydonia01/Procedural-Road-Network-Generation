"""Road elevation profiles: RoadGraph heights -> OpenDRIVE <elevation> records.

A road's heights are fitted with a monotone cubic Hermite spline (PCHIP,
Fritsch & Carlson 1980): height and slope are continuous, and it never
overshoots the samples, so a road does not rise above its highest point.
Both ends are flat (slope 0) at their node's height, which is also the
height of the flat junction area there; every road and connecting road of
a junction therefore meets at the same height and grade.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

MIN_KNOT_GAP = 1.0  # [m] samples closer than this to a road end are dropped


class Elevation(NamedTuple):
    """z(ds) = a + b ds + c ds^2 + d ds^3 from s on."""

    s: float
    a: float
    b: float
    c: float
    d: float


def pchip_slopes(s: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Slopes at the interior knots (Fritsch-Carlson); the two end slopes are 0."""
    h = np.diff(s)
    delta = np.diff(z) / h
    m = np.zeros_like(z)
    for i in range(1, len(z) - 1):
        d0, d1 = delta[i - 1], delta[i]
        if d0 * d1 <= 0:
            continue  # a local extremum (or flat): keep it flat
        w0, w1 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
        m[i] = (w0 + w1) / (w0 / d0 + w1 / d1)
    return m


def road_elevation(d: np.ndarray, z: np.ndarray, start: float, end: float,
                   z_start: float, z_end: float) -> list[Elevation]:
    """Records for the road covering edge distances [start, end].

    d, z: the edge's height samples (distance along the road's direction).
    The road's s runs from 0 at start to end - start; it begins at z_start
    and ends at z_end, both flat.
    """
    length = end - start
    gap = max(MIN_KNOT_GAP, 0.25 * float(np.min(np.diff(d)))) if len(d) > 1 else MIN_KNOT_GAP
    inner = (d > start + gap) & (d < end - gap)
    s = np.concatenate([[0.0], d[inner] - start, [length]])
    h = np.concatenate([[z_start], z[inner], [z_end]])
    if np.all(h == h[0]):
        return [Elevation(0.0, float(h[0]), 0.0, 0.0, 0.0)]
    m = pchip_slopes(s, h)
    out = []
    for i in range(len(s) - 1):
        w = s[i + 1] - s[i]
        delta = (h[i + 1] - h[i]) / w
        c = (3 * delta - 2 * m[i] - m[i + 1]) / w
        dd = (m[i] + m[i + 1] - 2 * delta) / (w * w)
        out.append(Elevation(*(float(v) for v in (s[i], h[i], m[i], c, dd))))
    return out


def evaluate(records: list[Elevation], s: float) -> tuple[float, float]:
    """(height, slope) at s."""
    rec = records[0]
    for r in records:
        if r.s <= s + 1e-9:
            rec = r
    ds = s - rec.s
    return (rec.a + rec.b * ds + rec.c * ds * ds + rec.d * ds ** 3,
            rec.b + 2 * rec.c * ds + 3 * rec.d * ds * ds)
