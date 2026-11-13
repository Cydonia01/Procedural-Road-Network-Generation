"""Road surfaces of a written .xodr, for the scene (stage R4 / polish R4a-R4e).

The ground is cut along what esmini draws: every lane of every road and
connecting road, as the .xodr describes it (reference line, lane offset and
widths, elevation, superelevation and the lane <height> that raises curbs
and sidewalks). Per road the outer edge on each side is sampled every
``step`` with its height; the strips between them, outside bridges and
tunnels, and the junction fills (the surface no lane covers, drawn by
roadgen.scene.junctions) make up the footprint the ground leaves out. Bridge
and tunnel stretches are kept apart (Span) for the structures and for the
ground rules under bridges and over tunnels.

Nothing here reads the RoadGraph: the .xodr is the product, and the scene
follows it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import numpy as np
import shapely
from lxml import etree
from shapely.geometry import Polygon

from roadgen.opendrive.plot import _cubics
from roadgen.validate.continuity import RoadEval

log = logging.getLogger(__name__)

Outline = list[tuple[float, float, float]]
MIN_SAMPLED_WIDTH = 2.0  # narrower stretches of a road get no surface samples [m]


@dataclass
class Span:
    """A bridge or tunnel stretch of one road: centre line and outer edges with heights
    (n, 3), headings (n,), every step along it."""

    kind: str  # "bridge" | "tunnel"
    road: str
    centre: np.ndarray
    left: np.ndarray
    right: np.ndarray
    hdg: np.ndarray

    @property
    def half_width(self) -> float:
        return float(np.max(np.hypot(*(self.left[:, :2] - self.right[:, :2]).T)) / 2)


@dataclass
class RoadSurfaces:
    """What the ground needs to know about the roads esmini draws."""

    parts: list[Polygon] = field(default_factory=list)  # road strips and fills the ground leaves out
    edges: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (n, 3) points on their outlines
    spans: list[Span] = field(default_factory=list)  # bridges and tunnels
    samples: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (n, 3) on the driven surface

    @property
    def empty(self) -> bool:
        return not self.parts


@dataclass
class _Road:
    s: np.ndarray
    centre: np.ndarray  # (n, 3)
    left: np.ndarray  # (n, 3) outer edges
    right: np.ndarray
    hdg: np.ndarray
    bank: np.ndarray  # sin(superelevation)


def read_surfaces(xodr: Union[str, Path, etree._Element], fills: Optional[list[Outline]] = None,
                  step: float = 2.0) -> RoadSurfaces:
    """Road surfaces of an .xodr (path or parsed root), plus the junction fill outlines
    (sidecar "junction_fills") as part of the footprint."""
    root = xodr if isinstance(xodr, etree._Element) else etree.parse(str(xodr)).getroot()
    out = RoadSurfaces()
    edges, samples = [], []
    for road in root.findall("road"):
        structures = [(kind, float(el.get("s")), float(el.get("length")))
                      for kind in ("bridge", "tunnel") for el in road.findall(f"objects/{kind}")]
        r = _sample_road(road, step, [v for _, s0, length in structures for v in (s0, s0 + length)])
        if r is None:
            continue
        inside = np.zeros(len(r.s), bool)
        for kind, s0, length in structures:
            on = (r.s >= s0 - 1e-6) & (r.s <= s0 + length + 1e-6)
            inside |= (r.s > s0 + 1e-6) & (r.s < s0 + length - 1e-6)
            if on.sum() >= 2:
                out.spans.append(Span(kind, road.get("id"), r.centre[on], r.left[on], r.right[on], r.hdg[on]))
        for run in _runs(~inside):
            if len(run) < 2:
                continue
            left, right = r.left[run], r.right[run]
            poly = Polygon(np.vstack([left[:, :2], right[::-1, :2]]))
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 1e-3:
                continue
            out.parts.extend(g for g in getattr(poly, "geoms", [poly]) if g.geom_type == "Polygon")
            edges += [left, right, _segment(left[0], right[0], step), _segment(left[-1], right[-1], step)]
            # The driven surface, inside the edges (an edge itself may be raised by a sidewalk):
            # not at the strip's ends, which are on the footprint's outline where nothing
            # follows, and only where it is at least MIN_SAMPLED_WIDTH wide (not on the tails
            # of side lanes), clear of the ground reaching in under the edges.
            inner = run[1:-1]
            inner = inner[np.hypot(*(r.left[inner, :2] - r.right[inner, :2]).T) >= MIN_SAMPLED_WIDTH]
            for f in (0.25, 0.5, 0.75):
                samples.append(_across(r, inner, f))
    for outline in fills or []:
        if len(outline) < 3:
            continue
        pts = np.array(outline, float)
        poly = Polygon(pts[:, :2])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        out.parts.extend(g for g in getattr(poly, "geoms", [poly]) if g.geom_type == "Polygon")
        edges += [_segment(a, b, step) for a, b in zip(pts, np.roll(pts, -1, axis=0))]
    out.edges = np.vstack(edges) if edges else np.zeros((0, 3))
    out.samples = np.vstack(samples) if samples else np.zeros((0, 3))
    log.info("road surfaces: %d parts, %d edge points, %d bridges, %d tunnels", len(out.parts), len(out.edges),
             sum(sp.kind == "bridge" for sp in out.spans), sum(sp.kind == "tunnel" for sp in out.spans))
    return out


def _sample_road(road: etree._Element, step: float, extra: Optional[list[float]] = None) -> Optional[_Road]:
    """Centre line and outer edges (x, y, z) of a road every step, per lane section (both
    ends of each section, so tapers start where the .xodr says), and at every s of extra."""
    ev = RoadEval(road)
    if ev.length <= 1e-6:
        return None
    sections = ev.sections
    bounds = [float(sec.get("s")) for sec in sections] + [ev.length]
    elevation = road.findall("elevationProfile/elevation")
    superelevation = road.findall("lateralProfile/superelevation")
    ss_all, left_all, right_all = [], [], []
    for k, sec in enumerate(sections):
        s0, s1 = bounds[k], bounds[k + 1]
        if s1 - s0 <= 1e-6:
            continue
        ss = np.linspace(s0, s1, max(2, int(np.ceil((s1 - s0) / step)) + 1))
        ss = np.union1d(ss, [v for v in extra or [] if s0 < v < s1])
        if ss_all:
            ss = ss[1:]  # the previous section's last sample
        if not len(ss):
            continue
        offset = _cubics(ev.offsets, ss, "s")
        sides = {}
        for side, sign in (("right", -1.0), ("left", 1.0)):
            lanes = sorted(sec.findall(f"{side}/lane"), key=lambda lane: abs(int(lane.get("id"))))
            t = offset.copy()
            lift = np.zeros(len(ss))
            for lane in lanes:
                w = _cubics(lane.findall("width"), ss - s0, "sOffset")
                heights = lane.findall("height")
                h = _steps(heights, ss - s0) if heights else np.zeros(len(ss))
                wide = w > 1e-6
                t = t + sign * w
                lift = np.where(wide, h, lift)
            sides[side] = (t, lift)
        ss_all.append(ss)
        left_all.append(sides["left"])
        right_all.append(sides["right"])
    if not ss_all:
        return None
    s = np.concatenate(ss_all)
    ref = np.array([ev.reference(v) for v in s])
    z = _cubics(elevation, s, "s")
    bank = np.sin(_cubics(superelevation, s, "s"))
    normal = np.column_stack([-np.sin(ref[:, 2]), np.cos(ref[:, 2])])

    def edge(parts) -> np.ndarray:
        t = np.concatenate([p[0] for p in parts])
        lift = np.concatenate([p[1] for p in parts])
        return np.column_stack([ref[:, :2] + normal * t[:, None], z + t * bank + lift])

    centre = np.column_stack([ref[:, :2], z])
    return _Road(s, centre, edge(left_all), edge(right_all), ref[:, 2], bank)


def _steps(records: list[etree._Element], ds: np.ndarray) -> np.ndarray:
    """The outer value of piecewise-constant <height> records at every ds."""
    starts = np.array([float(r.get("sOffset", "0")) for r in records])
    values = np.array([float(r.get("outer", "0")) for r in records])
    return values[np.maximum(np.searchsorted(starts, ds + 1e-9, side="right") - 1, 0)]


def _runs(mask: np.ndarray) -> list[np.ndarray]:
    """Index runs where mask holds."""
    out, k, n = [], 0, len(mask)
    while k < n:
        if mask[k]:
            j = k
            while j < n and mask[j]:
                j += 1
            out.append(np.arange(k, j))
            k = j
        else:
            k += 1
    return out


def _segment(a: np.ndarray, b: np.ndarray, step: float) -> np.ndarray:
    n = max(2, int(np.ceil(np.hypot(*(b[:2] - a[:2])) / step)) + 1)
    return a + np.linspace(0.0, 1.0, n)[:, None] * (b - a)


def _across(r: _Road, run: np.ndarray, f: float) -> np.ndarray:
    """Points a share f across from the right to the left edge, on the banked surface
    through the centre line (without the side lanes' lift)."""
    left, right, centre = r.left[run], r.right[run], r.centre[run]
    xy = right[:, :2] + f * (left[:, :2] - right[:, :2])
    normal = np.column_stack([-np.sin(r.hdg[run]), np.cos(r.hdg[run])])
    t = np.einsum("ij,ij->i", xy - centre[:, :2], normal)
    return np.column_stack([xy, centre[:, 2] + t * r.bank[run]])


def footprint(parts: list[Polygon]):
    """The union of the parts (one geometry)."""
    return shapely.union_all(parts) if parts else Polygon()
