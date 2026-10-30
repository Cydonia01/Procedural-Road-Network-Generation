"""How much room a junction needs: where the roads meeting at a node must stop.

Pure geometry on arm headings and road widths, shared by the OpenDRIVE
builder, the 3D ground and the generators' repair pass.

With a cross-section (stage B, roadgen.geometry.cross_section) an arm has two
widths per side: its curb line (driving lanes and the paved side lanes
inside it) and its outer edge (sidewalks included); and two neighbouring
arms are joined by a curb return of the smaller of their class radii, which
the arms must reach past (StreetGen sec. 2.4-2.5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from roadgen.config import DEFAULTS
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    edge_cross_section,
    loop_edges,
    side_half_widths,
)
from roadgen.graph.model import Edge, RoadGraph

DEFAULT_MARGIN = DEFAULTS["opendrive"]["junction_margin"]  # [m]
MIN_ARM_ANGLE_DEG = 10.0  # roads meeting at a sharper angle cannot be separated sensibly


@dataclass(frozen=True)
class Arm:
    """A road leaving a junction node, seen from the node."""

    edge_id: int
    heading: float  # outward direction from the node [rad]
    left: float  # road width on the counter-clockwise side of `heading` [m] (to the curb line)
    right: float  # road width on the clockwise side [m]
    left_outer: Optional[float] = None  # ... to the outer edge (sidewalks included); None = left
    right_outer: Optional[float] = None
    radius: float = 0.0  # curb-return radius of the arm's class [m]; 0 = sharp corners
    length: float = math.inf  # the arm's edge length [m]: bounds its curb returns

    @property
    def outer(self) -> tuple[float, float]:
        """(left, right) widths to the outer edge [m]."""
        return (self.left if self.left_outer is None else self.left_outer,
                self.right if self.right_outer is None else self.right_outer)


@dataclass(frozen=True)
class Corner:
    """The curb return between two neighbouring arms (second is counter-clockwise of first)."""

    first: int  # edge ids
    second: int
    angle: float  # between the arms [rad]
    radius: float  # of the curb return [m], after shrinking
    wanted: float  # the class radius before shrinking [m]


def junction_trims(arms: list[Arm], margin: float, max_share: float = 1.0) -> dict[int, float]:
    """Distance from the node at which each arm's road must stop.

    Baseline: the widest half carriageway (to the curb line) at the node, as
    for right-angle junctions. For two neighboring arms an angle theta apart, the facing road
    edges (offset a = left width of the first, b = right width of the second)
    cross at distance d1 = (b + a cos theta) / sin theta along the first arm and
    d2 = (a + b cos theta) / sin theta along the second; each arm is trimmed past
    every such crossing, so acute junctions never overlap. The facing curb lines
    are joined by a curb return of radius r (junction_corners), which adds its
    tangent length r / tan(theta / 2) to both crossings; the outer edges
    (sidewalks) only must not overlap.

    The crossing only matters when it lies on both arms (d1 > 0 and d2 > 0).
    Otherwise it lies behind one arm's start, and the two road surfaces only
    overlap within b |tan theta| <= a of the node, which the baseline covers.
    Near-straight arms of different widths (theta close to pi) would
    otherwise get a trim of about (b - a) / sin theta, hundreds of meters.

    Raises ValueError for arms closer than MIN_ARM_ANGLE_DEG.
    """
    base = max(max(arm.left, arm.right) for arm in arms)
    need = {arm.edge_id: base for arm in arms}
    radius = {(c.first, c.second): c.radius for c in junction_corners(arms, max_share)}
    for first, second, theta in _neighbours(arms):
        sin, cos = math.sin(theta), math.cos(theta)
        tangent = radius.get((first.edge_id, second.edge_id), 0.0) / math.tan(theta / 2)
        for a, b, extra in ((first.left, second.right, tangent), (first.outer[0], second.outer[1], 0.0)):
            d1, d2 = (b + a * cos) / sin, (a + b * cos) / sin
            if d1 <= 0 or d2 <= 0:
                continue  # crossing behind one arm's start: within the baseline
            need[first.edge_id] = max(need[first.edge_id], d1 + extra)
            need[second.edge_id] = max(need[second.edge_id], d2 + extra)
    return {eid: d + margin for eid, d in need.items()}


def junction_corners(arms: list[Arm], max_share: float = 1.0) -> list[Corner]:
    """Curb returns between neighbouring arms that both have a radius, where three or
    more roads meet (two roads meeting are one road continuing: a bend or a lane-count
    change kept as a junction).

    The radius is the smaller of the two arms' (StreetGen sec. 2.4), shrunk so
    that its tangent length r / tan(theta / 2) takes at most max_share of the
    shorter arm (sec. 2.5: the axis is too short for the guessed radius).
    """
    out: list[Corner] = []
    if len(arms) < 3:
        return out
    for first, second, theta in _neighbours(arms):
        wanted = min(first.radius, second.radius)
        if wanted <= 0:
            continue
        room = max_share * min(first.length, second.length) * math.tan(theta / 2)
        out.append(Corner(first.edge_id, second.edge_id, theta, min(wanted, room), wanted))
    return out


def _neighbours(arms: list[Arm]) -> list[tuple[Arm, Arm, float]]:
    """(first, second, angle) of neighbouring arms counter-clockwise, facing edges
    diverging (angle >= pi) left out. Raises ValueError for arms closer than
    MIN_ARM_ANGLE_DEG."""
    ordered = sorted(arms, key=lambda arm: (arm.heading % (2 * math.pi), arm.edge_id))
    out = []
    if len(ordered) >= 2:
        for i, first in enumerate(ordered):
            second = ordered[(i + 1) % len(ordered)]
            theta = (second.heading - first.heading) % (2 * math.pi)
            if theta < math.radians(MIN_ARM_ANGLE_DEG):
                raise ValueError(
                    f"edges {first.edge_id} and {second.edge_id} meet at {math.degrees(theta):.1f} deg "
                    f"(< {MIN_ARM_ANGLE_DEG} deg); the junction cannot be built"
                )
            if theta < math.pi - 1e-9:
                out.append((first, second, theta))
    return out


def oriented_lanes(edge: Edge) -> tuple[int, int, bool]:
    """(right_lanes, left_lanes, reversed) for the road built from edge.

    Backward-only one-way edges get a reversed reference line so that all
    lanes sit on the right side.
    """
    if edge.lanes_forward == 0:
        return edge.lanes_backward, 0, True
    return edge.lanes_forward, edge.lanes_backward, False


def node_arms(graph: RoadGraph, node_id: int, cross_section: Optional[CrossSectionParams] = None) -> list[Arm]:
    """The roads leaving a junction node as arms; with cross_section, with their side
    lanes as they reach the junction, their curb-return radius and length."""
    node = graph.nodes[node_id]
    loops = loop_edges(graph) if cross_section is not None else frozenset()
    incident = graph.incident_edges(node_id)
    # Where a loop starts (roundabout entry, cul-de-sac stem) its transform designed the
    # corners: no class curb returns there, and bike lanes end before it.
    at_loop = any(e.id in loops for e in incident)
    arms = []
    for e in incident:
        right, left, reverse = oriented_lanes(e)
        start_node = e.to_node if reverse else e.from_node
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        heading = math.atan2(other.y - node.y, other.x - node.x)
        # Leaving the node along +s keeps the lane sides; along -s swaps them.
        if cross_section is None:
            lw, rw = left * e.lane_width, right * e.lane_width
            if node_id != start_node:
                lw, rw = rw, lw
            arms.append(Arm(e.id, heading, lw, rw))
            continue
        loop = e.id in loops
        rc, lc, ro, lo = side_half_widths(e, cross_section, right, left, at_junction=True,
                                          ramp=node.kind == "ramp", loop=loop, at_loop=at_loop)
        if node_id != start_node:
            rc, lc, ro, lo = lc, rc, lo, ro
        radius = 0.0 if at_loop else edge_cross_section(e, cross_section, loop).radius
        arms.append(Arm(e.id, heading, lc, rc, lo, ro, radius, graph.edge_length(e.id)))
    return arms


def node_trims(graph: RoadGraph, node_id: int, margin: float = DEFAULT_MARGIN,
               cross_section: Optional[CrossSectionParams] = None) -> dict[int, float]:
    """Per incident edge: how far from the junction node its road stops [m]."""
    arms = node_arms(graph, node_id, cross_section)
    try:
        if cross_section is None:
            return junction_trims(arms, margin)
        return junction_trims(arms, margin, cross_section.max_corner_share)
    except ValueError as err:
        raise ValueError(f"node {node_id}: {err}") from None


def node_corners(graph: RoadGraph, node_id: int, cross_section: CrossSectionParams) -> list[Corner]:
    """The curb returns of a junction node, radii shrunk to fit."""
    try:
        return junction_corners(node_arms(graph, node_id, cross_section), cross_section.max_corner_share)
    except ValueError as err:
        raise ValueError(f"node {node_id}: {err}") from None

