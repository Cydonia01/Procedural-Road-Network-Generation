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

Speeds: each edge's limit over its range, lowered to a corner's advisory speed
from a deceleration lead before the corner (at ADVISORY_DECEL) to its end.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import numpy as np

from roadgen.geometry import design
from roadgen.geometry.alignment import ChainAlignment
from roadgen.graph.model import RoadGraph

log = logging.getLogger(__name__)

Side = Literal["right", "left"]
ADVISORY_DECEL = 1.5  # comfortable deceleration ahead of an advisory curve [m/s^2]
STATION_EPS = 1e-6  # [m] breakpoints closer than this are merged
MAX_TAPER_SHARE = 0.5  # a taper uses at most this share of its edge


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
class LaneSection:
    s0: float
    s1: float
    right: tuple[WidthPiece, ...]  # lanes from the centre outward
    left: tuple[WidthPiece, ...]


@dataclass(frozen=True)
class LaneLayout:
    length: float
    right: tuple[tuple[WidthPiece, ...], ...]  # per lane position (1 = next to the centre), its pieces
    left: tuple[tuple[WidthPiece, ...], ...]

    def breakpoints(self) -> list[float]:
        pts = {0.0, self.length}
        for side in (self.right, self.left):
            for pieces in side:
                for p in pieces:
                    pts.update((p.s0, p.s1))
        out: list[float] = []
        for s in sorted(pts):
            if not out or s > out[-1] + STATION_EPS:
                out.append(s)
        out[-1] = self.length
        return out

    def sections(self) -> list[LaneSection]:
        bps = self.breakpoints()
        out = []
        for s0, s1 in pairwise(bps):
            mid = 0.5 * (s0 + s1)
            sides = []
            for side in (self.right, self.left):
                present = [_covering(pieces, mid) for pieces in side]
                n = sum(p is not None for p in present)
                if any(p is None for p in present[:n]):
                    raise ValueError(f"lanes are not contiguous from the centre at s={mid:.2f}")
                sides.append(tuple(p for p in present[:n] if p is not None))
            out.append(LaneSection(s0, s1, sides[0], sides[1]))
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


def lane_layout(graph: RoadGraph, ca: ChainAlignment) -> LaneLayout:
    """Lane widths along a chain road, with tapers at lane-count and width changes."""
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
    return LaneLayout(ca.length, tuple(map(tuple, sides["right"])), tuple(map(tuple, sides["left"])))


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
    spans = []
    for eid in ca.chain.edge_ids:
        for d0, d1 in graph.edges[eid].bridges:
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
