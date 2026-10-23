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

from roadgen.geometry import design as design_lib
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


ARM_SAMPLE = 10.0  # [m] approach heights for a junction plane are taken this far into each road
HERMITE_SPLIT = 4  # cubic Hermite pieces per connecting-road geometry


def junction_planes(graph: RoadGraph, na: NetworkAlignment,
                    design: DesignParams = DesignParams()) -> dict[int, JunctionPlane]:
    """The plane of every junction area.

    Through the node's height, with the gradient that best fits (least
    squares) the heights of the roads ARM_SAMPLE into each approach, capped at
    design.junction_max_grade. Flat (gradient 0) without heights or with a
    zero cap: then every road meets the junction level, as before.
    """
    planes = flat_planes(graph, na.junctions)
    if design.junction_max_grade <= 0 or not graph.has_elevation:
        return planes
    arms: dict[int, list[tuple[float, float, float]]] = {n: [] for n in na.junctions}
    for ca in na.chains:
        d, z = height_samples(graph, ca)
        for node_id, s in ((ca.chain.start, min(ARM_SAMPLE, ca.length / 2)),
                           (ca.chain.end, max(ca.length - ARM_SAMPLE, ca.length / 2))):
            if node_id in arms:
                p = ca.alignment.pose(s)
                arms[node_id].append((p.x, p.y, float(np.interp(s, d, z))))
    for n, pts in arms.items():
        plane = planes[n]
        if not pts:
            continue
        a = np.array([(x - plane.x0, y - plane.y0) for x, y, _ in pts])
        b = np.array([h - plane.z0 for _, _, h in pts])
        (gx, gy), *_ = np.linalg.lstsq(a, b, rcond=None)
        grade = math.hypot(gx, gy)
        if grade > design.junction_max_grade:
            gx, gy = gx * design.junction_max_grade / grade, gy * design.junction_max_grade / grade
        planes[n] = JunctionPlane(plane.x0, plane.y0, plane.z0, float(gx), float(gy))
    return planes


# --- piecewise cubics ----------------------------------------------------------------------

Cubic = tuple[float, float, float, float, float]  # (s, a, b, c, d): a + b ds + c ds^2 + d ds^3 from s


def _shift(rec: Cubic, s: float) -> Cubic:
    """The same cubic, expanded about s instead of rec[0]."""
    s0, a, b, c, d = rec
    h = s - s0
    return (s, a + b * h + c * h * h + d * h ** 3, b + 2 * c * h + 3 * d * h * h, c + 3 * d * h, d)


def _value(records: list[Cubic], s: float) -> float:
    return float(evaluate_many(records, np.array([s]))[0]) if records else 0.0


def _sum(parts: list[list[Cubic]], length: float) -> list[Cubic]:
    """Sum of piecewise cubics (each zero before its first record), merged."""
    starts = sorted({0.0, *(r[0] for part in parts for r in part)})
    starts = [s for s in starts if s < length - 1e-9]
    out: list[Cubic] = []
    for s in starts:
        coef = np.zeros(4)
        for part in parts:
            active = [r for r in part if r[0] <= s + 1e-9]
            if active:
                coef += np.array(_shift(active[-1], s)[1:])
        rec = (s, *map(float, coef))
        if out and np.allclose(_shift(out[-1], s)[1:], rec[1:], atol=1e-12):
            continue
        out.append(rec)
    return out


def hermite(s0: float, s1: float, v0: float, v1: float, m0: float, m1: float) -> Cubic:
    """The cubic from s0 to s1 with values v0, v1 and slopes m0, m1."""
    w = s1 - s0
    delta = (v1 - v0) / w
    return (s0, v0, m0, (3 * delta - 2 * m0 - m1) / w, (m0 + m1 - 2 * delta) / (w * w))


def chain_superelevation(graph: RoadGraph, ca: ChainAlignment, planes: Mapping[int, JunctionPlane],
                         design: DesignParams = DesignParams()) -> list[Cubic]:
    """Superelevation records (s, a, b, c, d) [rad] of a chain road; [] when it is not banked.

    Positive superelevation falls to the right (OpenDRIVE). On each corner
    (with design.banking) the road rolls towards the curve centre: along the
    entry spiral to atan(e) on the arc and back to 0 along the exit spiral, e
    from design.superelevation. The runoff is a smoothstep rather than
    AASHTO's linear one, so the roll rate is 0 at the tangent points and lane
    grades stay continuous where a curve ends at a junction. At a junction end the road meets
    the junction plane's cross slope, reached over bank_ramp_length (at most
    half the road) with a smoothstep, so the roll rate is 0 at both ends of
    the ramp. Ramps and corner banking add up where they overlap.
    """
    parts: list[list[Cubic]] = []
    if design.banking:
        for c in ca.corners:
            e = design_lib.superelevation(c.lane_radius, c.design_speed, _class(graph, ca, c.node), design)
            if e <= 0:
                continue
            phi = -math.copysign(math.atan(e), c.deflection)
            up = min(c.spiral_length, 0.5 * (c.s_end - c.s_start))  # spiral-spiral: up to the middle
            parts.append([hermite(c.s_start, c.s_start + up, 0.0, phi, 0.0, 0.0),
                          (c.s_start + up, phi, 0.0, 0.0, 0.0),
                          hermite(c.s_end - up, c.s_end, phi, 0.0, 0.0, 0.0),
                          (c.s_end, 0.0, 0.0, 0.0, 0.0)])
    ramp = min(design.bank_ramp_length, ca.length / 2)
    for node_id, pose, at_start in ((ca.chain.start, ca.alignment.start, True),
                                    (ca.chain.end, ca.alignment.end, False)):
        plane = planes.get(node_id)
        if plane is None or plane.grade == 0.0:
            continue
        phi = math.atan(plane.cross_slope(pose.hdg))
        if at_start:
            parts.append([hermite(0.0, ramp, phi, 0.0, 0.0, 0.0), (ramp, 0.0, 0.0, 0.0, 0.0)])
        else:
            parts.append([hermite(ca.length - ramp, ca.length, 0.0, phi, 0.0, 0.0)])
    if not parts:
        return []
    out = _sum(parts, ca.length)
    return [] if all(max(abs(v) for v in r[1:]) < 1e-12 for r in out) else out


def _class(graph: RoadGraph, ca: ChainAlignment, node_id: int) -> str:
    k = ca.chain.nodes.index(node_id)
    a, b = graph.edges[ca.chain.edge_ids[k - 1]], graph.edges[ca.chain.edge_ids[k]]
    return (a if a.speed_limit >= b.speed_limit else b).road_class


def path_profiles(points, length: float, breaks: list[float],
                  plane: JunctionPlane) -> tuple[list[Cubic], list[Cubic]]:
    """(elevation, superelevation) records of a connecting road lying on a plane.

    points(s) -> (x, y, hdg, curvature) along the road. Each stretch between
    breaks is split in HERMITE_SPLIT cubic Hermite pieces matching the plane's
    height and the road's grade (and the plane's roll and its rate) exactly at
    their ends, so the road meets the roads it joins with matching height,
    grade and roll; inside, the error is far below a millimetre.
    """
    if plane.grade == 0.0:
        return [(0.0, plane.z0, 0.0, 0.0, 0.0)], []
    knots = sorted({0.0, length, *(
        b0 + (b1 - b0) * k / HERMITE_SPLIT
        for b0, b1 in zip(breaks, [*breaks[1:], length]) for k in range(1, HERMITE_SPLIT))})
    vals = []
    for s in knots:
        x, y, h, kappa = points(s)
        c = plane.cross_slope(h)
        vals.append((float(plane.height(x, y)), plane.slope(h),
                     math.atan(c), -kappa * plane.slope(h) / (1 + c * c)))
    elev, sup = [], []
    for (s0, v0), (s1, v1) in zip(zip(knots, vals), zip(knots[1:], vals[1:])):
        elev.append(hermite(s0, s1, v0[0], v1[0], v0[1], v1[1]))
        sup.append(hermite(s0, s1, v0[2], v1[2], v0[3], v1[3]))
    return elev, sup
