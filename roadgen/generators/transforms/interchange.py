"""Highway interchanges (stage C9): diamond and cloverleaf templates.

A template replaces a grade-separated crossing (two roads crossing without a
shared node; roadgen.geometry.grade_separation raises one over the other)
with ramps between them. Both share one base:

- the mainline (a highway) and the crossing road are rebuilt as straight
  chains through the crossing, with nodes where the template needs them;
- ramps are one-lane, one-way highway-class roads; the mainline nodes where
  they leave or join get Node.kind "ramp", where the builder allows only
  straight-through, right exits and right merges (no left turns across the
  centre line, no ramp to ramp);
- auxiliary lanes (deceleration before an exit, acceleration after an
  entry, weaving between a cloverleaf's loops) are lane-count changes on
  the mainline, built as the stage A tapers.

Diamond (highway x avenue / street; AASHTO's most common urban
interchange): each travel direction leaves ramp_off before the crossing and
rejoins ramp_on after it, beyond the overpass's ramps, through a terminal on
the crossing road terminal_offset off the mainline on its right; the
terminals are ordinary at-grade junctions.

Cloverleaf (highway x highway): in each quadrant a loop ramp (a 240 deg
right turn of loop_radius, leaving and joining at ramp_angle_deg) carries
one left turn, and an outer ramp the right turn back; the outer ramps move
out until they clear the loops. Between a loop's entry and the next loop's
exit each direction weaves on an auxiliary lane.

Nothing changes unless the whole interchange fits: the rebuilt roads may
have no other road joining them inside the interchange, and no new piece may
come within CLEARANCE of another road. Interchanges are listed in
graph.metadata["interchanges"].
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from shapely.geometry import LineString
from shapely.strtree import STRtree

from roadgen.config import DEFAULTS, section
from roadgen.graph.model import Edge, Node, RoadGraph

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "interchange")
CLEARANCE = 8.0  # [m] between a new piece's edge and any other road
STRAIGHT_TOL = 20.0  # [m] a rebuilt road may leave the straight line by at most this
ARC_STEP_DEG = 40.0  # most turn between two nodes of a ramp curve
LANE = 3.5  # [m] ramp lane width

Point = tuple[float, float]


@dataclass(frozen=True)
class InterchangeParams:
    ramp_off: float = _D["ramp_off"]  # [m] diamond: exits this far before the crossing
    ramp_on: float = _D["ramp_on"]  # [m] diamond: entries this far after it
    terminal_offset: float = _D["terminal_offset"]  # [m] diamond terminals off the mainline
    aux_length: float = _D["aux_length"]  # [m] deceleration / acceleration lanes
    loop_radius: float = _D["loop_radius"]  # [m] cloverleaf loops
    outer_radius: float = _D["outer_radius"]  # [m] cloverleaf outer ramps
    ramp_angle_deg: float = _D["ramp_angle_deg"]  # cloverleaf ramps leave and join at this angle
    ramp_speed: float = _D["ramp_speed"]  # [m/s]
    loop_speed: float = _D["loop_speed"]  # [m/s]
    cross_lanes: tuple[int, int] = _D["cross_lanes"]  # diamond: the crossing road's lanes, at least

    def __post_init__(self) -> None:
        if min(self.ramp_off, self.ramp_on, self.terminal_offset, self.aux_length, self.loop_radius,
               self.outer_radius, self.ramp_speed, self.loop_speed) <= 0:
            raise ValueError("interchange lengths and speeds must be positive")
        if not 10.0 <= self.ramp_angle_deg <= 45.0:
            raise ValueError("ramp_angle_deg must be in [10, 45]")

    @property
    def diamond_reach(self) -> float:
        """Distance along the mainline the diamond rebuilds on each side of the crossing."""
        return max(self.ramp_off, self.ramp_on) + self.aux_length

    def loop_offset(self) -> float:
        """Distance of a loop's ends from the crossing: the loop clears the highways by
        CLEARANCE beyond their half widths, its tangents start right after the ends."""
        s, c = math.sin(math.radians(self.ramp_angle_deg)), math.cos(math.radians(self.ramp_angle_deg))
        # Loop centre at (k, -k): k (c - s) + L s = R (it touches the departure line at
        # radius R); clearance k - R >= GAP from both highways.
        gap = 2 * LANE + LANE + CLEARANCE
        return max(20.0, (self.loop_radius - (self.loop_radius + gap) * (c - s)) / s)


# --- geometry ---------------------------------------------------------------------------------


def _rot(v: np.ndarray, deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a)])


def turn_path(p0: np.ndarray, h0: float, p1: np.ndarray, h1: float, radius: float,
              clockwise: bool) -> Optional[list[np.ndarray]]:
    """Points of a line - arc - line path from p0 (heading h0 [rad]) to p1 (heading h1):
    the arc of radius turns one way, tangent to both rays; None if the tangent points
    don't lie ahead of p0 and before p1. Arc points every ARC_STEP_DEG at most."""
    d0, d1 = np.array([math.cos(h0), math.sin(h0)]), np.array([math.cos(h1), math.sin(h1)])
    side = -1.0 if clockwise else 1.0  # centre to the right (clockwise) or left
    n0, n1 = side * np.array([-d0[1], d0[0]]), side * np.array([-d1[1], d1[0]])
    a, b = p0 + radius * n0, p1 + radius * n1  # the centre lies on both offset lines
    den = d0[0] * d1[1] - d0[1] * d1[0]
    if abs(den) < 1e-9:
        return None
    t = ((b[0] - a[0]) * d1[1] - (b[1] - a[1]) * d1[0]) / den
    centre = a + t * d0
    t0 = float(np.dot(centre - p0, d0))  # tangent point on the first ray
    t1 = float(np.dot(centre - p1, d1))  # ... on the second (negative: before p1)
    if t0 < 5.0 or t1 > -5.0:
        return None
    q0, q1 = p0 + t0 * d0, p1 + t1 * d1
    a0 = math.atan2(*(q0 - centre)[::-1])
    a1 = math.atan2(*(q1 - centre)[::-1])
    sweep = (a0 - a1) % (2 * math.pi) if clockwise else (a1 - a0) % (2 * math.pi)
    steps = max(1, math.ceil(math.degrees(sweep) / ARC_STEP_DEG))
    arc = [centre + radius * np.array([math.cos(a0 + side * sweep * k / steps),
                                       math.sin(a0 + side * sweep * k / steps)])
           for k in range(steps + 1)]
    return [p0, *arc, p1]


# --- the base: rebuilding a road through the crossing -------------------------------------


@dataclass
class _Road:
    """One road (the mainline or the crossing road) straight through the crossing."""

    first: int  # the existing node before the interchange
    last: int  # ... after it
    inner_nodes: list[int]  # existing nodes between them (removed)
    inner_edges: list[int]  # existing edges between them (removed)
    template: Edge  # attributes for the rebuilt edges
    forward: bool  # the template's lanes_forward run along +u
    u: np.ndarray  # unit direction through the crossing
    s_first: float  # along-u position of first / last, from the crossing
    s_last: float


def _walk(graph: RoadGraph, eid: int, origin: np.ndarray, u: np.ndarray, reach: float) -> Optional[_Road]:
    """The road of edge eid straight through origin along u, out to reach on both sides:
    None if it bends more than STRAIGHT_TOL off the line, ends, or another road joins it."""
    e = graph.edges[eid]
    nodes = {}
    edges = [eid]

    def pos(n: int) -> np.ndarray:
        return np.array([graph.nodes[n].x, graph.nodes[n].y])

    ends = {}
    for start in (e.to_node, e.from_node):
        sign = 1.0 if float(np.dot(pos(start) - origin, u)) > 0 else -1.0
        if sign in ends:
            return None  # the edge doesn't pass through origin
        prev, node = eid, start
        while True:
            s = float(np.dot(pos(node) - origin, u))
            if sign * s >= reach:  # the first node beyond the interchange may bend away
                ends[sign] = (node, s)
                break
            if abs(_cross(u, pos(node) - origin)) > STRAIGHT_TOL:
                return None
            nodes[node] = s
            nxt = [x for x in graph.incident_edges(node) if x.id != prev]
            if len(nxt) != 1:  # a junction or a dead end inside the interchange
                return None
            prev = nxt[0].id
            edges.append(prev)
            node = nxt[0].to_node if nxt[0].from_node == node else nxt[0].from_node
    (last, s_last), (first, s_first) = ends[1.0], ends[-1.0]
    head = graph.nodes[e.to_node]
    tail = graph.nodes[e.from_node]
    forward = float(np.dot(np.array([head.x - tail.x, head.y - tail.y]), u)) > 0
    return _Road(first, last, sorted(nodes), edges, e, forward, u, s_first, s_last)


def _rebuild(graph: RoadGraph, road: _Road, origin: np.ndarray, stations: list[float],
             extra: dict[tuple[float, float], tuple[int, int]], ids: dict) -> dict[float, int]:
    """Replace the road between first and last with a straight chain through nodes at
    the stations (along u from origin); extra lanes (along +u, against) on the pieces
    between two stations. Returns station -> node id."""
    for eid in road.inner_edges:
        graph.edges.pop(eid, None)
    for n in road.inner_nodes:
        graph.nodes.pop(n, None)
    t = road.template
    chain = [road.first]
    at = {}
    for s in sorted(stations):
        p = origin + s * road.u
        nid = ids["node"]
        ids["node"] += 1
        graph.add_node(Node(nid, float(p[0]), float(p[1])))
        chain.append(nid)
        at[s] = nid
    chain.append(road.last)
    marks = [road.s_first, *sorted(stations), road.s_last]
    base_f, base_b = (t.lanes_forward, t.lanes_backward) if road.forward else (t.lanes_backward, t.lanes_forward)
    for (a, b), (sa, sb) in zip(zip(chain, chain[1:]), zip(marks, marks[1:])):
        add_f, add_b = 0, 0
        for (lo, hi), (ef, eb) in extra.items():
            if lo <= sa + 1e-6 and sb <= hi + 1e-6:
                add_f, add_b = add_f + ef, add_b + eb
        graph.add_edge(Edge(ids["edge"], a, b, t.road_class, base_f + add_f, base_b + add_b,
                            lane_width=t.lane_width, speed_limit=t.speed_limit))
        ids["edge"] += 1
    return at


def _ramp(graph: RoadGraph, points: list[np.ndarray], start: int, end: int, speed: float,
          ids: dict) -> list[int]:
    """A one-way, one-lane ramp from node start through points (bend nodes) to node end."""
    chain = [start]
    for p in points[1:-1]:
        nid = ids["node"]
        ids["node"] += 1
        graph.add_node(Node(nid, float(p[0]), float(p[1])))
        chain.append(nid)
    chain.append(end)
    out = []
    for a, b in zip(chain, chain[1:]):
        graph.add_edge(Edge(ids["edge"], a, b, "highway", 1, 0, lane_width=LANE, speed_limit=speed))
        out.append(ids["edge"])
        ids["edge"] += 1
    return out


def _clear(graph: RoadGraph, pieces: list[LineString], allowed: set[int]) -> bool:
    """No piece within CLEARANCE (+ widths) of a road outside allowed, nor of another piece."""
    others = [(eid, LineString([(graph.nodes[e.from_node].x, graph.nodes[e.from_node].y),
                                (graph.nodes[e.to_node].x, graph.nodes[e.to_node].y)]))
              for eid, e in graph.edges.items() if eid not in allowed]
    if others:
        tree = STRtree([g for _, g in others])
        for piece in pieces:
            for k in tree.query(piece.buffer(CLEARANCE + 2 * LANE + LANE), predicate="intersects"):
                return False
    for i, a in enumerate(pieces):
        for b in pieces[i + 1:]:
            if a.crosses(b):
                return False
    return True


# --- templates --------------------------------------------------------------------------------


def add_diamond(graph: RoadGraph, mainline_edge: int, cross_edge: int, point: Point,
                params: InterchangeParams = InterchangeParams()) -> bool:
    """A diamond where mainline_edge (a highway) crosses cross_edge without a node at point."""
    p = params
    origin = np.array(point, dtype=float)
    m, c = graph.edges[mainline_edge], graph.edges[cross_edge]
    u = _unit(graph, m)
    v = _unit(graph, c)
    if abs(_cross(u, v)) < math.sin(math.radians(60)):
        log.debug("diamond at %s: crossing too skewed", point)
        return False  # too skewed for straight diamond ramps
    main = _walk(graph, mainline_edge, origin, u, p.diamond_reach + 30.0)
    right = np.array([u[1], -u[0]])  # right of +u traffic
    v = v if float(np.dot(v, right)) > 0 else -v  # the crossing road along +right
    cross = _walk(graph, cross_edge, origin, v, p.terminal_offset + 40.0)
    if main is None or cross is None:
        log.debug("diamond at %s: the %s is not one straight road through the interchange", point,
                  "mainline" if main is None else "crossing road")
        return False
    # Ramps: +u traffic exits at -ramp_off to the right terminal (+v), rejoins at +ramp_on;
    # -u traffic exits at +ramp_off to the left terminal (-v), rejoins at -ramp_on.
    t_r, t_l = origin + p.terminal_offset * v, origin - p.terminal_offset * v
    ramp_lines = [
        [origin - p.ramp_off * u, t_r], [t_r, origin + p.ramp_on * u],
        [origin + p.ramp_off * u, t_l], [t_l, origin - p.ramp_on * u],
    ]
    pieces = [LineString(r) for r in ramp_lines]
    allowed = set(main.inner_edges) | set(cross.inner_edges)
    allowed |= {x.id for n in (main.first, main.last, cross.first, cross.last) for x in graph.incident_edges(n)}
    if not _clear(graph, pieces, allowed):
        log.debug("diamond at %s: a ramp would come too close to another road", point)
        return False
    ids = {"node": max(graph.nodes) + 1, "edge": max(graph.edges) + 1}
    a = p.aux_length
    stations = [-p.ramp_off - a, -p.ramp_off, -p.ramp_on - a, -p.ramp_on,
                p.ramp_on, p.ramp_on + a, p.ramp_off, p.ramp_off + a]
    extra = {(-p.ramp_off - a, -p.ramp_off): (1, 0), (p.ramp_on, p.ramp_on + a): (1, 0),
             (p.ramp_off, p.ramp_off + a): (0, 1), (-p.ramp_on - a, -p.ramp_on): (0, 1)}
    at = _rebuild(graph, main, origin, stations, extra, ids)
    cross_template = cross.template
    if min(cross_template.lanes_forward, cross_template.lanes_backward) < min(p.cross_lanes):
        cross_template.lanes_forward = max(cross_template.lanes_forward, p.cross_lanes[0])
        cross_template.lanes_backward = max(cross_template.lanes_backward, p.cross_lanes[1])
    terminals = _rebuild(graph, cross, origin, [-p.terminal_offset, p.terminal_offset], {}, ids)
    tr, tl = terminals[p.terminal_offset], terminals[-p.terminal_offset]
    ramps = (_ramp(graph, ramp_lines[0], at[-p.ramp_off], tr, p.ramp_speed, ids)
             + _ramp(graph, ramp_lines[1], tr, at[p.ramp_on], p.ramp_speed, ids)
             + _ramp(graph, ramp_lines[2], at[p.ramp_off], tl, p.ramp_speed, ids)
             + _ramp(graph, ramp_lines[3], tl, at[-p.ramp_on], p.ramp_speed, ids))
    ramp_nodes = [at[-p.ramp_off], at[p.ramp_on], at[p.ramp_off], at[-p.ramp_on]]
    _record(graph, "diamond", point, ramp_nodes, ramps, [tr, tl])
    return True


def add_cloverleaf(graph: RoadGraph, edge_a: int, edge_b: int, point: Point,
                   params: InterchangeParams = InterchangeParams()) -> bool:
    """A cloverleaf where highways edge_a and edge_b cross without a node at point."""
    p = params
    origin = np.array(point, dtype=float)
    a_dir = _unit(graph, graph.edges[edge_a])
    b_dir = _unit(graph, graph.edges[edge_b])
    if _cross(a_dir, b_dir) < 0:
        b_dir = -b_dir  # b is a turned counter-clockwise
    if abs(_cross(a_dir, b_dir)) < math.sin(math.radians(70)):
        log.debug("cloverleaf at %s: crossing too skewed", point)
        return False  # the template assumes a near right angle
    sigma = p.ramp_angle_deg
    loop_at = p.loop_offset()
    plan = None
    for outer_at in np.arange(loop_at + 2 * p.loop_radius, loop_at + 12 * p.loop_radius, 25.0):
        plan = _cloverleaf_paths(origin, a_dir, b_dir, loop_at, float(outer_at), p, sigma)
        if plan is not None:
            break
    if plan is None:
        return False
    reach = plan["outer_at"] + p.aux_length + 30.0
    road_a = _walk(graph, edge_a, origin, a_dir, reach)
    road_b = _walk(graph, edge_b, origin, b_dir, reach)
    if road_a is None or road_b is None:
        return False
    allowed = set(road_a.inner_edges) | set(road_b.inner_edges)
    allowed |= {x.id for n in (road_a.first, road_a.last, road_b.first, road_b.last)
                for x in graph.incident_edges(n)}
    if not _clear(graph, [LineString(r["points"]) for r in plan["ramps"]], allowed):
        return False
    ids = {"node": max(graph.nodes) + 1, "edge": max(graph.edges) + 1}
    lo, out = plan["loop_at"], plan["outer_at"]
    stations = [-out, -lo, lo, out]
    weave = {(-lo, lo): (1, 1)}  # both directions weave between the loops
    at = {"a": _rebuild(graph, road_a, origin, stations, weave, ids),
          "b": _rebuild(graph, road_b, origin, stations, weave, ids)}
    ramps = []
    for r in plan["ramps"]:
        speed = p.loop_speed if r["loop"] else p.ramp_speed
        ramps += _ramp(graph, r["points"], at[r["from"][0]][r["from"][1]], at[r["to"][0]][r["to"][1]],
                       speed, ids)
    ramp_nodes = [at[h][s] for h in "ab" for s in stations]
    _record(graph, "cloverleaf", point, ramp_nodes, ramps, [])
    return True


def _cloverleaf_paths(origin, a_dir, b_dir, loop_at, outer_at, p: InterchangeParams, sigma: float):
    """The eight ramps, or None if a loop and an outer ramp come too close."""
    streams = {(1, "a"): a_dir, (1, "b"): b_dir, (-1, "a"): -a_dir, (-1, "b"): -b_dir}

    def road(vec):
        for (sign, h), d in streams.items():
            if np.allclose(d, vec):
                return sign, h
        raise AssertionError

    ramps = []
    for s1, s2 in ((a_dir, b_dir), (b_dir, -a_dir), (-a_dir, -b_dir), (-b_dir, a_dir)):
        sg1, h1 = road(s1)
        sg2, h2 = road(s2)
        hd1, hd2 = math.atan2(s1[1], s1[0]), math.atan2(s2[1], s2[0])
        # Loop: from stream 1 after the crossing, right around, into stream 2 before it.
        d1, m2 = origin + loop_at * s1, origin - loop_at * s2
        loop = turn_path(d1, hd1 - math.radians(sigma), m2, hd2 + math.radians(sigma), p.loop_radius, True)
        # Outer ramp: from stream 2 before the crossing, right, into stream 1 after it.
        d2, m1 = origin - outer_at * s2, origin + outer_at * s1
        outer = turn_path(d2, hd2 - math.radians(sigma), m1, hd1 + math.radians(sigma), p.outer_radius, True)
        if loop is None or outer is None:
            return None
        if LineString(loop).distance(LineString(outer)) < CLEARANCE + 2 * LANE:
            return None
        ramps.append({"points": loop, "loop": True, "from": (h1, sg1 * loop_at), "to": (h2, -sg2 * loop_at)})
        ramps.append({"points": outer, "loop": False, "from": (h2, -sg2 * outer_at), "to": (h1, sg1 * outer_at)})
    return {"ramps": ramps, "loop_at": loop_at, "outer_at": outer_at}


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    """z of the cross product of two 2D vectors."""
    return float(a[0] * b[1] - a[1] * b[0])


def _unit(graph: RoadGraph, e: Edge) -> np.ndarray:
    a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
    v = np.array([b.x - a.x, b.y - a.y])
    return v / np.linalg.norm(v)


def _record(graph: RoadGraph, kind: str, point: Point, ramp_nodes: list[int], ramps: list[int],
            terminals: list[int]) -> None:
    for n in ramp_nodes:
        graph.nodes[n].kind = "ramp"
    graph.metadata.setdefault("interchanges", []).append({
        "type": kind, "point": [round(point[0], 3), round(point[1], 3)],
        "ramp_nodes": ramp_nodes, "ramps": ramps, "terminals": terminals,
    })
