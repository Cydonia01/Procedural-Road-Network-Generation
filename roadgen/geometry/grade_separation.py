"""Grade separation (stage C8): roads that cross without a junction pass over each other.

A RoadGraph is planar except where a generator lets two roads cross without a
shared node (lsystem's highway_crossings: separated). For each such crossing
the higher road class goes over (highway > avenue > street; the lower edge id
on a tie): it is raised by a smooth bump so that it clears the lower road by
`clearance` (vertical clearance plus deck), with ramps no steeper than
`max_grade` and a flat top `flat_margin` beyond the lower road's edges, which
becomes a bridge span (Edge.bridges, so the ground is not levelled under it).

The bump follows the upper road along the graph: from the crossing edge on
through its straightest continuation at each node (same rank, at most
CONTINUATION_DEG off). Roads joining the raised stretch are ramped down at
the same grade from the height they meet it at, so no junction gets a step;
the lower road is never raised. Heights add to those already in the graph
(drape), so this runs after drape, also on a flat graph.

Crossings are found on the fitted roads the builder will draw
(roadgen.geometry.alignment), like drape samples them: a curve can move a
crossing tens of metres from where the straight edges cross. The flat top
also covers a skewed crossing, where the lower road spans a longer stretch
of the upper one (its half width / sin of the crossing angle).

Neutral geometry: RoadGraph in, RoadGraph out, deterministic. The crossings
are listed in graph.metadata["overpasses"].
"""

from __future__ import annotations

import copy
import heapq
import logging
import math
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
from shapely.geometry import LineString
from shapely.strtree import STRtree

from roadgen.config import DEFAULTS, section
from roadgen.geometry.alignment import NetworkAlignment, half_width, network_alignment
from roadgen.graph.model import RoadGraph

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "grade_separation")
RANK = {"highway": 2, "avenue": 1, "street": 0}
CONTINUATION_DEG = 45.0  # a road continues through a node into an edge at most this far off straight
RAMP_FACTOR = 1.5  # a smoothstep of height H over length L climbs at most 1.5 H / L
LINE_STEP = 2.0  # [m] between the points of a fitted road's polyline
MIN_SIN = 0.3  # skew factor cap: crossings flatter than ~17 deg get no wider top


@dataclass(frozen=True)
class GradeSeparationParams:
    clearance: float = _D["clearance"]  # [m] between the lower and the upper road surface
    max_grade: float = _D["max_grade"]  # steepest ramp, rise / run
    flat_margin: float = _D["flat_margin"]  # [m] flat top beyond each edge of the lower road
    sample_spacing: float = _D["sample_spacing"]  # [m] most distance between profile points

    def __post_init__(self) -> None:
        if min(self.clearance, self.max_grade, self.sample_spacing) <= 0 or self.flat_margin < 0:
            raise ValueError("need clearance, max_grade, sample_spacing > 0 and flat_margin >= 0")


@dataclass(frozen=True)
class Crossing:
    upper: int  # edge ids
    lower: int
    x: float
    y: float
    d_upper: float  # [m] from the upper edge's from_node, along the straight edge
    d_lower: float  # [m] from the lower edge's from_node
    sin: float = 1.0  # sine of the crossing angle


def find_crossings(graph: RoadGraph, na: Optional[NetworkAlignment] = None) -> list[Crossing]:
    """Pairs of edges that cross without a shared node, by edge id.

    The pairs are those whose straight edges cross (the graph's own crossings);
    with na, each is placed where the fitted roads cross (closest to the straight
    crossing), positions still as distances along the straight edges.
    """
    ids = sorted(graph.edges)
    straight = [_Line(graph, e, None) for e in ids]
    tree = STRtree([line.geom for line in straight])
    out = []
    for i, eid in enumerate(ids):
        e = graph.edges[eid]
        for j in tree.query(straight[i].geom, predicate="crosses"):
            if j <= i:
                continue
            f = graph.edges[ids[j]]
            if {e.from_node, e.to_node} & {f.from_node, f.to_node}:
                continue
            p0 = straight[i].geom.intersection(straight[j].geom)
            if p0.geom_type != "Point":
                continue
            li, lj, pt = straight[i], straight[j], p0
            if na is not None:
                fi, fj = _Line(graph, eid, na), _Line(graph, ids[j], na)
                hit = fi.geom.intersection(fj.geom)
                pts = [g for g in ([hit] if hit.geom_type == "Point" else getattr(hit, "geoms", []))
                       if g.geom_type == "Point"]
                if pts:
                    li, lj, pt = fi, fj, min(pts, key=lambda q: q.distance(p0))
            upper_first = (RANK[e.road_class], -e.id) >= (RANK[f.road_class], -f.id)
            lu, ll = (li, lj) if upper_first else (lj, li)
            sin = abs(math.sin(lu.heading(pt) - ll.heading(pt)))
            out.append(Crossing(lu.eid, ll.eid, pt.x, pt.y, lu.distance(pt), ll.distance(pt), sin))
    return out


class _Line:
    """An edge's road as a polyline (fitted where na is given), with the straight-edge
    distance and the heading at a point on it."""

    def __init__(self, graph: RoadGraph, eid: int, na: Optional[NetworkAlignment]) -> None:
        self.eid = eid
        length = graph.edge_length(eid)
        self.d = np.linspace(0.0, length, max(2, math.ceil(length / LINE_STEP) + 1))
        if na is not None:
            x, y = na.edge_points(graph, eid, self.d)
        else:
            e = graph.edges[eid]
            a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
            t = self.d / length if length else self.d
            x, y = a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)
        self.x, self.y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        self.geom = LineString(np.column_stack([self.x, self.y]))
        self.arc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(self.x), np.diff(self.y)))])

    def distance(self, pt) -> float:
        return float(np.interp(self.geom.project(pt), self.arc, self.d))

    def heading(self, pt) -> float:
        k = int(np.clip(np.searchsorted(self.arc, self.geom.project(pt)), 1, len(self.arc) - 1))
        return math.atan2(self.y[k] - self.y[k - 1], self.x[k] - self.x[k - 1])


def _height(graph: RoadGraph, eid: int, d: float) -> float:
    dd, zz = graph.edge_profile(eid)
    return float(np.interp(d, dd, zz))


def _smooth_drop(x: np.ndarray) -> np.ndarray:
    """1 at x <= 0, 0 at x >= 1, a smoothstep in between."""
    t = np.clip(x, 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


class _Bump:
    """Raise of one crossing: H on the flat top, smoothstep ramps of length L."""

    def __init__(self, height: float, flat: float, grade: float) -> None:
        self.height, self.flat = height, flat
        self.ramp = RAMP_FACTOR * height / grade if height > 0 else 0.0

    @property
    def reach(self) -> float:
        return self.flat + self.ramp

    def __call__(self, dist: np.ndarray) -> np.ndarray:
        if self.height <= 0:
            return np.zeros_like(dist)
        return self.height * _smooth_drop((np.abs(dist) - self.flat) / self.ramp)


def _walk_line(graph: RoadGraph, c: Crossing, reach: float) -> dict[int, tuple[float, int]]:
    """Edges of the upper road within reach of the crossing: edge -> (along-road
    distance of its from_node from the crossing, +1 / -1 direction of d)."""
    e = graph.edges[c.upper]
    rank = RANK[e.road_class]
    out = {e.id: (-c.d_upper, 1)}
    for node, dist, sign in ((e.to_node, graph.edge_length(e.id) - c.d_upper, 1), (e.from_node, c.d_upper, -1)):
        prev = e
        while dist < reach:
            nxt = _continuation(graph, node, prev, rank)
            if nxt is None or nxt.id in out:
                break
            length = graph.edge_length(nxt.id)
            if nxt.from_node == node:  # d grows away from the crossing
                out[nxt.id] = (sign * dist, sign)
                node = nxt.to_node
            else:  # the edge points at the crossing
                out[nxt.id] = (sign * (dist + length), -sign)
                node = nxt.from_node
            dist += length
            prev = nxt
    return out


def _continuation(graph: RoadGraph, node: int, prev, rank: int):
    n = graph.nodes[node]
    p = graph.nodes[prev.from_node if prev.to_node == node else prev.to_node]
    h_in = math.atan2(n.y - p.y, n.x - p.x)
    best, best_dev = None, math.radians(CONTINUATION_DEG)
    for e in graph.incident_edges(node):
        if e.id == prev.id or RANK[e.road_class] != rank:
            continue
        o = graph.nodes[e.to_node if e.from_node == node else e.from_node]
        dev = abs((math.atan2(o.y - n.y, o.x - n.x) - h_in + math.pi) % (2 * math.pi) - math.pi)
        if dev <= best_dev:
            best, best_dev = e, dev
    return best


def separate(graph: RoadGraph, params: GradeSeparationParams = GradeSeparationParams(),
             design=None, junction_margin: Optional[float] = None) -> RoadGraph:
    """A copy of graph with every crossing without a junction grade-separated.

    design, junction_margin: as for the OpenDRIVE builder, so the crossings are
    those of the roads it draws.
    """
    out = copy.deepcopy(graph)
    crossings = find_crossings(out, fitted(out, design, junction_margin))
    if not crossings:
        return out
    lower_edges = {c.lower for c in crossings}
    protected = {n for eid in lower_edges for n in (out.edges[eid].from_node, out.edges[eid].to_node)}
    line_raise: dict[int, list[tuple[_Bump, float, int]]] = {}  # edge -> bumps along it
    node_raise: dict[int, float] = {}
    records = []
    for c in crossings:
        up, low = out.edges[c.upper], out.edges[c.lower]
        gap = _height(out, c.lower, c.d_lower) + params.clearance - _height(out, c.upper, c.d_upper)
        flat = half_width(low) / max(c.sin, MIN_SIN) + params.flat_margin
        bump = _Bump(max(0.0, gap), flat, params.max_grade)
        for eid, (start, sign) in _walk_line(out, c, bump.reach).items():
            line_raise.setdefault(eid, []).append((bump, start, sign))
            e = out.edges[eid]
            for node, d in ((e.from_node, start), (e.to_node, start + sign * out.edge_length(eid))):
                r = float(bump(np.array(d)))
                if node in protected:
                    if r > 0:
                        log.warning("grade separation: node %d ends a road passing under another "
                                    "crossing and stays down; its upper road gets a step", node)
                    continue
                node_raise[node] = max(node_raise.get(node, 0.0), r)
        half = bump.flat
        bridge = (max(0.0, c.d_upper - half), min(out.edge_length(up.id), c.d_upper + half))
        length = out.edge_length(up.id)
        up.bridges = [(a, min(b, length)) for a, b in _merge([*up.bridges, bridge]) if min(b, length) > a]
        records.append({"upper": up.id, "lower": low.id, "point": [round(c.x, 3), round(c.y, 3)],
                        "raise": round(bump.height, 3)})
    _spread(out, node_raise, set(line_raise) | lower_edges, protected, params.max_grade)
    _apply(out, line_raise, node_raise, lower_edges, params)
    out.metadata["overpasses"] = records
    out.metadata["grade_separation"] = asdict(params)
    log.info("grade separation: %d crossings, highest raise %.1f m", len(records),
             max(r["raise"] for r in records))
    return out


def fitted(graph: RoadGraph, design=None, junction_margin: Optional[float] = None
           ) -> Optional[NetworkAlignment]:
    """The builder's alignment of graph, or None where it can't be built."""
    from roadgen.geometry.design import DesignParams
    from roadgen.geometry.junction_space import DEFAULT_MARGIN

    try:
        return network_alignment(graph, design or DesignParams(),
                                 DEFAULT_MARGIN if junction_margin is None else junction_margin)
    except ValueError as e:
        log.warning("grade separation: no fitted roads (%s); crossings of the straight edges", e)
        return None


def _spread(graph: RoadGraph, node_raise: dict[int, float], fixed: set[int], protected: set[int],
            grade: float) -> None:
    """Raise the nodes of roads joining raised nodes: each step down a road of
    length L keeps r (1 - smoothstep(L / ramp(r))), the largest such value wins."""
    heap = [(-r, n) for n, r in node_raise.items() if r > 0]
    heapq.heapify(heap)
    while heap:
        r, n = heapq.heappop(heap)
        r = -r
        if r < node_raise.get(n, 0.0) - 1e-12:
            continue
        for e in graph.incident_edges(n):
            if e.id in fixed:
                continue
            far = e.to_node if e.from_node == n else e.from_node
            if far in protected:
                continue
            rf = r * float(_smooth_drop(np.array(graph.edge_length(e.id) / (RAMP_FACTOR * r / grade))))
            if rf > node_raise.get(far, 0.0) + 1e-9:
                node_raise[far] = rf
                heapq.heappush(heap, (-rf, far))


def _apply(graph: RoadGraph, line_raise: dict, node_raise: dict[int, float], lower_edges: set[int],
           params: GradeSeparationParams) -> None:
    """Heights: every edge resampled at sample_spacing, raised; then the nodes."""
    new_profiles = {}
    for eid, e in graph.edges.items():
        length = graph.edge_length(eid)
        ra, rb = node_raise.get(e.from_node, 0.0), node_raise.get(e.to_node, 0.0)
        if eid not in line_raise and (eid in lower_edges or (ra <= 0 and rb <= 0)):
            continue
        n = max(len(e.profile), math.ceil(length / params.sample_spacing) - 1, 0)
        d = np.linspace(0.0, length, n + 2)
        dd, zz = graph.edge_profile(eid)
        z = np.interp(d, dd, zz)
        if eid in line_raise:
            raise_ = np.max([bump(start + sign * d) for bump, start, sign in line_raise[eid]], axis=0)
        else:  # a road joining raised nodes ramps down from each end
            def ramp(r: float, x: np.ndarray) -> np.ndarray:
                return r * _smooth_drop(x / (RAMP_FACTOR * r / params.max_grade)) if r > 0 else 0 * x
            raise_ = np.maximum(ramp(ra, d), ramp(rb, length - d))
        new_profiles[eid] = [round(float(v), 3) for v in (z + raise_)[1:-1]]
    for eid, profile in new_profiles.items():
        graph.edges[eid].profile = profile
    for n, r in node_raise.items():
        graph.nodes[n].z = round(graph.nodes[n].z + r, 3)


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def clearance_at(graph: RoadGraph, c: Crossing) -> float:
    """Vertical gap [m] between the upper and the lower road at a crossing."""
    return _height(graph, c.upper, c.d_upper) - _height(graph, c.lower, c.d_lower)
