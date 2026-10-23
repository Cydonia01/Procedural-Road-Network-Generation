"""Vertical road geometry: elevation profiles and junction planes.

A road's heights are fitted with a monotone cubic Hermite spline (PCHIP,
Fritsch & Carlson 1980): height and slope are continuous, and it never
overshoots the samples, so a road does not rise above its highest point.

Each junction area is a plane through its node's height (JunctionPlane;
flat unless sloped junctions are on). A road ending at a junction ends at the
plane's height with the plane's grade along the road, and every connecting
road of the junction lies on the same plane, so all roads meet at the same
height and grade. Dead ends are flat at their node's height.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, NamedTuple

import numpy as np

from roadgen.geometry.alignment import ChainAlignment, NetworkAlignment
from roadgen.geometry.design import DesignParams
from roadgen.geometry.profiles import height_samples
from roadgen.graph.model import RoadGraph

MIN_KNOT_GAP = 1.0  # [m] samples closer than this to a road end are dropped


class Elevation(NamedTuple):
    """z(ds) = a + b ds + c ds^2 + d ds^3 from s on."""

    s: float
    a: float
    b: float
    c: float
    d: float


def pchip_slopes(s: np.ndarray, z: np.ndarray, m_start: float = 0.0, m_end: float = 0.0) -> np.ndarray:
    """Slopes at the interior knots (Fritsch-Carlson); the end slopes are given."""
    h = np.diff(s)
    delta = np.diff(z) / h
    m = np.zeros_like(z)
    m[0], m[-1] = m_start, m_end
    for i in range(1, len(z) - 1):
        d0, d1 = delta[i - 1], delta[i]
        if d0 * d1 <= 0:
            continue  # a local extremum (or flat): keep it flat
        w0, w1 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
        m[i] = (w0 + w1) / (w0 / d0 + w1 / d1)
    return m


def road_elevation(d: np.ndarray, z: np.ndarray, start: float, end: float,
                   z_start: float, z_end: float, m_start: float = 0.0, m_end: float = 0.0,
                   ) -> list[Elevation]:
    """Records for the road covering distances [start, end] of its samples.

    d, z: height samples (distance along the road's direction).
    The road's s runs from 0 at start to end - start; it begins at z_start
    with slope m_start and ends at z_end with slope m_end (flat by default).
    """
    length = end - start
    gap = max(MIN_KNOT_GAP, 0.25 * float(np.min(np.diff(d)))) if len(d) > 1 else MIN_KNOT_GAP
    inner = (d > start + gap) & (d < end - gap)
    s = np.concatenate([[0.0], d[inner] - start, [length]])
    h = np.concatenate([[z_start], z[inner], [z_end]])
    if np.all(h == h[0]) and m_start == 0.0 and m_end == 0.0:
        return [Elevation(0.0, float(h[0]), 0.0, 0.0, 0.0)]
    m = pchip_slopes(s, h, m_start, m_end)
    out = []
    for i in range(len(s) - 1):
        w = s[i + 1] - s[i]
        delta = (h[i + 1] - h[i]) / w
        c = (3 * delta - 2 * m[i] - m[i + 1]) / w
        dd = (m[i] + m[i + 1] - 2 * delta) / (w * w)
        out.append(Elevation(*(float(v) for v in (s[i], h[i], m[i], c, dd))))
    return out


def evaluate_many(records: list, s: np.ndarray) -> np.ndarray:
    """Values a + b ds + c ds^2 + d ds^3 of (s, a, b, c, d) records at stations s."""
    s = np.asarray(s, dtype=float)
    starts = np.array([r[0] for r in records])
    i = np.clip(np.searchsorted(starts, s + 1e-9, side="right") - 1, 0, len(records) - 1)
    coef = np.array([r[1:] for r in records])[i]
    ds = s - starts[i]
    return coef[:, 0] + coef[:, 1] * ds + coef[:, 2] * ds ** 2 + coef[:, 3] * ds ** 3


def evaluate(records: list[Elevation], s: float) -> tuple[float, float]:
    """(height, slope) at s."""
    rec = records[0]
    for r in records:
        if r.s <= s + 1e-9:
            rec = r
    ds = s - rec.s
    return (rec.a + rec.b * ds + rec.c * ds * ds + rec.d * ds ** 3,
            rec.b + 2 * rec.c * ds + 3 * rec.d * ds * ds)


@dataclass(frozen=True)
class JunctionPlane:
    """z = z0 + gx (x - x0) + gy (y - y0) over a junction area."""

    x0: float
    y0: float
    z0: float
    gx: float = 0.0  # dz/dx
    gy: float = 0.0  # dz/dy

    def height(self, x, y):
        return self.z0 + self.gx * (np.asarray(x) - self.x0) + self.gy * (np.asarray(y) - self.y0)

    def slope(self, hdg: float) -> float:
        """Grade along heading hdg (rise / run)."""
        return self.gx * math.cos(hdg) + self.gy * math.sin(hdg)

    def cross_slope(self, hdg: float) -> float:
        """Rise per metre to the left of heading hdg."""
        return -self.gx * math.sin(hdg) + self.gy * math.cos(hdg)

    @property
    def grade(self) -> float:
        return math.hypot(self.gx, self.gy)


def flat_planes(graph: RoadGraph, nodes) -> dict[int, JunctionPlane]:
    """A flat plane at each node's height."""
    return {n: JunctionPlane(graph.nodes[n].x, graph.nodes[n].y, graph.nodes[n].z) for n in nodes}


def chain_elevation(graph: RoadGraph, ca: ChainAlignment,
                    planes: Mapping[int, JunctionPlane]) -> list[Elevation]:
    """Elevation records of a chain road: through all its height samples, ending
    on its junctions' planes (height and grade along the road), flat at dead ends."""
    d, z = height_samples(graph, ca)
    ends = []
    for node_id, pose in ((ca.chain.start, ca.alignment.start), (ca.chain.end, ca.alignment.end)):
        plane = planes.get(node_id)
        if plane is None:
            ends.append((graph.nodes[node_id].z, 0.0))
        else:
            ends.append((float(plane.height(pose.x, pose.y)), plane.slope(pose.hdg)))
    (z0, m0), (z1, m1) = ends
    return road_elevation(d + ca.trim_start, z, ca.trim_start, ca.trim_start + ca.length, z0, z1, m0, m1)


def junction_planes(graph: RoadGraph, na: NetworkAlignment,
                    design: DesignParams = DesignParams()) -> dict[int, JunctionPlane]:
    """The plane of every junction area (flat at the node's height for now)."""
    return flat_planes(graph, na.junctions)


def chain_superelevation(graph: RoadGraph, ca: ChainAlignment, planes: Mapping[int, JunctionPlane],
                         design: DesignParams = DesignParams()) -> list:
    """Superelevation records (s, a, b, c, d) [rad] of a chain road; [] when it is not banked."""
    return []
