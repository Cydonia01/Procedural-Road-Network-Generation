"""Lane-level geometric continuity across road links.

For every lane with a predecessor/successor lane link to another road, the
lane-center position, travel heading, height and grade must agree at the
contact point. Heights come from <elevationProfile> (0 without one) and
<lateralProfile><superelevation>: a lane centre lies t across the road
surface, which is pitched by the grade and rolled by the superelevation
about its pitched tangent (as esmini evaluates it).
The .xodr is evaluated independently of the builder (line, arc, spiral and
paramPoly3 geometries, laneOffset and polynomial lane widths); spirals use
the shared clothoid maths of roadgen.geometry.primitives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from roadgen.geometry.planar import Pose
from roadgen.geometry.primitives import Piece

POS_TOL = 0.01  # m
HDG_TOL = 0.01  # rad
Z_TOL = 0.01  # m
GRADE_TOL = 0.01  # rise / run
_FD_STEP = 1e-4

PathLike = Union[str, Path]


def _poly(el: etree._Element, ds: float) -> float:
    a, b, c, d = (float(el.get(k, 0)) for k in ("a", "b", "c", "d"))
    return a + b * ds + c * ds * ds + d * ds ** 3


def _piecewise(records: list[etree._Element], s: float, key: str) -> Optional[etree._Element]:
    """Last record whose start (attribute key) is <= s, else the first record."""
    chosen = records[0] if records else None
    for r in records:
        if float(r.get(key)) <= s + 1e-9:
            chosen = r
    return chosen


@dataclass
class RoadEval:
    road: etree._Element

    @property
    def id(self) -> str:
        return self.road.get("id")

    @property
    def length(self) -> float:
        return float(self.road.get("length"))

    @cached_property
    def geometries(self) -> list[etree._Element]:
        return self.road.findall("planView/geometry")

    @cached_property
    def sections(self) -> list[etree._Element]:
        return self.road.findall("lanes/laneSection")

    @cached_property
    def offsets(self) -> list[etree._Element]:
        return self.road.findall("lanes/laneOffset")

    def reference(self, s: float) -> tuple[float, float, float]:
        """(x, y, hdg) of the reference line at s."""
        g = _piecewise(self.geometries, s, "s")
        x0, y0, h0 = float(g.get("x")), float(g.get("y")), float(g.get("hdg"))
        ds = s - float(g.get("s"))
        child = g[0]
        if child.tag == "line":
            return x0 + ds * math.cos(h0), y0 + ds * math.sin(h0), h0
        if child.tag == "arc":
            k = float(child.get("curvature"))
            if abs(k) < 1e-12:
                return x0 + ds * math.cos(h0), y0 + ds * math.sin(h0), h0
            h = h0 + k * ds
            return x0 + (math.sin(h) - math.sin(h0)) / k, y0 - (math.cos(h) - math.cos(h0)) / k, h
        if child.tag == "spiral":
            piece = Piece(Pose(x0, y0, h0), float(g.get("length")),
                          float(child.get("curvStart")), float(child.get("curvEnd")))
            x, y = piece.xy(min(max(ds, 0.0), piece.length))
            return float(x), float(y), float(piece.heading(ds))
        if child.tag == "paramPoly3":
            length = float(g.get("length"))
            p = ds / length if child.get("pRange", "normalized") == "normalized" else ds
            u = [float(child.get(f"{c}U")) for c in "abcd"]
            v = [float(child.get(f"{c}V")) for c in "abcd"]
            pu = u[0] + u[1] * p + u[2] * p * p + u[3] * p ** 3
            pv = v[0] + v[1] * p + v[2] * p * p + v[3] * p ** 3
            du = u[1] + 2 * u[2] * p + 3 * u[3] * p * p
            dv = v[1] + 2 * v[2] * p + 3 * v[3] * p * p
            c, sn = math.cos(h0), math.sin(h0)
            return x0 + pu * c - pv * sn, y0 + pu * sn + pv * c, h0 + math.atan2(dv, du)
        raise NotImplementedError(f"road {self.id}: geometry <{child.tag}> not supported")

    def curvature(self, s: float, geometry: Optional[etree._Element] = None) -> float:
        """Signed curvature [1/m] of the reference line at s (positive = turning left).

        geometry: evaluate this <geometry> element instead of the one covering s,
        e.g. the previous one at its end, to see both sides of a geometry boundary.
        """
        g = geometry if geometry is not None else _piecewise(self.geometries, s, "s")
        ds = s - float(g.get("s"))
        child = g[0]
        if child.tag == "line":
            return 0.0
        if child.tag == "arc":
            return float(child.get("curvature"))
        if child.tag == "spiral":
            k0, k1 = float(child.get("curvStart")), float(child.get("curvEnd"))
            return k0 + (k1 - k0) * ds / float(g.get("length"))
        if child.tag == "paramPoly3":
            length = float(g.get("length"))
            p = ds / length if child.get("pRange", "normalized") == "normalized" else ds
            u = [float(child.get(f"{c}U")) for c in "abcd"]
            v = [float(child.get(f"{c}V")) for c in "abcd"]
            du, dv = u[1] + 2 * u[2] * p + 3 * u[3] * p * p, v[1] + 2 * v[2] * p + 3 * v[3] * p * p
            ddu, ddv = 2 * u[2] + 6 * u[3] * p, 2 * v[2] + 6 * v[3] * p
            return (du * ddv - dv * ddu) / math.hypot(du, dv) ** 3
        raise NotImplementedError(f"road {self.id}: geometry <{child.tag}> not supported")

    def lane_curvature(self, lane_id: int, s: float, geometry: Optional[etree._Element] = None) -> float:
        """Signed curvature [1/m] of a lane centre in its RHT travel direction.

        The lane centre is the reference line offset by t: kappa / (1 - kappa t).
        Width changes along s (tapers) are ignored.
        """
        k = self.curvature(s, geometry)
        k_lane = k / (1.0 - k * self._lane_t(lane_id, s))
        return k_lane if lane_id < 0 else -k_lane

    def elevation(self, s: float) -> tuple[float, float]:
        """(height [m], dz/ds) of the reference line at s."""
        rec = _piecewise(self.road.findall("elevationProfile/elevation"), s, "s")
        if rec is None:
            return 0.0, 0.0
        ds = s - float(rec.get("s"))
        b, c, d = (float(rec.get(k, 0)) for k in ("b", "c", "d"))
        return _poly(rec, ds), b + 2 * c * ds + 3 * d * ds * ds

    def superelevation(self, s: float) -> float:
        """Roll angle [rad] of the cross section at s (positive: falls to the right)."""
        rec = _piecewise(self.road.findall("lateralProfile/superelevation"), s, "s")
        return 0.0 if rec is None else _poly(rec, s - float(rec.get("s")))

    def lane_height(self, lane_id: int, s: float) -> float:
        """Height [m] of the lane centre: the reference height plus t sin(roll) cos(pitch)."""
        return self.elevation(s)[0] + self._lane_t(lane_id, s) * self._lateral(s)[2]

    def lane_ids(self, s: float) -> set[int]:
        section = self._section(s)
        return {int(l.get("id")) for l in section.iter("lane") if l.get("id") != "0"}

    def _lateral(self, s: float) -> tuple[float, float, float]:
        """Unit vector across the road surface (heading, left, up components).

        The road surface at s is pitched by the grade and rolled by the
        superelevation about its pitched tangent (cos p, 0, sin p); the lateral
        axis (0, 1, 0) rolled about it is (-sin r sin p, cos r, sin r cos p).
        """
        r = self.superelevation(s)
        p = math.atan(self.elevation(s)[1])
        return -math.sin(r) * math.sin(p), math.cos(r), math.sin(r) * math.cos(p)

    def lane_center(self, lane_id: int, s: float) -> tuple[float, float]:
        """(x, y) of the lane centre: t measured across the pitched and rolled road surface."""
        x, y, h = self.reference(s)
        t = self._lane_t(lane_id, s)
        along, left, _ = self._lateral(s)
        return (x + t * (along * math.cos(h) - left * math.sin(h)),
                y + t * (along * math.sin(h) + left * math.cos(h)))

    def lane_grade(self, lane_id: int, s: float) -> float:
        """d(lane height)/ds in the lane's RHT travel direction."""
        lo, hi = max(0.0, s - _FD_STEP), min(self.length, s + _FD_STEP)
        g = (self.lane_height(lane_id, hi) - self.lane_height(lane_id, lo)) / (hi - lo)
        return g if lane_id < 0 else -g

    def lane_travel_heading(self, lane_id: int, s: float) -> float:
        """Heading of the lane-center curve in the RHT travel direction."""
        lo, hi = max(0.0, s - _FD_STEP), min(self.length, s + _FD_STEP)
        (x0, y0), (x1, y1) = self.lane_center(lane_id, lo), self.lane_center(lane_id, hi)
        hdg = math.atan2(y1 - y0, x1 - x0)
        return hdg if lane_id < 0 else hdg + math.pi

    def _section(self, s: float) -> etree._Element:
        return _piecewise(self.sections, s, "s")

    def _lane_t(self, lane_id: int, s: float) -> float:
        rec = _piecewise(self.offsets, s, "s")
        t = _poly(rec, s - float(rec.get("s"))) if rec is not None else 0.0
        section = self._section(s)
        ds_section = s - float(section.get("s"))
        side = "right" if lane_id < 0 else "left"
        sign = -1.0 if lane_id < 0 else 1.0
        for k in range(1, abs(lane_id) + 1):
            lane = section.find(f"{side}/lane[@id='{int(sign) * k}']")
            if lane is None:
                raise KeyError(f"road {self.id}: lane {int(sign) * k} not found")
            width = _piecewise(lane.findall("width"), ds_section, "sOffset")
            w = _poly(width, ds_section - float(width.get("sOffset"))) if width is not None else 0.0
            t += sign * (w if k < abs(lane_id) else w / 2)
        return t


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def check_continuity(xodr_path: PathLike) -> list[str]:
    return check_continuity_tree(etree.parse(str(xodr_path)))


def check_continuity_tree(tree: etree._ElementTree) -> list[str]:
    roads = {r.get("id"): RoadEval(r) for r in tree.getroot().findall("road")}
    errors: list[str] = []
    for rid, road in sorted(roads.items(), key=lambda kv: int(kv[0])):
        errors += _check_sections(road)
        for kind in ("predecessor", "successor"):
            link = road.road.find(f"link/{kind}")
            if link is None or link.get("elementType") != "road":
                continue
            other = roads.get(link.get("elementId"))
            if other is None:
                continue  # reported by the topology check
            s_here = 0.0 if kind == "predecessor" else road.length
            s_there = 0.0 if link.get("contactPoint") == "start" else other.length
            in_junction = road.road.get("junction", "-1") != "-1"
            for lane_id in sorted(road.lane_ids(s_here)):
                lane = road._section(s_here).find(f".//lane[@id='{lane_id}']")
                lane_link = lane.find(f"link/{kind}")
                where = f"road {rid} lane {lane_id} {kind} road {other.id}"
                if lane_link is None:
                    if in_junction:
                        errors.append(f"{where}: connecting-road lane has no lane link")
                    continue
                other_lane = int(lane_link.get("id"))
                where += f" lane {other_lane}"
                if other_lane not in other.lane_ids(s_there):
                    errors.append(f"{where}: lane does not exist")
                    continue
                errors += _compare(where, road, lane_id, s_here, other, other_lane, s_there)
    return errors


def _width(lane: etree._Element, ds: float) -> float:
    rec = _piecewise(lane.findall("width"), ds, "sOffset")
    return _poly(rec, ds - float(rec.get("sOffset"))) if rec is not None else 0.0


def _check_sections(road: RoadEval) -> list[str]:
    """Lanes across lane-section boundaries inside a road.

    A lane linked to the next section must exist there with the same width at
    the boundary, linked back; a lane without a successor must have narrowed
    to 0 by the boundary, and one without a predecessor must start at 0.
    """
    errors = []
    sections = road.sections
    for a, b in pairwise(sections):
        s_b = float(b.get("s"))
        ds = s_b - float(a.get("s"))
        where = f"road {road.id} at s={s_b:.3f}"
        for lane in a.iterfind("*/lane"):
            lid = lane.get("id")
            if lid == "0":
                continue
            w_end = _width(lane, ds)
            succ = lane.find("link/successor")
            if succ is None:
                if w_end > POS_TOL:
                    errors.append(f"{where}: lane {lid} ends {w_end:.3f} m wide without a successor")
                continue
            nxt = b.find(f"*/lane[@id='{succ.get('id')}']")
            if nxt is None:
                errors.append(f"{where}: lane {lid} successor {succ.get('id')} does not exist")
                continue
            if abs(_width(nxt, 0.0) - w_end) > POS_TOL:
                errors.append(f"{where}: lane {lid} width jumps {w_end:.3f} -> {_width(nxt, 0.0):.3f} m")
            back = nxt.find("link/predecessor")
            if back is None or back.get("id") != lid:
                errors.append(f"{where}: lane {succ.get('id')} does not link back to lane {lid}")
        for lane in b.iterfind("*/lane"):
            if lane.get("id") != "0" and lane.find("link/predecessor") is None and _width(lane, 0.0) > POS_TOL:
                errors.append(f"{where}: lane {lane.get('id')} starts {_width(lane, 0.0):.3f} m wide "
                              "without a predecessor")
    return errors


CURVATURE_TOL = 0.002  # [1/m] lane-centre curvature jump counted as a discontinuity


def check_curvature(xodr_path: PathLike) -> tuple[list[str], list[str]]:
    return check_curvature_tree(etree.parse(str(xodr_path)))


def check_curvature_tree(tree: etree._ElementTree) -> tuple[list[str], list[str]]:
    """(errors, warnings): curvature jumps (G2 breaks) inside roads and across lane links.

    Jumps at a paramPoly3 geometry (the fallback for junction turns a
    clothoid could not fit) are warnings; every other jump is an error.
    """
    roads = {r.get("id"): RoadEval(r) for r in tree.getroot().findall("road")}
    errors: list[str] = []
    warnings: list[str] = []

    def report(msg: str, *geoms: Optional[etree._Element]) -> None:
        poly = any(g is not None and g[0].tag == "paramPoly3" for g in geoms)
        (warnings if poly else errors).append(msg)

    for rid, road in sorted(roads.items(), key=lambda kv: int(kv[0])):
        geoms = road.geometries
        for prev, nxt in pairwise(geoms):
            s = float(nxt.get("s"))
            jump = abs(road.curvature(s, prev) - road.curvature(s, nxt))
            if jump > CURVATURE_TOL:
                report(f"road {rid} at s={s:.3f}: curvature jumps by {jump:.4f} 1/m", prev, nxt)
        for kind in ("predecessor", "successor"):
            link = road.road.find(f"link/{kind}")
            if link is None or link.get("elementType") != "road":
                continue
            other = roads.get(link.get("elementId"))
            if other is None:
                continue
            s_here = 0.0 if kind == "predecessor" else road.length
            s_there = 0.0 if link.get("contactPoint") == "start" else other.length
            for lane in road._section(s_here).iterfind("*/lane"):
                lane_link = lane.find(f"link/{kind}")
                if lane.get("id") == "0" or lane_link is None:
                    continue
                a, b = int(lane.get("id")), int(lane_link.get("id"))
                try:
                    jump = abs(road.lane_curvature(a, s_here) - other.lane_curvature(b, s_there))
                except KeyError:
                    continue  # reported by the continuity check
                if jump > CURVATURE_TOL:
                    report(f"road {rid} lane {a} {kind} road {other.id} lane {b}: curvature jumps by "
                           f"{jump:.4f} 1/m", _piecewise(road.geometries, s_here, "s"),
                           _piecewise(other.geometries, s_there, "s"))
    return errors, warnings


def _compare(
    where: str,
    a: RoadEval, lane_a: int, s_a: float,
    b: RoadEval, lane_b: int, s_b: float,
) -> list[str]:
    errors = []
    (xa, ya), (xb, yb) = a.lane_center(lane_a, s_a), b.lane_center(lane_b, s_b)
    gap = math.hypot(xa - xb, ya - yb)
    if gap > POS_TOL:
        errors.append(f"{where}: position mismatch {gap:.4f} m")
    dh = _angle_diff(a.lane_travel_heading(lane_a, s_a), b.lane_travel_heading(lane_b, s_b))
    if dh > HDG_TOL:
        errors.append(f"{where}: heading mismatch {dh:.4f} rad")
    za, zb = a.lane_height(lane_a, s_a), b.lane_height(lane_b, s_b)
    if abs(za - zb) > Z_TOL:
        errors.append(f"{where}: height mismatch {abs(za - zb):.4f} m")
    ga, gb = a.lane_grade(lane_a, s_a), b.lane_grade(lane_b, s_b)
    if abs(ga - gb) > GRADE_TOL:
        errors.append(f"{where}: grade mismatch {abs(ga - gb):.4f}")
    return errors
