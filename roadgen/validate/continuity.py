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
from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property
from itertools import pairwise
from pathlib import Path
from typing import Optional, Union

import numpy as np
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


class Records:
    """Piecewise cubic records (a, b, c, d after a start attribute), parsed once. The
    record covering x is the last one starting at or before x (+1e-9), else the first,
    as _piecewise picks it (records in document order, starts ascending)."""

    __slots__ = ("_arrays", "coef", "elements", "starts")

    def __init__(self, elements: list[etree._Element], key: str) -> None:
        self.elements = elements
        self.starts = [float(r.get(key)) for r in elements]
        self.coef = [tuple(float(r.get(k, 0)) for k in ("a", "b", "c", "d")) for r in elements]
        self._arrays: Optional[tuple[np.ndarray, np.ndarray]] = None

    def index(self, x: float) -> int:
        """Index of the record covering x; -1 without records."""
        if not self.starts:
            return -1
        return max(bisect_right(self.starts, x + 1e-9) - 1, 0)

    def value(self, x: float) -> float:
        """The covering record's cubic at x (0 without records)."""
        k = self.index(x)
        if k < 0:
            return 0.0
        a, b, c, d = self.coef[k]
        ds = x - self.starts[k]
        return a + b * ds + c * ds * ds + d * ds ** 3

    def slope(self, x: float) -> float:
        """d(value)/dx (0 without records)."""
        k = self.index(x)
        if k < 0:
            return 0.0
        _, b, c, d = self.coef[k]
        ds = x - self.starts[k]
        return b + 2 * c * ds + 3 * d * ds * ds

    def values(self, xs: np.ndarray) -> np.ndarray:
        """value() at every x of xs."""
        if not self.starts:
            return np.zeros(len(xs))
        a, b, c, d, ds = self._at(xs)
        return a + b * ds + c * ds * ds + d * ds ** 3

    def slopes(self, xs: np.ndarray) -> np.ndarray:
        """slope() at every x of xs."""
        if not self.starts:
            return np.zeros(len(xs))
        _, b, c, d, ds = self._at(xs)
        return b + 2 * c * ds + 3 * d * ds * ds

    def _at(self, xs: np.ndarray) -> tuple[np.ndarray, ...]:
        """(a, b, c, d, x - start) of the record covering each x."""
        if self._arrays is None:
            self._arrays = np.array(self.starts), np.array(self.coef)
        starts, coef = self._arrays
        idx = np.maximum(np.searchsorted(starts, xs + 1e-9, side="right") - 1, 0)
        a, b, c, d = coef[idx].T
        return a, b, c, d, xs - starts[idx]


@dataclass(frozen=True)
class _Geometry:
    """One <geometry> record, parsed."""

    s: float
    x: float
    y: float
    hdg: float
    length: float
    tag: str
    k0: float = 0.0  # arc: its curvature; spiral: curvStart
    k1: float = 0.0  # spiral: curvEnd
    u: tuple[float, ...] = ()  # paramPoly3: aU..dU
    v: tuple[float, ...] = ()  # paramPoly3: aV..dV
    normalized: bool = True  # paramPoly3: pRange
    piece: Optional[Piece] = None  # spiral

    @staticmethod
    def parse(g: etree._Element) -> _Geometry:
        child = g[0]
        s, x, y, h = (float(g.get(k)) for k in ("s", "x", "y", "hdg"))
        length = float(g.get("length"))
        tag = child.tag
        if tag == "arc":
            return _Geometry(s, x, y, h, length, tag, float(child.get("curvature")))
        if tag == "spiral":
            k0, k1 = float(child.get("curvStart")), float(child.get("curvEnd"))
            return _Geometry(s, x, y, h, length, tag, k0, k1, piece=Piece(Pose(x, y, h), length, k0, k1))
        if tag == "paramPoly3":
            return _Geometry(s, x, y, h, length, tag, u=tuple(float(child.get(f"{c}U")) for c in "abcd"),
                             v=tuple(float(child.get(f"{c}V")) for c in "abcd"),
                             normalized=child.get("pRange", "normalized") == "normalized")
        return _Geometry(s, x, y, h, length, tag)


@dataclass
class _Section:
    """One <laneSection>, parsed: its start, lane ids and per side the lane widths."""

    s: float
    ids: frozenset[int]
    widths: dict[int, Records]  # lane id -> width records (first lane of that id per side)


class RoadEval:
    """A <road> evaluated from its records: reference line, heights, lane centres.

    The records are parsed once per RoadEval (P3); one RoadEval per road can serve every
    validator (road_evals)."""

    def __init__(self, road: etree._Element) -> None:
        self.road = road

    @property
    def id(self) -> str:
        return self.road.get("id")

    @cached_property
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

    @cached_property
    def _geoms(self) -> list[_Geometry]:
        return [_Geometry.parse(g) for g in self.geometries]

    @cached_property
    def _geom_starts(self) -> list[float]:
        return [g.s for g in self._geoms]

    @cached_property
    def offset_records(self) -> Records:
        return Records(self.offsets, "s")

    @cached_property
    def elevation_records(self) -> Records:
        return Records(self.road.findall("elevationProfile/elevation"), "s")

    @cached_property
    def superelevation_records(self) -> Records:
        return Records(self.road.findall("lateralProfile/superelevation"), "s")

    @cached_property
    def _section_starts(self) -> list[float]:
        return [float(sec.get("s")) for sec in self.sections]

    @cached_property
    def _parsed_sections(self) -> list[_Section]:
        out = []
        for sec, s in zip(self.sections, self._section_starts):
            widths: dict[int, Records] = {}
            for side in ("right", "left"):
                for lane in sec.findall(f"{side}/lane"):
                    widths.setdefault(int(lane.get("id")), Records(lane.findall("width"), "sOffset"))
            ids = frozenset(int(lane.get("id")) for lane in sec.iter("lane") if lane.get("id") != "0")
            out.append(_Section(s, ids, widths))
        return out

    def _geometry_at(self, s: float) -> _Geometry:
        return self._geoms[max(bisect_right(self._geom_starts, s + 1e-9) - 1, 0)]

    def _section_index(self, s: float) -> int:
        return max(bisect_right(self._section_starts, s + 1e-9) - 1, 0)

    def reference(self, s: float) -> tuple[float, float, float]:
        """(x, y, hdg) of the reference line at s."""
        if not self._geoms:
            raise IndexError(f"road {self.id}: no geometry")
        return self._reference(self._geometry_at(s), s)

    def _reference(self, g: _Geometry, s: float) -> tuple[float, float, float]:
        x0, y0, h0 = g.x, g.y, g.hdg
        ds = s - g.s
        if g.tag == "line":
            return x0 + ds * math.cos(h0), y0 + ds * math.sin(h0), h0
        if g.tag == "arc":
            k = g.k0
            if abs(k) < 1e-12:
                return x0 + ds * math.cos(h0), y0 + ds * math.sin(h0), h0
            h = h0 + k * ds
            return x0 + (math.sin(h) - math.sin(h0)) / k, y0 - (math.cos(h) - math.cos(h0)) / k, h
        if g.tag == "spiral":
            piece = g.piece
            x, y = piece.xy(min(max(ds, 0.0), piece.length))
            return float(x), float(y), float(piece.heading(ds))
        if g.tag == "paramPoly3":
            p = ds / g.length if g.normalized else ds
            u, v = g.u, g.v
            pu = u[0] + u[1] * p + u[2] * p * p + u[3] * p ** 3
            pv = v[0] + v[1] * p + v[2] * p * p + v[3] * p ** 3
            du = u[1] + 2 * u[2] * p + 3 * u[3] * p * p
            dv = v[1] + 2 * v[2] * p + 3 * v[3] * p * p
            c, sn = math.cos(h0), math.sin(h0)
            return x0 + pu * c - pv * sn, y0 + pu * sn + pv * c, h0 + math.atan2(dv, du)
        raise NotImplementedError(f"road {self.id}: geometry <{g.tag}> not supported")

    def references(self, ss: np.ndarray) -> np.ndarray:
        """reference() at every station of ss, as an (n, 3) array of x, y, hdg."""
        ss = np.asarray(ss, dtype=float)
        out = np.empty((len(ss), 3))
        if not len(ss):
            return out
        idx = np.maximum(np.searchsorted(np.array(self._geom_starts), ss + 1e-9, side="right") - 1, 0)
        for k in np.unique(idx):
            at = idx == k
            g = self._geoms[k]
            ds = ss[at] - g.s
            x0, y0, h0 = g.x, g.y, g.hdg
            if g.tag == "line" or (g.tag == "arc" and abs(g.k0) < 1e-12):
                out[at] = np.column_stack([x0 + ds * math.cos(h0), y0 + ds * math.sin(h0), np.full(len(ds), h0)])
            elif g.tag == "arc":
                h = h0 + g.k0 * ds
                out[at] = np.column_stack([x0 + (np.sin(h) - math.sin(h0)) / g.k0,
                                           y0 - (np.cos(h) - math.cos(h0)) / g.k0, h])
            elif g.tag == "spiral":
                x, y = g.piece.xy(np.clip(ds, 0.0, g.piece.length))
                out[at] = np.column_stack([x, y, g.piece.heading(ds)])
            elif g.tag == "paramPoly3":
                p = ds / g.length if g.normalized else ds
                u, v = g.u, g.v
                pu = u[0] + u[1] * p + u[2] * p * p + u[3] * p ** 3
                pv = v[0] + v[1] * p + v[2] * p * p + v[3] * p ** 3
                du = u[1] + 2 * u[2] * p + 3 * u[3] * p * p
                dv = v[1] + 2 * v[2] * p + 3 * v[3] * p * p
                c, sn = math.cos(h0), math.sin(h0)
                out[at] = np.column_stack([x0 + pu * c - pv * sn, y0 + pu * sn + pv * c, h0 + np.arctan2(dv, du)])
            else:
                raise NotImplementedError(f"road {self.id}: geometry <{g.tag}> not supported")
        return out

    def curvature(self, s: float, geometry: Optional[etree._Element] = None) -> float:
        """Signed curvature [1/m] of the reference line at s (positive = turning left).

        geometry: evaluate this <geometry> element instead of the one covering s,
        e.g. the previous one at its end, to see both sides of a geometry boundary.
        """
        g = self._geometry_at(s) if geometry is None else self._parsed(geometry)
        ds = s - g.s
        if g.tag == "line":
            return 0.0
        if g.tag == "arc":
            return g.k0
        if g.tag == "spiral":
            return g.k0 + (g.k1 - g.k0) * ds / g.length
        if g.tag == "paramPoly3":
            p = ds / g.length if g.normalized else ds
            u, v = g.u, g.v
            du, dv = u[1] + 2 * u[2] * p + 3 * u[3] * p * p, v[1] + 2 * v[2] * p + 3 * v[3] * p * p
            ddu, ddv = 2 * u[2] + 6 * u[3] * p, 2 * v[2] + 6 * v[3] * p
            return (du * ddv - dv * ddu) / math.hypot(du, dv) ** 3
        raise NotImplementedError(f"road {self.id}: geometry <{g.tag}> not supported")

    def _parsed(self, geometry: etree._Element) -> _Geometry:
        for el, g in zip(self.geometries, self._geoms):
            if el is geometry:
                return g
        return _Geometry.parse(geometry)

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
        rec = self.elevation_records
        return rec.value(s), rec.slope(s)

    def superelevation(self, s: float) -> float:
        """Roll angle [rad] of the cross section at s (positive: falls to the right)."""
        return self.superelevation_records.value(s)

    def lane_height(self, lane_id: int, s: float) -> float:
        """Height [m] of the lane centre: the reference height plus t sin(roll) cos(pitch)."""
        return self.elevation(s)[0] + self._lane_t(lane_id, s) * self._lateral(s)[2]

    def lane_ids(self, s: float) -> set[int]:
        return set(self._parsed_sections[self._section_index(s)].ids)

    def _lateral(self, s: float) -> tuple[float, float, float]:
        """Unit vector across the road surface (heading, left, up components).

        The road surface at s is pitched by the grade and rolled by the
        superelevation about its pitched tangent (cos p, 0, sin p); the lateral
        axis (0, 1, 0) rolled about it is (-sin r sin p, cos r, sin r cos p).
        """
        r = self.superelevation(s)
        p = math.atan(self.elevation_records.slope(s))
        return -math.sin(r) * math.sin(p), math.cos(r), math.sin(r) * math.cos(p)

    def lane_center(self, lane_id: int, s: float) -> tuple[float, float]:
        """(x, y) of the lane centre: t measured across the pitched and rolled road surface."""
        x, y, h = self.reference(s)
        t = self._lane_t(lane_id, s)
        along, left, _ = self._lateral(s)
        return (x + t * (along * math.cos(h) - left * math.sin(h)),
                y + t * (along * math.sin(h) + left * math.cos(h)))

    def lane_points(self, lane_id: int, ss: np.ndarray) -> np.ndarray:
        """(x, y, z) of the lane centre at every station of ss, an (n, 3) array: lane_center
        and lane_height on arrays. KeyError if the lane is missing where asked."""
        ss = np.asarray(ss, dtype=float)
        ref = self.references(ss)
        t = self._lane_ts(lane_id, ss)
        r = self.superelevation_records.values(ss)
        p = np.arctan(self.elevation_records.slopes(ss))
        along, left, up = -np.sin(r) * np.sin(p), np.cos(r), np.sin(r) * np.cos(p)
        h = ref[:, 2]
        return np.column_stack([ref[:, 0] + t * (along * np.cos(h) - left * np.sin(h)),
                                ref[:, 1] + t * (along * np.sin(h) + left * np.cos(h)),
                                self.elevation_records.values(ss) + t * up])

    def _lane_ts(self, lane_id: int, ss: np.ndarray) -> np.ndarray:
        """_lane_t at every station of ss."""
        t = self.offset_records.values(ss)
        idx = np.maximum(np.searchsorted(np.array(self._section_starts), ss + 1e-9, side="right") - 1, 0)
        sign = -1 if lane_id < 0 else 1
        for k in np.unique(idx):
            at = idx == k
            section = self._parsed_sections[k]
            ds = ss[at] - section.s
            for j in range(1, abs(lane_id) + 1):
                widths = section.widths.get(sign * j)
                if widths is None:
                    raise KeyError(f"road {self.id}: lane {sign * j} not found")
                w = widths.values(ds)
                t[at] += sign * (w if j < abs(lane_id) else w / 2)
        return t

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
        return self.sections[self._section_index(s)]

    def parsed_section(self, s: float) -> _Section:
        """The lane section covering s, parsed (start, lane ids, width records)."""
        return self._parsed_sections[self._section_index(s)]

    def _lane_t(self, lane_id: int, s: float) -> float:
        t = self.offset_records.value(s)
        section = self._parsed_sections[self._section_index(s)]
        ds_section = s - section.s
        sign = -1 if lane_id < 0 else 1
        for k in range(1, abs(lane_id) + 1):
            widths = section.widths.get(sign * k)
            if widths is None:
                raise KeyError(f"road {self.id}: lane {sign * k} not found")
            w = widths.value(ds_section)
            t += sign * (w if k < abs(lane_id) else w / 2)
        return t


def road_evals(tree: etree._ElementTree) -> dict[str, RoadEval]:
    """Road id -> RoadEval of every road of a parsed .xodr. The validators of one file
    take one such dict (evals=...), so each road's records are parsed once (P2)."""
    return {r.get("id"): RoadEval(r) for r in tree.getroot().findall("road")}


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def check_continuity(xodr_path: PathLike) -> list[str]:
    return check_continuity_tree(etree.parse(str(xodr_path)))


def check_continuity_tree(tree: etree._ElementTree, evals: Optional[dict[str, RoadEval]] = None) -> list[str]:
    roads = evals if evals is not None else road_evals(tree)
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
                    # A connecting-road lane needs a link unless it is 0 m wide at that end
                    # (a side lane of a corner road that only one of its arms has).
                    ds = s_here - float(road._section(s_here).get("s"))
                    if in_junction and _width(lane, ds) > 1e-6:
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


def check_curvature_tree(tree: etree._ElementTree, evals: Optional[dict[str, RoadEval]] = None
                         ) -> tuple[list[str], list[str]]:
    """(errors, warnings): curvature jumps (G2 breaks) inside roads and across lane links.

    Jumps at a paramPoly3 geometry (the fallback for junction turns a
    clothoid could not fit) are warnings; every other jump is an error.
    """
    roads = evals if evals is not None else road_evals(tree)
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
