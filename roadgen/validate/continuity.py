"""Lane-level geometric continuity across road links.

For every lane with a predecessor/successor lane link to another road, the
lane-center position and travel heading must agree at the contact point.
The .xodr is evaluated independently of the builder (line, arc and
paramPoly3 geometries, laneOffset and polynomial lane widths).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from lxml import etree

POS_TOL = 0.01  # m
HDG_TOL = 0.01  # rad
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

    def reference(self, s: float) -> tuple[float, float, float]:
        """(x, y, hdg) of the reference line at s."""
        geoms = self.road.findall("planView/geometry")
        g = _piecewise(geoms, s, "s")
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

    def lane_ids(self, s: float) -> set[int]:
        section = self._section(s)
        return {int(l.get("id")) for l in section.iter("lane") if l.get("id") != "0"}

    def lane_center(self, lane_id: int, s: float) -> tuple[float, float]:
        x, y, h = self.reference(s)
        t = self._lane_t(lane_id, s)
        return x - t * math.sin(h), y + t * math.cos(h)

    def lane_travel_heading(self, lane_id: int, s: float) -> float:
        """Heading of the lane-center curve in the RHT travel direction."""
        lo, hi = max(0.0, s - _FD_STEP), min(self.length, s + _FD_STEP)
        (x0, y0), (x1, y1) = self.lane_center(lane_id, lo), self.lane_center(lane_id, hi)
        hdg = math.atan2(y1 - y0, x1 - x0)
        return hdg if lane_id < 0 else hdg + math.pi

    def _section(self, s: float) -> etree._Element:
        sections = self.road.findall("lanes/laneSection")
        return _piecewise(sections, s, "s")

    def _lane_t(self, lane_id: int, s: float) -> float:
        offsets = self.road.findall("lanes/laneOffset")
        rec = _piecewise(offsets, s, "s")
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
    return errors
