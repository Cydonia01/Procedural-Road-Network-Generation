"""Fitted reference lines: each chain's polyline turned into lines, spirals and arcs.

The single source of truth for road shape: the OpenDRIVE builder, the 3D
scene, the drape and the metrics all read the same NetworkAlignment, so
buildings, ground and bridges follow the curved road.

Per chain (see chains.py):

1. The polyline through the chain's nodes is trimmed at real junctions by
   junction_trims; arms leaving a junction stay straight.
2. Every inner node with deflection D gets a symmetric corner: spiral, arc,
   spiral (or two spirals when they alone turn by D). Its design shape uses
   the minimum radius for the design speed (the faster of the two edges) and
   the spiral length from design.spiral_length.
3. The corner is shrunk, keeping its shape, by a scale lambda <= 1 (lengths
   times lambda, curvature divided by it, so the tangent length T scales by
   lambda too):
   - to Node.radius_cap / a radius cap given by the caller,
   - to max_corner_offset (how far the curve cuts inside the corner),
   - so that the corners on each straight segment fit: T_a + T_b <= the
     segment's usable length, shrinking both in proportion where they don't,
   - so that the curve keeps road_clearance from every other road it did not
     already touch as a straight polyline (bisection on lambda).
4. Speeds are set by the innermost lane centre (the reference radius minus
   the offset to the farthest lane centre): the design radius includes that
   offset, and a corner that can't have it gets the advisory speed of the
   lane radius it has.

Stations: each graph node maps to a station on its chain road (an inner node to
its corner's midpoint, a chain end to minus its trim / length plus its trim),
and distances along the straight polyline map to stations piecewise linearly,
so edge ranges, height samples and bridges can be placed on the fitted road.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from roadgen.geometry import design
from roadgen.geometry.chains import Chain, find_chains, junction_nodes
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN, node_trims
from roadgen.geometry.planar import Pose, wrap_angle
from roadgen.geometry.primitives import Alignment, Piece, chain
from roadgen.graph.model import Edge, RoadGraph

log = logging.getLogger(__name__)

STRAIGHT_EPS = 1e-9  # [rad] deflections below this are no corner
LENGTH_EPS = 1e-9  # [m] pieces shorter than this are dropped
MIN_SCALE = 1e-3  # smallest corner scale tried by the clearance search
CLEARANCE_STEPS = 30  # bisection steps of the clearance search
SAMPLE_STEP = 1.0  # [m] curve sampling for clearance checks
END_TOL = 1e-4  # [m] allowed gap between the fitted road end and the trimmed polyline end

ClearanceCheck = Callable[[Chain, list[Piece], LineString, float], bool]


@dataclass(frozen=True)
class CornerShape:
    """A corner's design shape (scale 1), turning by deflection."""

    deflection: float  # signed [rad]
    kappa: float  # peak |curvature| [1/m]
    spiral: float  # length of each spiral [m]
    arc: float  # arc length [m]
    tangent: float  # distance from the vertex to each tangent point [m]
    offset: float  # distance from the vertex to the curve's midpoint [m]

    def pieces(self, start: Pose, scale: float) -> list[Piece]:
        k = math.copysign(self.kappa, self.deflection) / scale
        specs = [(self.spiral * scale, 0.0, k)]
        if self.arc * scale > LENGTH_EPS:
            specs.append((self.arc * scale, k, k))
        specs.append((self.spiral * scale, k, 0.0))
        return chain(start, specs)


def corner_shape(deflection: float, radius: float, spiral: float) -> CornerShape:
    """Symmetric spiral-arc-spiral corner; two spirals with a larger radius if they
    alone turn by the deflection. |deflection| must be below pi."""
    d = abs(deflection)
    if spiral / radius >= d:
        kappa, arc = d / spiral, 0.0
    else:
        kappa, arc = 1.0 / radius, (d - spiral / radius) * radius
    shape = CornerShape(deflection, kappa, spiral, arc, 0.0, 0.0)
    pieces = shape.pieces(Pose(0.0, 0.0, 0.0), 1.0)
    end = pieces[-1].end
    t_out = end.y / math.sin(deflection)  # tangent lines meet at the vertex
    tangent = end.x - t_out * math.cos(deflection)
    mid_s = sum(p.length for p in pieces) / 2
    mid = Alignment(pieces).pose(mid_s)
    offset = math.hypot(mid.x - tangent, mid.y)
    return CornerShape(deflection, kappa, spiral, arc, tangent, offset)


@dataclass(frozen=True)
class Corner:
    node: int
    deflection: float  # signed heading change [rad]
    radius: float  # smallest radius of the built corner's reference line [m]
    lane_radius: float  # ... of its innermost lane centre, which sets the speeds [m]
    spiral_length: float  # length of each spiral [m]
    s_start: float  # station of the first tangent point [m]
    s_end: float  # station of the second tangent point [m]
    design_speed: float  # [m/s]
    max_speed: float  # fastest comfortable speed on the built corner [m/s]
    advisory_speed: Optional[float]  # set when max_speed < design_speed [m/s]
    scale: float  # 1 = design shape, < 1 shrunk to fit
    shape: CornerShape  # the design shape (scale 1)
    vertex: tuple[float, float]  # the node's position [m]
    heading_in: float  # heading of the straight road into the corner [rad]

    def pieces(self, scale: Optional[float] = None) -> list[Piece]:
        """The corner's pieces at a scale (default: as built), from its first tangent point."""
        lam = self.scale if scale is None else scale
        t = lam * self.shape.tangent
        start = Pose(self.vertex[0] - t * math.cos(self.heading_in),
                     self.vertex[1] - t * math.sin(self.heading_in), self.heading_in)
        return self.shape.pieces(start, lam)


@dataclass(frozen=True)
class ChainAlignment:
    chain: Chain
    alignment: Alignment
    trim_start: float  # [m] cut off at the chain's start node
    trim_end: float  # [m] cut off at its end node
    corners: tuple[Corner, ...]
    node_poly: tuple[float, ...]  # distance of each chain node along the straight polyline [m]
    poly_breaks: tuple[float, ...]  # polyline distance -> station breakpoints
    station_breaks: tuple[float, ...]

    @property
    def length(self) -> float:
        return self.alignment.length

    def to_station(self, d_poly: float) -> float:
        """Station of a distance along the straight polyline (outside [0, length] in junctions)."""
        return float(np.interp(d_poly, self.poly_breaks, self.station_breaks))

    def node_station(self, node_id: int) -> float:
        return self.to_station(self.node_poly[self.chain.nodes.index(node_id)])

    def edge_index(self, edge_id: int) -> int:
        return self.chain.edge_ids.index(edge_id)

    def edge_range(self, edge_id: int) -> tuple[float, float]:
        """Stations covered by the edge on this road, clipped to the road."""
        k = self.edge_index(edge_id)
        s0, s1 = self.to_station(self.node_poly[k]), self.to_station(self.node_poly[k + 1])
        return max(0.0, s0), min(self.length, s1)

    def edge_station(self, edge_id: int, d: float) -> float:
        """Station of the point d [m] from the edge's from_node along the straight edge."""
        k = self.edge_index(edge_id)
        length = self.node_poly[k + 1] - self.node_poly[k]
        along = d if self.chain.edges[k].forward else length - d
        return self.to_station(self.node_poly[k] + along)


@dataclass(frozen=True)
class NetworkAlignment:
    chains: tuple[ChainAlignment, ...]
    junctions: tuple[int, ...]  # nodes that become OpenDRIVE junctions
    trims: Mapping[int, Mapping[int, float]]  # junction node -> edge -> trim [m]
    edge_chain: Mapping[int, int]  # edge id -> index into chains

    def of_edge(self, edge_id: int) -> ChainAlignment:
        return self.chains[self.edge_chain[edge_id]]

    def edge_points(self, graph: RoadGraph, edge_id: int, d) -> tuple[np.ndarray, np.ndarray]:
        """Positions on the fitted road of the points d [m] from the edge's from_node
        along the straight edge; inside a junction trim (off the road), on the edge."""
        ca = self.of_edge(edge_id)
        k = ca.edge_index(edge_id)
        d = np.asarray(d, dtype=float)
        length = ca.node_poly[k + 1] - ca.node_poly[k]
        along = d if ca.chain.edges[k].forward else length - d
        s = np.interp(ca.node_poly[k] + along, ca.poly_breaks, ca.station_breaks)
        e = graph.edges[edge_id]
        a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
        t = d / graph.edge_length(edge_id)
        x, y = a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)
        on = (s >= 0.0) & (s <= ca.length)
        if on.any():
            px, py, _ = ca.alignment.points(s[on])
            x, y = np.where(on, 0.0, x), np.where(on, 0.0, y)
            x[on], y[on] = px, py
        return x, y

    def edge_line(self, graph: RoadGraph, edge_id: int, step: float = 2.0) -> np.ndarray:
        """The edge's stretch of the fitted road as an (n, 2) polyline along its chain,
        extended straight to the junction node where the chain ends at one."""
        ca = self.of_edge(edge_id)
        k = ca.edge_index(edge_id)
        s0, s1 = ca.edge_range(edge_id)
        ss = np.linspace(s0, s1, max(2, math.ceil((s1 - s0) / step) + 1))
        x, y, _ = ca.alignment.points(ss)
        pts = np.column_stack([x, y])
        nodes = ca.chain.nodes
        if k == 0 and ca.trim_start > 0:
            pts = np.vstack([[graph.nodes[nodes[0]].x, graph.nodes[nodes[0]].y], pts])
        if k == len(ca.chain.edges) - 1 and ca.trim_end > 0:
            pts = np.vstack([pts, [graph.nodes[nodes[-1]].x, graph.nodes[nodes[-1]].y]])
        return pts


def half_width(edge: Edge) -> float:
    """Widest side of the road from its reference line [m]."""
    return max(edge.lanes_forward, edge.lanes_backward) * edge.lane_width


def _inner_offset(a: Edge, b: Edge) -> float:
    """Distance from the reference line to the farthest lane centre [m]: the innermost
    lane on whichever side the road turns to."""
    return max(half_width(e) - e.lane_width / 2 for e in (a, b))


def _class_of(a: Edge, b: Edge) -> str:
    return (a if a.speed_limit >= b.speed_limit else b).road_class


def network_alignment(graph: RoadGraph, params: DesignParams = DesignParams(),
                      junction_margin: float = DEFAULT_MARGIN,
                      radius_caps: Optional[Mapping[int, float]] = None) -> NetworkAlignment:
    """Chains, junction trims and fitted reference lines of the whole network.

    radius_caps: node id -> largest allowed corner radius [m], on top of
    Node.radius_cap.
    """
    chains = find_chains(graph, params.max_merge_deflection_deg)
    junctions = junction_nodes(graph, chains)
    trims = {n: node_trims(graph, n, junction_margin) for n in junctions}
    caps = dict(radius_caps or {})
    for n in graph.nodes.values():
        if n.radius_cap is not None:
            caps[n.id] = min(n.radius_cap, caps.get(n.id, math.inf))
    check = _clearance_check(graph, trims, params.road_clearance)
    fitted = tuple(align_chain(graph, c, trims, params, caps, check) for c in chains)
    edge_chain = {ce.edge_id: k for k, c in enumerate(chains) for ce in c.edges}
    return NetworkAlignment(fitted, tuple(junctions), trims, edge_chain)


def _trimmed_body(graph: RoadGraph, edge: Edge, trims: Mapping[int, Mapping[int, float]]) -> Optional[LineString]:
    a, b = graph.nodes[edge.from_node], graph.nodes[edge.to_node]
    length = graph.edge_length(edge.id)
    t0 = trims.get(edge.from_node, {}).get(edge.id, 0.0)
    t1 = trims.get(edge.to_node, {}).get(edge.id, 0.0)
    if t0 + t1 >= length:
        return None
    ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
    return LineString([(a.x + ux * t0, a.y + uy * t0), (b.x - ux * t1, b.y - uy * t1)])


def _clearance_check(graph: RoadGraph, trims: Mapping[int, Mapping[int, float]],
                     clearance: float) -> ClearanceCheck:
    """check(chain, curve pieces, straight corner polyline, half width) -> curve is clear.

    The curve may not touch, within clearance, any other road (straight and
    trimmed at junctions) that the straight corner polyline did not touch
    already: such contact is the generator's, not the fitting's.
    """
    ids, bodies = [], []
    for eid, e in sorted(graph.edges.items()):
        line = _trimmed_body(graph, e, trims)
        if line is not None:
            ids.append(eid)
            bodies.append(line.buffer(half_width(e)))
    tree = STRtree(bodies)

    def hits(geom: BaseGeometry, own: set[int]) -> set[int]:
        return {ids[i] for i in tree.query(geom, predicate="intersects")} - own

    def check(c: Chain, pieces: list[Piece], straight: LineString, width: float) -> bool:
        own = set(c.edge_ids)
        pad = width + clearance
        curve = LineString(np.concatenate([np.column_stack(p.xy(np.linspace(
            0.0, p.length, max(2, math.ceil(p.length / SAMPLE_STEP) + 1)))) for p in pieces]))
        new = hits(curve.buffer(pad), own)
        return not new or new <= hits(straight.buffer(pad), own)

    return check


def align_chain(graph: RoadGraph, c: Chain, trims: Mapping[int, Mapping[int, float]],
                params: DesignParams = DesignParams(), radius_caps: Optional[Mapping[int, float]] = None,
                clearance: Optional[ClearanceCheck] = None) -> ChainAlignment:
    caps = radius_caps or {}
    edges = [graph.edges[ce.edge_id] for ce in c.edges]
    pts = np.array([(graph.nodes[n].x, graph.nodes[n].y) for n in c.nodes])
    seg = np.diff(pts, axis=0)
    lengths = np.hypot(seg[:, 0], seg[:, 1])
    headings = np.arctan2(seg[:, 1], seg[:, 0])
    n = len(edges)
    trim_start = trims.get(c.start, {}).get(edges[0].id, 0.0)
    trim_end = trims.get(c.end, {}).get(edges[-1].id, 0.0)
    usable = lengths.copy()
    usable[0] -= trim_start
    usable[-1] -= trim_end
    for k in (0, n - 1):
        if usable[k] <= LENGTH_EPS:
            raise ValueError(
                f"edge {edges[k].id} is too short for its junctions: trims {trim_start:.3f} + "
                f"{trim_end:.3f} exceed segment length {lengths[k]:.3f}" if n == 1 else
                f"edge {edges[k].id} is too short for its junction: trim "
                f"{trim_start if k == 0 else trim_end:.3f} exceeds segment length {lengths[k]:.3f}")

    # Design corners at the inner nodes (index i = vertex i, 1..n-1).
    shapes: dict[int, CornerShape] = {}
    speeds: dict[int, tuple[float, str]] = {}
    scale: dict[int, float] = {}
    for i in range(1, n):
        a, b = edges[i - 1], edges[i]
        d = wrap_angle(float(headings[i] - headings[i - 1]))
        if abs(d) < STRAIGHT_EPS:
            continue
        v, cls = max(a.speed_limit, b.speed_limit), _class_of(a, b)
        e = design.e_max(cls, params) if params.banking else 0.0
        width = max(half_width(a), half_width(b))
        inner = _inner_offset(a, b)
        r = design.min_radius(v, e, params) + inner  # the innermost lane gets the design radius
        spiral = design.spiral_length(v, r, design.superelevation(r, v, cls, params), width, params)
        shapes[i] = corner_shape(d, r, spiral)
        speeds[i] = (v, cls, inner)
        lam = 1.0
        cap = caps.get(c.nodes[i])
        if cap is not None:
            lam = min(lam, cap * shapes[i].kappa)
        if params.max_corner_offset > 0:
            lam = min(lam, params.max_corner_offset / shapes[i].offset)
        scale[i] = max(lam, MIN_SCALE)

    def tangent(i: int) -> float:
        return scale[i] * shapes[i].tangent if i in shapes else 0.0

    # Fit the corners on each segment, shrinking both ends' corners in proportion.
    for _ in range(4 * n + 4):
        changed = False
        for j in range(n):
            need = tangent(j) + tangent(j + 1)
            if need > usable[j] + LENGTH_EPS:
                f = usable[j] / need * (1 - 1e-12)
                for i in (j, j + 1):
                    if i in scale:
                        scale[i] *= f
                changed = True
        if not changed:
            break

    # Keep clear of other roads.
    if clearance is not None:
        for i, shape in shapes.items():
            width = max(half_width(edges[i - 1]), half_width(edges[i]))
            ok = _corner_check(clearance, c, pts[i], float(headings[i - 1]), float(headings[i]), shape, width)
            if ok(scale[i]):
                continue
            lo, hi = MIN_SCALE, scale[i]
            if not ok(lo):
                log.debug("chain %s: corner at node %d is not clear even when tiny", c.edge_ids, c.nodes[i])
                scale[i] = lo
                continue
            for _ in range(CLEARANCE_STEPS):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if ok(mid) else (lo, mid)
            scale[i] = lo

    # Build the pieces and the station map.
    u0 = np.array([math.cos(headings[0]), math.sin(headings[0])])
    pose = Pose(*(pts[0] + u0 * trim_start), float(headings[0]))
    pieces: list[Piece] = []
    corners: list[Corner] = []
    node_poly = np.concatenate([[0.0], np.cumsum(lengths)])
    poly_breaks, station_breaks = [0.0, trim_start], [-trim_start, 0.0]
    s = 0.0
    for j in range(n):
        straight = max(0.0, usable[j] - tangent(j) - tangent(j + 1))
        if straight > LENGTH_EPS:
            pieces.append(Piece(pose, straight))
            pose, s = pieces[-1].end, s + straight
        i = j + 1
        if i == n:
            break
        if i not in shapes:
            poly_breaks.append(node_poly[i])
            station_breaks.append(s)
            continue
        shape, lam = shapes[i], scale[i]
        cps = shape.pieces(pose, lam)
        total = sum(p.length for p in cps)
        poly_breaks += [node_poly[i] - tangent(i), node_poly[i], node_poly[i] + tangent(i)]
        station_breaks += [s, s + total / 2, s + total]
        v, cls, inner = speeds[i]
        radius = lam / shape.kappa
        lane_radius = max(radius - inner, 1e-3)
        e = design.superelevation(lane_radius, v, cls, params)
        vmax = design.max_speed(lane_radius, e, params)
        corners.append(Corner(
            node=c.nodes[i], deflection=shape.deflection, radius=radius, lane_radius=lane_radius,
            spiral_length=shape.spiral * lam, s_start=s, s_end=s + total, design_speed=v,
            max_speed=vmax,
            advisory_speed=design.advisory_speed(lane_radius, e, params) if vmax < v - 1e-6 else None,
            scale=lam, shape=shape, vertex=(float(pts[i][0]), float(pts[i][1])),
            heading_in=float(headings[i - 1])))
        pieces += cps
        pose, s = cps[-1].end, s + total
    poly_breaks += [node_poly[-1] - trim_end, node_poly[-1]]
    station_breaks += [s, s + trim_end]

    alignment = Alignment(pieces)
    target = pts[-1] - np.array([math.cos(headings[-1]), math.sin(headings[-1])]) * trim_end
    gap = math.hypot(alignment.end.x - target[0], alignment.end.y - target[1])
    if gap > END_TOL:
        log.warning("chain %s: fitted road ends %.6f m from the trimmed polyline end", c.edge_ids, gap)
    pb, sb = _monotone(poly_breaks, station_breaks)
    return ChainAlignment(c, alignment, trim_start, trim_end, tuple(corners),
                          tuple(node_poly.tolist()), pb, sb)


def _corner_check(clearance: ClearanceCheck, c: Chain, vertex: np.ndarray, h_in: float, h_out: float,
                  shape: CornerShape, width: float) -> Callable[[float], bool]:
    u_in = np.array([math.cos(h_in), math.sin(h_in)])
    u_out = np.array([math.cos(h_out), math.sin(h_out)])

    def ok(lam: float) -> bool:
        t = lam * shape.tangent
        start = vertex - u_in * t
        pieces = shape.pieces(Pose(float(start[0]), float(start[1]), h_in), lam)
        straight = LineString([start, vertex, vertex + u_out * t])
        return clearance(c, pieces, straight, width)

    return ok


def _monotone(xs: list[float], ys: list[float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Breakpoints with repeated x dropped (np.interp needs increasing x)."""
    px, py = [xs[0]], [ys[0]]
    for x, y in zip(xs[1:], ys[1:]):
        if x > px[-1] + 1e-12:
            px.append(float(x))
            py.append(float(y))
    return tuple(px), tuple(py)
