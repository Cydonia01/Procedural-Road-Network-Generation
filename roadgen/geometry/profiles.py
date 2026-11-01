"""Along-road profiles of a fitted chain road: lanes, speeds, heights, bridges.

Everything is expressed in stations s of the chain's alignment (0 at the
road's start, after the junction trim).

Lanes: each edge of the chain contributes its lane counts (seen along the
chain) and lane width over its station range. Where a side gains lanes at a
node, the extra outermost lanes widen from 0 after the node; where it loses
lanes, they narrow to 0 before the node. Both use a cubic smoothstep
w0 + (w1 - w0)(3u^2 - 2u^3) over the MUTCD taper length, so lane edges stay
tangent-continuous. A change of lane width on continuing lanes uses the same
transition after the node. Tapers are at most half of their edge's length.

Lane sections: a new section starts where a driving lane's (or a turn
pocket's) width changes, or where any lane starts or ends. Side lanes and medians keep their width
changes (tapers, curb extensions) inside a section as several pieces
(WidthPieces), written as several <width> records, so they add no sections:
fewer, longer sections and much smaller files (stage B, moved from B9).

Lane roles: every lane position of a layout has a kind (driving; side lanes
from stage B) and keeps its position as its role. A lane section holds the
lanes present over it, outward from the centre, with their roles, so a side
lane outside a dropped driving lane keeps its role (and its links) while its
lane id shifts inward.

Side lanes (stage B, with a cross-section, roadgen.geometry.cross_section):
each edge's profile adds a median (at the centre line, split between the
two sides) and side lanes outside the driving lanes. Like driving lanes they
taper in and out at nodes where an edge gains or loses them, and change
width there. At the chain's junction ends some of them end before the
junction: parking parking_clear before it, shoulders (urban and suburban
profiles, and every profile at a ramp node) and, where a roundabout or
cul-de-sac loop starts, bike lanes right at it, and medians side_taper
before it after a MUTCD shift taper. Parking also stops over bridges, and
shoulders narrow to tunnel_shoulder inside tunnels. Side lanes taper over
side_taper.

Turn pockets (stage B8, cross_section.pocket_ends): on a chain end at a
junction where the cross-section's turn_lanes give the last edge a pocket,
an innermost lane of kind "pocket" on the incoming side (the right side at
the chain's end, the left at its start), between the median and the driving
lanes: it widens from 0 over pocket_taper and keeps the edge's lane width
over the last pocket_length, both on the last edge and out of the road's
corners (shortened, or left out where too little room remains). Like the
median its position runs the whole road, 0 m wide elsewhere, so the driving
lanes keep their ids.

Speeds: each edge's limit over its range, lowered to a corner's advisory speed
from a deceleration lead before the corner (at ADVISORY_DECEL) to its end.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, Optional

import numpy as np

from roadgen.geometry import design
from roadgen.geometry.alignment import ChainAlignment
from roadgen.geometry.cross_section import (
    BARE_DROP,
    SHOULDERS_END,
    CrossSectionParams,
    bare_ends,
    edge_cross_section,
    pocket_ends,
)
from roadgen.graph.model import RoadGraph

log = logging.getLogger(__name__)

Side = Literal["right", "left"]
ADVISORY_DECEL = 1.5  # comfortable deceleration ahead of an advisory curve [m/s^2]
STATION_EPS = 1e-6  # [m] breakpoints closer than this are merged
MAX_TAPER_SHARE = 0.5  # a taper uses at most this share of its edge
MIN_POCKET_TAPER = 2.0  # a turn pocket whose taper would be shorter is left out [m]


@dataclass(frozen=True)
class WidthPiece:
    """Lane width over [s0, s1]: constant, or a cubic smoothstep from w0 to w1."""

    s0: float
    s1: float
    w0: float
    w1: float

    @property
    def constant(self) -> bool:
        return self.w0 == self.w1

    def width(self, s: float) -> float:
        if self.constant:
            return self.w0
        u = min(max((s - self.s0) / (self.s1 - self.s0), 0.0), 1.0)
        return self.w0 + (self.w1 - self.w0) * (3 * u * u - 2 * u ** 3)

    def poly(self, at: float) -> tuple[float, float, float, float]:
        """(a, b, c, d) of the width as a cubic in ds = s - at (OpenDRIVE <width>)."""
        if self.constant:
            return self.w0, 0.0, 0.0, 0.0
        length, dw, h = self.s1 - self.s0, self.w1 - self.w0, at - self.s0
        l2, l3 = length ** 2, length ** 3
        return (self.w0 + dw * (3 * h * h / l2 - 2 * h ** 3 / l3),
                dw * (6 * h / l2 - 6 * h * h / l3),
                dw * (3 / l2 - 6 * h / l3),
                -2 * dw / l3)


@dataclass(frozen=True)
class WidthPieces:
    """A side lane's or median's width pieces within one lane section, in s order."""

    pieces: tuple[WidthPiece, ...]

    @property
    def s0(self) -> float:
        return self.pieces[0].s0

    @property
    def w0(self) -> float:
        return self.pieces[0].w0

    @property
    def constant(self) -> bool:
        return all(p.constant and p.w0 == self.w0 for p in self.pieces)

    def width(self, s: float) -> float:
        piece = _covering(self.pieces, s)
        if piece is None:  # just outside: the nearest end
            piece = self.pieces[0] if s < self.pieces[0].s0 else self.pieces[-1]
        return piece.width(s)


Role = tuple[str, int]  # (lane kind, position in the layout: 1 = next to the centre)


@dataclass(frozen=True)
class LaneSection:
    s0: float
    s1: float
    right: tuple[WidthPiece, ...]  # lanes from the centre outward
    left: tuple[WidthPiece, ...]
    right_roles: tuple[Role, ...] = ()  # role of each lane in right; () = driving lanes 1, 2, ...
    left_roles: tuple[Role, ...] = ()

    def roles(self, side: Side) -> tuple[Role, ...]:
        pieces, roles = (self.right, self.right_roles) if side == "right" else (self.left, self.left_roles)
        return roles or tuple(("driving", k) for k in range(1, len(pieces) + 1))


@dataclass(frozen=True)
class LaneLayout:
    length: float
    right: tuple[tuple[WidthPiece, ...], ...]  # per lane position (1 = next to the centre), its pieces
    left: tuple[tuple[WidthPiece, ...], ...]
    right_kinds: tuple[str, ...] = ()  # kind of each position; () = all driving
    left_kinds: tuple[str, ...] = ()
    right_heights: tuple[float, ...] = ()  # height of each position (curbs, sidewalks) [m]; () = 0
    left_heights: tuple[float, ...] = ()

    def height(self, side: Side, position: int) -> float:
        """Height [m] of the lane at a layout position (1 = next to the centre)."""
        heights = self.right_heights if side == "right" else self.left_heights
        return heights[position - 1] if heights else 0.0

    def breakpoints(self) -> list[float]:
        """Section boundaries: every boundary of a driving lane's pieces, and where any
        other lane starts or ends (its own width changes stay inside a section)."""
        pts = {0.0, self.length}
        for side in ("right", "left"):
            for pieces, kind in zip(getattr(self, side), self.kinds(side)):
                if kind in ("driving", "pocket"):
                    for p in pieces:
                        pts.update((p.s0, p.s1))
                else:
                    for run in _runs(pieces):
                        pts.update((run[0].s0, run[-1].s1))
        out: list[float] = []
        for s in sorted(pts):
            if not out or s > out[-1] + STATION_EPS:
                out.append(s)
        out[-1] = self.length
        return out

    def kinds(self, side: Side) -> tuple[str, ...]:
        positions, kinds = (self.right, self.right_kinds) if side == "right" else (self.left, self.left_kinds)
        return kinds or ("driving",) * len(positions)

    def sections(self) -> list[LaneSection]:
        """Lane sections between breakpoints. Driving lanes must be contiguous from the
        centre; other lanes close up inward where a lane between them is absent."""
        bps = self.breakpoints()
        out = []
        for s0, s1 in pairwise(bps):
            mid = 0.5 * (s0 + s1)
            sides = []
            for side, positions in (("right", self.right), ("left", self.left)):
                present, roles, gap = [], [], False
                for pos, (pieces, kind) in enumerate(zip(positions, self.kinds(side)), 1):
                    p = _covering(pieces, mid)
                    if p is None:
                        gap = gap or kind == "driving"
                        continue
                    if gap and kind == "driving":
                        raise ValueError(f"lanes are not contiguous from the centre at s={mid:.2f}")
                    if kind != "driving":
                        inside = tuple(q for q in pieces if q.s1 > s0 + STATION_EPS and q.s0 < s1 - STATION_EPS)
                        p = WidthPieces(inside) if len(inside) > 1 else inside[0]
                    present.append(p)
                    roles.append((kind, pos))
                sides.append((tuple(present), tuple(roles)))
            (right, rr), (left, lr) = sides
            plain = all(k == "driving" for k, _ in rr + lr)
            out.append(LaneSection(s0, s1, right, left, () if plain else rr, () if plain else lr))
        return out

    def lanes_at(self, s: float) -> list[tuple[int, str, float, float, float]]:
        """(lane id, kind, inner t, outer t, height) of every lane at station s, t being
        the offset from the reference line [m] (left positive)."""
        sec = next((x for x in self.sections() if x.s0 - STATION_EPS <= s <= x.s1 + STATION_EPS), None)
        if sec is None:
            raise ValueError(f"station {s:.3f} outside the road (length {self.length:.3f})")
        out = []
        for side, sign in (("right", -1), ("left", 1)):
            t = 0.0
            for k, (piece, (kind, pos)) in enumerate(zip(getattr(sec, side), sec.roles(side)), 1):
                w = piece.width(s)
                out.append((sign * k, kind, sign * t, sign * (t + w), self.height(side, pos)))
                t += w
        return out

    def driving_offsets(self, s: float) -> dict[int, float]:
        """Lane id -> lane-centre offset t from the reference line [m] of every driving lane
        (and turn pocket wider than 0) at station s (left positive); for road ends at junctions."""
        sec = next((x for x in self.sections() if x.s0 - STATION_EPS <= s <= x.s1 + STATION_EPS), None)
        if sec is None:
            raise ValueError(f"station {s:.3f} outside the road (length {self.length:.3f})")
        out: dict[int, float] = {}
        for side, sign in (("right", -1), ("left", 1)):
            t = 0.0
            for k, (piece, (kind, _)) in enumerate(zip(getattr(sec, side), sec.roles(side)), 1):
                w = piece.width(s)
                if kind == "driving" or (kind == "pocket" and w > STATION_EPS):
                    out[sign * k] = sign * (t + w / 2)
                t += w
        return out

    def half_widths(self, s: float) -> tuple[float, float]:
        """(right, left) total lane width at station s [m]."""
        return tuple(sum(p.width(s) for p in (_covering(pieces, s) for pieces in side) if p is not None)
                     for side in (self.right, self.left))  # type: ignore[return-value]


def _covering(pieces: tuple[WidthPiece, ...], s: float):
    for p in pieces:
        if p.s0 - STATION_EPS <= s <= p.s1 + STATION_EPS:
            return p
    return None


SIDE_ORDER = ("biking", "parking", "shoulder", "curb", "sidewalk", "border")  # outward, when profiles mix


def lane_layout(graph: RoadGraph, ca: ChainAlignment, cross_section: Optional[CrossSectionParams] = None,
                junctions: frozenset[int] = frozenset(), loops: frozenset[int] = frozenset()) -> LaneLayout:
    """Lane widths along a chain road, with tapers at lane-count and width changes; with a
    cross-section also its median and side lanes (junctions: the network's junction
    nodes, loops: its loop edges, see cross_section.loop_edges)."""
    edges = [(graph.edges[ce.edge_id], ce) for ce in ca.chain.edges]
    ranges = [ca.edge_range(e.id) for e, _ in edges]
    sides: dict[Side, list[list[WidthPiece]]] = {}
    for side_index, side in enumerate(("right", "left")):
        counts = [ce.lanes(e)[side_index] for e, ce in edges]
        widths = [e.lane_width for e, _ in edges]
        positions: list[list[WidthPiece]] = [[] for _ in range(max(counts))]
        for k, (e, _) in enumerate(edges):
            lo, hi = ranges[k]
            room = MAX_TAPER_SHARE * (hi - lo)
            for p in range(counts[k]):
                start, end = lo, hi
                head: list[WidthPiece] = []
                tail: list[WidthPiece] = []
                if k > 0 and p < counts[k - 1] and widths[k - 1] != widths[k]:
                    length = min(design.taper_length(e.speed_limit, abs(widths[k] - widths[k - 1])), room)
                    head.append(WidthPiece(lo, lo + length, widths[k - 1], widths[k]))
                    start = lo + length
                elif k > 0 and p >= counts[k - 1]:  # added lane: widens after the node
                    length = min(design.taper_length(e.speed_limit, widths[k]), room)
                    head.append(WidthPiece(lo, lo + length, 0.0, widths[k]))
                    start = lo + length
                if k < len(edges) - 1 and p >= counts[k + 1]:  # dropped lane: narrows before the node
                    length = min(design.taper_length(e.speed_limit, widths[k]), room)
                    tail.append(WidthPiece(hi - length, hi, widths[k], 0.0))
                    end = hi - length
                body = [WidthPiece(start, end, widths[k], widths[k])] if end > start + STATION_EPS else []
                positions[p] += head + body + tail
        sides[side] = [_merge(pieces) for pieces in positions]
    if cross_section is None:
        return LaneLayout(ca.length, tuple(map(tuple, sides["right"])), tuple(map(tuple, sides["left"])))
    out: dict[str, tuple] = {}
    for side in ("right", "left"):
        median, extra, kinds, heights = _extra_positions(graph, ca, side, cross_section, junctions, loops)
        pocket = _pocket(graph, ca, side, cross_section, junctions)
        driving = [tuple(p) for p in sides[side]]
        out[side] = (tuple(median + pocket + driving + extra),
                     ("median",) * len(median) + ("pocket",) * len(pocket) + ("driving",) * len(driving) + kinds,
                     (0.0,) * (len(median) + len(pocket) + len(driving)) + heights)
    return LaneLayout(ca.length, out["right"][0], out["left"][0], out["right"][1], out["left"][1],
                      out["right"][2], out["left"][2])


def _extra_positions(graph: RoadGraph, ca: ChainAlignment, side: Side, cs: CrossSectionParams,
                     junctions: frozenset[int], loops: frozenset[int]
                     ) -> tuple[list[tuple[WidthPiece, ...]], list[tuple[WidthPiece, ...]], tuple[str, ...],
                                tuple[float, ...]]:
    """(median position, side positions, their kinds, their heights) of one side of a chain."""
    edges = [graph.edges[ce.edge_id] for ce in ca.chain.edges]
    ranges = [ca.edge_range(e.id) for e in edges]
    xs = [edge_cross_section(e, cs, e.id in loops) for e in edges]
    # Side lanes per edge as roles (kind, k-th of that kind), outward.
    per_edge: list[dict[tuple[str, int], tuple[float, float]]] = []
    for x in xs:
        seen: dict[str, int] = {}
        roles = {}
        for lane in (x.right if side == "right" else x.left):
            seen[lane.kind] = seen.get(lane.kind, 0) + 1
            roles[(lane.kind, seen[lane.kind])] = (lane.width, lane.height)
        per_edge.append(roles)
    orders = {tuple(r) for r in per_edge}
    roles = list(per_edge[0]) if len(orders) == 1 else sorted(
        {r for roles in per_edge for r in roles}, key=lambda r: (SIDE_ORDER.index(r[0]), r[1]))
    ends = _junction_ends(graph, ca, junctions, loops)
    speed = max(e.speed_limit for e in edges)

    def side_taper(_: float) -> float:
        return cs.side_taper

    def median_taper(width: float) -> float:
        return design.taper_length(speed, width)

    medians = [x.median / 2 for x in xs]
    median: list[tuple[WidthPiece, ...]] = []
    if any(medians):
        pieces = _role_pieces(medians, ranges, median_taper)
        for at_start, _, _, _ in ends:  # medians end before every junction
            w = max(medians)
            pieces = _cut_end(pieces, ca.length, at_start, cs.side_taper, median_taper(w))
        if _present(pieces, 2 * cs.side_taper):
            # The median lane runs the whole road, 0 m wide where there is none, so the
            # driving lanes keep their ids along the road (lane-following by id: esmini's
            # drive-check, the scenario's routes).
            median.append(tuple(_fill(pieces, ca.length)))

    extra, kinds, heights = [], [], []
    for role in roles:
        kind = role[0]
        widths = [r.get(role, (0.0, 0.0))[0] for r in per_edge]
        height = next((r[role][1] for r in per_edge if role in r), 0.0)
        pieces = _role_pieces(widths, ranges, side_taper)
        for at_start, ramp, at_loop, bare in ends:
            profile = xs[0 if at_start else -1].profile
            if bare and kind in BARE_DROP:  # a bare road end: only the curb reaches the junction
                pieces = _cut_end(pieces, ca.length, at_start,
                                  cs.parking_clear if kind == "parking" else 0.0, cs.side_taper)
            elif kind == "parking":
                pieces = _cut_end(pieces, ca.length, at_start, cs.parking_clear, cs.side_taper)
            elif (kind == "shoulder" and (ramp or profile in SHOULDERS_END)) or (kind == "biking" and at_loop):
                pieces = _cut_end(pieces, ca.length, at_start, 0.0, cs.side_taper)
        if kind == "parking":
            for s0, length in bridge_spans(graph, ca):
                pieces = _cut(pieces, s0, s0 + length, 0.0, cs.side_taper)
        if kind == "shoulder":
            for s0, length in tunnel_spans(graph, ca):
                pieces = _cut(pieces, s0, s0 + length, cs.tunnel_shoulder, cs.side_taper)
        pieces = _close_runs(pieces, ca.length, cs.side_taper)
        if _present(pieces, 2 * cs.side_taper):
            # The whole road, 0 m wide where the lane is absent: it starts and ends nowhere
            # inside the road, so its tapers add no lane sections.
            extra.append(tuple(_fill(pieces, ca.length)))
            kinds.append(kind)
            heights.append(height)
    return median, extra, tuple(kinds), tuple(heights)


def _pocket(graph: RoadGraph, ca: ChainAlignment, side: Side, cs: CrossSectionParams,
            junctions: frozenset[int]) -> list[tuple[WidthPiece, ...]]:
    """The turn pocket position of one side of a chain ([] or one position): traffic on the
    right side reaches the chain's end node, on the left side its start node."""
    if cs.turn_lanes is None:
        return []
    at_end = side == "right"
    node = ca.chain.end if at_end else ca.chain.start
    edge = graph.edges[ca.chain.edge_ids[-1 if at_end else 0]]
    if node not in junctions or edge.id not in pocket_ends(graph, node, cs.turn_lanes):
        return []
    lo, hi = ca.edge_range(edge.id)
    # Out of the road's corners: a pocket inside a curve would be the curve's innermost,
    # tightest lane, sharper than its design speed allows.
    for c in ca.corners:
        if at_end and c.s_end > lo and c.s_start < hi:
            lo = max(lo, c.s_end)
        elif not at_end and c.s_start < hi and c.s_end > lo:
            hi = min(hi, c.s_start)
    if hi - lo <= 0.0:
        return []
    length = min(cs.turn_lanes.length(edge.road_class), (hi - lo) / 2)
    taper = min(cs.turn_lanes.pocket_taper, (hi - lo) - length)  # both stay on the edge
    if taper < MIN_POCKET_TAPER:
        return []
    w = edge.lane_width
    if at_end:
        a = max(hi - length - taper, lo)
        pieces = [WidthPiece(a, a + taper, 0.0, w), WidthPiece(a + taper, ca.length, w, w)]
    else:
        b = min(lo + length + taper, hi)
        pieces = [WidthPiece(0.0, b - taper, w, w), WidthPiece(b - taper, b, w, 0.0)]
    return [tuple(_fill(pieces, ca.length))]


def _junction_ends(graph: RoadGraph, ca: ChainAlignment, junctions: frozenset[int],
                   loops: frozenset[int]) -> list[tuple[bool, bool, bool, bool]]:
    """(at chain start, ramp node, a loop starts there, the road ends bare there) for each
    chain end at a junction."""
    out = []
    for at_start, node in ((True, ca.chain.start), (False, ca.chain.end)):
        if node not in junctions:
            continue
        at_loop = any(e.id in loops for e in graph.incident_edges(node)) and not all(
            e.id in loops for e in graph.incident_edges(node))
        edge = ca.chain.edge_ids[0 if at_start else -1]
        out.append((at_start, graph.nodes[node].kind == "ramp", at_loop, edge in bare_ends(graph, node)))
    return out


def _role_pieces(widths: list[float], ranges: list[tuple[float, float]], taper) -> list[WidthPiece]:
    """Width pieces of one lane role over a chain, from its width on each edge (0 = absent):
    it widens from 0 or changes width after a node, and narrows to 0 before one, over
    taper(width) [m], at most MAX_TAPER_SHARE of the edge."""
    n = len(widths)
    pieces: list[WidthPiece] = []
    for k, (lo, hi) in enumerate(ranges):
        w = widths[k]
        if w <= 0:
            continue
        room = MAX_TAPER_SHARE * (hi - lo)
        start, end = lo, hi
        head: list[WidthPiece] = []
        tail: list[WidthPiece] = []
        if k > 0 and widths[k - 1] != w:
            prev = max(widths[k - 1], 0.0)
            length = min(taper(abs(w - prev)), room)
            head.append(WidthPiece(lo, lo + length, prev, w))
            start = lo + length
        if k < n - 1 and widths[k + 1] <= 0:
            length = min(taper(w), room)
            tail.append(WidthPiece(hi - length, hi, w, 0.0))
            end = hi - length
        body = [WidthPiece(start, end, w, w)] if end > start + STATION_EPS else []
        pieces += head + body + tail
    return _merge(pieces)


def _cut_end(pieces: list[WidthPiece], length: float, at_start: bool, clear: float,
             taper: float) -> list[WidthPiece]:
    """The role absent over the last (or first) clear metres of the chain, narrowing to 0
    over taper before that."""
    return _cut(pieces, 0.0, clear, 0.0, taper) if at_start else _cut(pieces, length - clear, length, 0.0, taper)


def _cut(pieces: list[WidthPiece], a: float, b: float, w_low: float, taper: float) -> list[WidthPiece]:
    """Width w_low over [a, b], blending from and back to the constant width around it
    over taper. A piece that is not constant within the blend zone is left out, and a
    blend that would cross a piece boundary becomes part of [a, b]."""
    lo_zone, hi_zone = a - taper, b + taper
    out: list[WidthPiece] = []
    for p in pieces:
        if p.s1 <= lo_zone + STATION_EPS or p.s0 >= hi_zone - STATION_EPS:
            out.append(p)
            continue
        if not p.constant:
            continue
        w = p.w0
        if w <= w_low:
            out.append(p)
            continue
        blend_in = lo_zone >= p.s0 - STATION_EPS  # the blends fit inside this piece
        blend_out = hi_zone <= p.s1 + STATION_EPS
        g0 = a if blend_in else p.s0
        g1 = b if blend_out else p.s1
        segments = [(p.s0, g0 - (taper if blend_in else 0.0), w, w)]
        if blend_in:
            segments.append((g0 - taper, g0, w, w_low))
        segments.append((g0, g1, w_low, w_low))
        if blend_out:
            segments.append((g1, g1 + taper, w_low, w))
        segments.append((g1 + (taper if blend_out else 0.0), p.s1, w, w))
        for s0, s1, w0, w1 in segments:
            if s1 - s0 > STATION_EPS and (w0 > 0 or w1 > 0):
                out.append(WidthPiece(max(s0, p.s0), min(s1, p.s1), w0, w1))
    return _merge(out)


def _runs(pieces) -> list[list[WidthPiece]]:
    """Pieces grouped into runs that touch end to end."""
    runs: list[list[WidthPiece]] = []
    for p in sorted(pieces, key=lambda p: p.s0):
        if runs and abs(runs[-1][-1].s1 - p.s0) <= STATION_EPS:
            runs[-1].append(p)
        else:
            runs.append([p])
    return runs


def _close_runs(pieces: list[WidthPiece], length: float, taper: float) -> list[WidthPiece]:
    """Taper every run of a lane that starts or ends inside the road at full width (a cut
    can leave one where it dropped a piece it could not blend), over taper; a run too
    short for that is dropped."""
    out: list[WidthPiece] = []
    for run in _runs(pieces):
        head, tail = run[0], run[-1]
        if tail.s1 < length - STATION_EPS and tail.w1 > 0:
            if not tail.constant or tail.s1 - tail.s0 < taper + STATION_EPS:
                continue  # nothing to taper on
            run[-1:] = [WidthPiece(tail.s0, tail.s1 - taper, tail.w0, tail.w0),
                        WidthPiece(tail.s1 - taper, tail.s1, tail.w0, 0.0)]
            head = run[0]
        if head.s0 > STATION_EPS and head.w0 > 0:
            if not head.constant or head.s1 - head.s0 < taper + STATION_EPS:
                continue
            run[:1] = [WidthPiece(head.s0, head.s0 + taper, 0.0, head.w0),
                       WidthPiece(head.s0 + taper, head.s1, head.w0, head.w0)]
        out += [p for p in run if p.s1 - p.s0 > STATION_EPS]
    return _merge(out)


def _fill(pieces: list[WidthPiece], length: float) -> list[WidthPiece]:
    """The pieces with 0 m wide pieces wherever [0, length] is not covered."""
    out: list[WidthPiece] = []
    s = 0.0
    for p in sorted(pieces, key=lambda p: p.s0):
        if p.s0 > s + STATION_EPS:
            out.append(WidthPiece(s, p.s0, 0.0, 0.0))
        out.append(p)
        s = p.s1
    if length > s + STATION_EPS:
        out.append(WidthPiece(s, length, 0.0, 0.0))
    return out


def _present(pieces: list[WidthPiece], least: float) -> bool:
    """Whether a role has at least least metres at full width somewhere."""
    return any(p.constant and p.w0 > 0 and p.s1 - p.s0 >= least for p in pieces)


def _merge(pieces: list[WidthPiece]) -> list[WidthPiece]:
    """Join touching constant pieces of equal width."""
    out: list[WidthPiece] = []
    for p in pieces:
        if p.s1 - p.s0 <= STATION_EPS:
            continue
        last = out[-1] if out else None
        if (last is not None and last.constant and p.constant and last.w0 == p.w0
                and abs(last.s1 - p.s0) <= STATION_EPS):
            out[-1] = WidthPiece(last.s0, p.s1, p.w0, p.w0)
        else:
            out.append(p)
    return out


def speed_profile(graph: RoadGraph, ca: ChainAlignment) -> list[tuple[float, float]]:
    """(station, speed [m/s]) records: edge limits lowered to corner advisory speeds."""
    limits = [(ca.edge_range(eid)[0], graph.edges[eid].speed_limit) for eid in ca.chain.edge_ids]

    def limit(s: float) -> float:
        v = limits[0][1]
        for s0, speed in limits:
            if s0 <= s + STATION_EPS:
                v = speed
        return v

    slow = []
    for c in ca.corners:
        if c.advisory_speed is None:
            continue
        v = limit(c.s_start)
        lead = max(0.0, (v * v - c.advisory_speed ** 2) / (2 * ADVISORY_DECEL))
        slow.append((max(0.0, c.s_start - lead), c.s_end, c.advisory_speed))
    bps = sorted({0.0, *(s for s, _ in limits), *(a for a, _, _ in slow), *(b for _, b, _ in slow)})
    bps = [s for s in bps if s < ca.length - STATION_EPS]
    out: list[tuple[float, float]] = []
    for s0, s1 in zip(bps, bps[1:] + [ca.length]):
        mid = 0.5 * (s0 + s1)
        v = min([limit(mid)] + [adv for a, b, adv in slow if a <= mid <= b])
        if not out or abs(out[-1][1] - v) > 1e-9:
            out.append((s0, v))
    return out


def height_samples(graph: RoadGraph, ca: ChainAlignment) -> tuple[np.ndarray, np.ndarray]:
    """(station, height [m]) samples of the chain, its end nodes included.

    The end nodes sit at -trim_start and length + trim_end; edge profiles are
    placed with edge_station.
    """
    s, z = [], []
    for eid in ca.chain.edge_ids:
        d, h = graph.edge_profile(eid)
        s += [ca.edge_station(eid, float(x)) for x in d]
        z += list(h)
    order = np.argsort(s, kind="stable")
    s_arr, z_arr = np.asarray(s)[order], np.asarray(z)[order]
    keep = np.concatenate([[True], np.diff(s_arr) > STATION_EPS])
    return s_arr[keep], z_arr[keep]


def bridge_spans(graph: RoadGraph, ca: ChainAlignment) -> list[tuple[float, float]]:
    """Bridges as (s, length) on the chain road, clipped to it, touching spans merged."""
    return structure_spans(graph, ca, "bridges")


def tunnel_spans(graph: RoadGraph, ca: ChainAlignment) -> list[tuple[float, float]]:
    """Tunnels as (s, length) on the chain road, clipped to it, touching spans merged."""
    return structure_spans(graph, ca, "tunnels")


def structure_spans(graph: RoadGraph, ca: ChainAlignment, kind: str) -> list[tuple[float, float]]:
    """Edge.bridges or Edge.tunnels of the chain as (s, length), clipped, merged."""
    spans = []
    for eid in ca.chain.edge_ids:
        for d0, d1 in getattr(graph.edges[eid], kind):
            a, b = sorted((ca.edge_station(eid, d0), ca.edge_station(eid, d1)))
            a, b = max(a, 0.0), min(b, ca.length)
            if b - a > 1e-3:
                spans.append([a, b])
    merged: list[list[float]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1e-3:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b - a) for a, b in merged]
