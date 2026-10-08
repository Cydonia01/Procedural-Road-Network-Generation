"""L-system-style road growth after Parish & Müller (2001), "Procedural
Modeling of Cities".

Road segments are proposed into a priority queue (lower time = earlier).
Each popped proposal passes local constraints, which may shorten, redirect
or reject it:

1. bounds: proposals ending outside the area are dropped;
2. intersect: a proposal crossing an existing road stops at the first
   crossing, which becomes a junction (the crossed road is split there);
3. snap: an end within snap_radius of an existing node ends on that node;
   an end within snap_radius of a road ends on that road (split);
4. minimum length, no duplicate road, and a minimum angle to every road
   already at either end.

An accepted segment with a free end asks the global goal for follow-ups.
The "grid" goal proposes a straight continuation (time + 1) and, with
branch_prob, perpendicular branches (time + branch_delay). Headings are
pulled back to the grid axes every step, so angle_jitter_deg never drifts.

Everything is computed in the area's local frame (lower-left corner at 0,
x along the grid's first axis) and rotated/translated to world coordinates
at the end. Output is deterministic for a seed.
"""

from __future__ import annotations

import heapq
import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional

import numpy as np

from roadgen.generators.base import RoadNetworkGenerator, assign_node_kinds, register
from roadgen.graph.model import Edge, Node, RoadGraph

log = logging.getLogger(__name__)

GlobalGoal = Literal["grid"]
GLOBAL_GOALS = ("grid",)
Point = tuple[float, float]
EPS = 1e-9


@dataclass(frozen=True)
class LSystemParams:
    global_goal: GlobalGoal = "grid"
    extent: tuple[float, float] = (1000.0, 600.0)  # area width (local x), height (local y) [m]
    origin: tuple[float, float] = (0.0, 0.0)  # world position of the area's lower-left corner
    rotation_deg: float = 0.0  # rotation of the local frame around origin
    start: tuple[float, float] = (0.5, 0.5)  # seed point, as a fraction of the extent
    block_size: tuple[float, float] = (250.0, 80.0)  # grid goal: segment length along local x / y
    branch_prob: float = 1.0  # chance of each perpendicular branch at a free end
    branch_delay: int = 3  # queue delay of branches relative to straight growth
    angle_jitter_deg: float = 0.0  # std dev of heading noise around the grid axes
    length_jitter: float = 0.0  # relative std dev of segment length
    snap_radius: float = 15.0  # [m]
    min_segment_length: float = 30.0  # [m]
    min_angle_deg: float = 30.0  # min angle between roads meeting at a node
    max_segments: int = 20000
    # Roads along local y are "avenues" (major), along local x "streets" (minor).
    major_lanes: tuple[int, int] = (2, 2)
    minor_lanes: tuple[int, int] = (1, 1)
    lane_width: float = 3.5
    major_speed: float = 13.9
    minor_speed: float = 11.1

    def __post_init__(self) -> None:
        if self.global_goal not in GLOBAL_GOALS:
            raise ValueError(f"unknown global_goal {self.global_goal!r}; available: {GLOBAL_GOALS}")
        if min(self.extent) <= 0 or min(self.block_size) <= 0:
            raise ValueError("extent and block_size must be positive")
        if not all(0.0 <= f <= 1.0 for f in self.start):
            raise ValueError("start is a fraction of the extent, each in [0, 1]")
        if not 0.0 <= self.branch_prob <= 1.0:
            raise ValueError("branch_prob must be in [0, 1]")
        if self.snap_radius < 0 or self.min_segment_length <= 0:
            raise ValueError("snap_radius must be >= 0 and min_segment_length > 0")
        if self.snap_radius * 2 >= min(self.block_size):
            raise ValueError("snap_radius must be well below the block size")
        if not 0.0 < self.min_angle_deg < 90.0:
            raise ValueError("min_angle_deg must be in (0, 90)")
        for lanes in (self.major_lanes, self.minor_lanes):
            if len(lanes) != 2 or min(lanes) < 0 or sum(lanes) < 1:
                raise ValueError(f"invalid lane counts {lanes}")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "LSystemParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # Exactly a 10 x 10 Manhattan grid: grown from a corner, branching everywhere.
    "grid": {
        "extent": (2250.0, 720.0),
        "start": (0.0, 0.0),
        "block_size": (250.0, 80.0),
        "branch_prob": 1.0,
    },
    # Same goal with noise: wobbly headings and lengths, some missing branches.
    "grid_organic": {
        "extent": (1500.0, 900.0),
        "start": (0.5, 0.5),
        "block_size": (200.0, 100.0),
        "branch_prob": 0.85,
        "angle_jitter_deg": 6.0,
        "length_jitter": 0.12,
        "snap_radius": 20.0,
        "min_segment_length": 50.0,
        "min_angle_deg": 60.0,
    },
}
LSystemParams.PRESETS = PRESETS  # type: ignore[attr-defined]


@dataclass(order=True)
class _Proposal:
    t: int
    seq: int  # FIFO tie-break keeps the order deterministic
    node: int  # start node
    axis: int  # 0 = local x (minor road), 1 = local y (major road)
    sign: int  # +1 / -1 along the axis


class _SpatialHash:
    """Uniform grid of cells -> ids, for points and segments."""

    def __init__(self, cell: float) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], set[int]] = defaultdict(set)
        self.where: dict[int, list[tuple[int, int]]] = {}

    def _span(self, x0: float, y0: float, x1: float, y1: float, pad: float = 0.0):
        c = self.cell
        for i in range(math.floor((min(x0, x1) - pad) / c), math.floor((max(x0, x1) + pad) / c) + 1):
            for j in range(math.floor((min(y0, y1) - pad) / c), math.floor((max(y0, y1) + pad) / c) + 1):
                yield i, j

    def add(self, key: int, a: Point, b: Optional[Point] = None) -> None:
        b = b or a
        cells = list(self._span(a[0], a[1], b[0], b[1]))
        for c in cells:
            self.cells[c].add(key)
        self.where[key] = cells

    def remove(self, key: int) -> None:
        for c in self.where.pop(key, []):
            self.cells[c].discard(key)

    def query(self, a: Point, b: Point, pad: float) -> set[int]:
        found: set[int] = set()
        for c in self._span(a[0], a[1], b[0], b[1], pad):
            found |= self.cells.get(c, set())
        return found


def _segment_hit(p: Point, q: Point, a: Point, b: Point) -> Optional[tuple[float, float]]:
    """(u along p->q, v along a->b) of the crossing point, or None (incl. parallel)."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    sx, sy = b[0] - a[0], b[1] - a[1]
    den = rx * sy - ry * sx
    if abs(den) < EPS:
        return None
    qpx, qpy = a[0] - p[0], a[1] - p[1]
    u = (qpx * sy - qpy * sx) / den
    v = (qpx * ry - qpy * rx) / den
    if -EPS <= u <= 1 + EPS and -EPS <= v <= 1 + EPS:
        return u, v
    return None


def _project(pt: Point, a: Point, b: Point) -> tuple[float, float]:
    """(v along a->b clamped to [0, 1], distance from pt to that point)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    v = 0.0 if length2 < EPS else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / length2))
    px, py = a[0] + v * dx, a[1] + v * dy
    return v, math.hypot(pt[0] - px, pt[1] - py)


def _angle_between(u: Point, v: Point) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) /
                                                    (math.hypot(*u) * math.hypot(*v))))))


class _Growth:
    """Mutable network state while growing (local frame)."""

    def __init__(self, p: LSystemParams, rng: np.random.Generator) -> None:
        self.p, self.rng = p, rng
        self.pos: dict[int, Point] = {}
        self.edges: dict[int, tuple[int, int, int]] = {}  # id -> (a, b, axis)
        self.adj: dict[int, set[int]] = defaultdict(set)  # node -> edge ids
        cell = max(p.block_size)
        self.node_index, self.edge_index = _SpatialHash(cell), _SpatialHash(cell)
        self.next_node = self.next_edge = 1
        self.queue: list[_Proposal] = []
        self.seq = 0

    # --- mutation -----------------------------------------------------------------------

    def add_node(self, pt: Point) -> int:
        nid = self.next_node
        self.next_node += 1
        self.pos[nid] = pt
        self.node_index.add(nid, pt)
        return nid

    def add_edge(self, a: int, b: int, axis: int) -> int:
        eid = self.next_edge
        self.next_edge += 1
        self.edges[eid] = (a, b, axis)
        self.adj[a].add(eid)
        self.adj[b].add(eid)
        self.edge_index.add(eid, self.pos[a], self.pos[b])
        return eid

    def split_edge(self, eid: int, pt: Point) -> int:
        a, b, axis = self.edges.pop(eid)
        self.adj[a].discard(eid)
        self.adj[b].discard(eid)
        self.edge_index.remove(eid)
        mid = self.add_node(pt)
        self.add_edge(a, mid, axis)
        self.add_edge(mid, b, axis)
        return mid

    def propose(self, t: int, node: int, axis: int, sign: int) -> None:
        self.seq += 1
        heapq.heappush(self.queue, _Proposal(t, self.seq, node, axis, sign))

    # --- local constraints --------------------------------------------------------------

    def target(self, prop: _Proposal) -> Optional[tuple[str, Any]]:
        """Where the proposal ends: ("free", point), ("node", id) or ("split", (edge, point))."""
        p, rng = self.p, self.rng
        a = self.pos[prop.node]
        base = 0.0 if prop.axis == 0 else math.pi / 2
        heading = base + (0.0 if prop.sign > 0 else math.pi)
        if p.angle_jitter_deg:
            heading += math.radians(rng.normal(0.0, p.angle_jitter_deg))
        length = p.block_size[prop.axis]
        if p.length_jitter:
            length *= max(0.3, 1.0 + rng.normal(0.0, p.length_jitter))
        end = (a[0] + length * math.cos(heading), a[1] + length * math.sin(heading))

        w, h = p.extent
        if not (-EPS <= end[0] <= w + EPS and -EPS <= end[1] <= h + EPS):
            return None  # 1. bounds

        # 2. intersect: first crossing with a road not touching the start node.
        best = None
        for eid in self.edge_index.query(a, end, p.snap_radius):
            ea, eb, _ = self.edges[eid]
            if prop.node in (ea, eb):
                continue
            hit = _segment_hit(a, end, self.pos[ea], self.pos[eb])
            if hit and hit[0] > EPS and (best is None or hit[0] < best[0]):
                best = (hit[0], hit[1], eid)
        if best:
            u, v, eid = best
            ea, eb, _ = self.edges[eid]
            pt = (a[0] + u * (end[0] - a[0]), a[1] + u * (end[1] - a[1]))
            for n in (ea, eb):  # crossing right next to a node: use the node
                if math.dist(pt, self.pos[n]) <= p.snap_radius:
                    return "node", n
            return "split", (eid, pt)

        # 3. snap to a node, then to a road.
        near = [n for n in self.node_index.query(end, end, p.snap_radius) if n != prop.node]
        near = [(math.dist(end, self.pos[n]), n) for n in near]
        near = [(d, n) for d, n in near if d <= p.snap_radius]
        if near:
            return "node", min(near)[1]
        best_edge = None
        for eid in self.edge_index.query(end, end, p.snap_radius):
            ea, eb, _ = self.edges[eid]
            if prop.node in (ea, eb):
                continue
            v, d = _project(end, self.pos[ea], self.pos[eb])
            if d <= p.snap_radius and (best_edge is None or d < best_edge[0]):
                best_edge = (d, v, eid)
        if best_edge:
            _, v, eid = best_edge
            ea, eb, _ = self.edges[eid]
            pa, pb = self.pos[ea], self.pos[eb]
            return "split", (eid, (pa[0] + v * (pb[0] - pa[0]), pa[1] + v * (pb[1] - pa[1])))
        return "free", end

    def acceptable(self, a: int, end: Point, end_node: Optional[int], split_edge: Optional[int]) -> bool:
        """4. length, duplicates, angles, and no crossing of other roads."""
        p, pa = self.p, self.pos[a]
        if math.dist(pa, end) < p.min_segment_length:
            return False
        if end_node is not None:
            if end_node == a or any(end_node in self.edges[e][:2] for e in self.adj[a]):
                return False
        if split_edge is not None:  # pieces of a split road must stay long enough too
            ea, eb, _ = self.edges[split_edge]
            if min(math.dist(end, self.pos[ea]), math.dist(end, self.pos[eb])) < p.min_segment_length:
                return False
        direction = (end[0] - pa[0], end[1] - pa[1])
        for eid in self.adj[a]:
            other = self._other(eid, a)
            if _angle_between(direction, self._vec(a, other)) < p.min_angle_deg:
                return False
        back = (-direction[0], -direction[1])
        if end_node is not None:
            for eid in self.adj[end_node]:
                if _angle_between(back, self._vec(end_node, self._other(eid, end_node))) < p.min_angle_deg:
                    return False
        if split_edge is not None:  # the split road continues both ways from the new node
            ea, eb, _ = self.edges[split_edge]
            for n in (ea, eb):
                if _angle_between(back, (self.pos[n][0] - end[0], self.pos[n][1] - end[1])) < p.min_angle_deg:
                    return False
        # The (possibly snapped) segment must not cross any other road.
        touching = {a} | ({end_node} if end_node is not None else set())
        for eid in self.edge_index.query(pa, end, 0.0):
            if eid == split_edge:
                continue
            ea, eb, _ = self.edges[eid]
            if touching & {ea, eb}:
                continue
            hit = _segment_hit(pa, end, self.pos[ea], self.pos[eb])
            if hit and EPS < hit[0] < 1 - EPS:
                return False
        return True

    def _other(self, eid: int, n: int) -> int:
        a, b, _ = self.edges[eid]
        return b if a == n else a

    def _vec(self, a: int, b: int) -> Point:
        return self.pos[b][0] - self.pos[a][0], self.pos[b][1] - self.pos[a][1]

    # --- main loop ------------------------------------------------------------------------

    def run(self) -> None:
        p = self.p
        start = self.add_node((p.start[0] * p.extent[0], p.start[1] * p.extent[1]))
        for axis in (1, 0):
            for sign in (1, -1):
                self.propose(0, start, axis, sign)

        while self.queue and len(self.edges) < p.max_segments:
            prop = heapq.heappop(self.queue)
            tgt = self.target(prop)
            if tgt is None:
                continue
            kind, data = tgt
            if kind == "free":
                end, end_node, split = data, None, None
            elif kind == "node":
                end, end_node, split = self.pos[data], data, None
            else:
                split, end = data
                end_node = None
            if not self.acceptable(prop.node, end, end_node, split):
                continue
            if kind == "split":
                end_node = self.split_edge(split, end)
            elif kind == "free":
                end_node = self.add_node(end)
            self.add_edge(prop.node, end_node, prop.axis)
            if kind == "free":
                self.grow(prop, end_node)

    def grow(self, prop: _Proposal, node: int) -> None:
        """Global goal "grid": continue straight, branch perpendicular."""
        self.propose(prop.t + 1, node, prop.axis, prop.sign)
        other = 1 - prop.axis
        for sign in (1, -1):
            if self.rng.random() < self.p.branch_prob:
                self.propose(prop.t + self.p.branch_delay, node, other, sign)


@register("lsystem")
class LSystemGenerator(RoadNetworkGenerator[LSystemParams]):
    params_type = LSystemParams
    description = "Parish & Mueller style growth: priority queue, global goals, local constraints"

    def generate(self, params: LSystemParams, seed: int) -> RoadGraph:
        rng = np.random.default_rng(seed)
        growth = _Growth(params, rng)
        growth.run()
        return self._to_graph(growth, params, seed)

    @staticmethod
    def _to_graph(g: _Growth, p: LSystemParams, seed: int) -> RoadGraph:
        theta = math.radians(p.rotation_deg)
        c, s = math.cos(theta), math.sin(theta)
        graph = RoadGraph(metadata={
            "name": f"lsystem_{p.global_goal}_s{seed}",
            "generator": "lsystem",
            "seed": seed,
            "params": asdict(p),
        })
        # Renumber in creation order, dropping nodes that never got a road.
        used = sorted({n for a, b, _ in g.edges.values() for n in (a, b)})
        new_id = {old: k for k, old in enumerate(used, 1)}
        for old in used:
            x, y = g.pos[old]
            graph.add_node(Node(new_id[old], float(p.origin[0] + c * x - s * y),
                                float(p.origin[1] + s * x + c * y)))
        for k, (_, (a, b, axis)) in enumerate(sorted(g.edges.items()), 1):
            lanes = p.major_lanes if axis == 1 else p.minor_lanes
            graph.add_edge(Edge(
                k, new_id[a], new_id[b], "avenue" if axis == 1 else "street", *lanes,
                lane_width=p.lane_width, speed_limit=p.major_speed if axis == 1 else p.minor_speed,
            ))
        assign_node_kinds(graph)
        log.debug("lsystem: %d nodes, %d edges", len(graph.nodes), len(graph.edges))
        return graph
