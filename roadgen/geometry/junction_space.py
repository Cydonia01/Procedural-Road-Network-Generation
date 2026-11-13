"""How much room a junction needs: where the roads meeting at a node must stop.

Pure geometry on arm headings and road widths, shared by the OpenDRIVE
builder, the 3D ground and the generators' repair pass. With a cross-section
(stage B, roadgen.geometry.cross_section) an arm's width counts a left-turn
pocket on its incoming side; medians have ended before the junction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from roadgen.config import DEFAULTS
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    half_widths,
    loop_edges,
    pocket_ends,
)
from roadgen.graph.model import Edge, RoadGraph

DEFAULT_MARGIN = DEFAULTS["opendrive"]["junction_margin"]  # [m]
MIN_ARM_ANGLE_DEG = 10.0  # roads meeting at a sharper angle cannot be separated sensibly


@dataclass(frozen=True)
class Arm:
    """A road leaving a junction node, seen from the node."""

    edge_id: int
    heading: float  # outward direction from the node [rad]
    left: float  # road width on the counter-clockwise side of `heading` [m]
    right: float  # road width on the clockwise side [m]


def junction_trims(arms: list[Arm], margin: float) -> dict[int, float]:
    """Distance from the node at which each arm's road must stop.

    Baseline: the widest half road at the node, as for right-angle junctions.
    For two neighboring arms an angle theta apart, the facing road edges
    (offset a = left width of the first, b = right width of the second) cross
    at distance d1 = (b + a cos theta) / sin theta along the first arm and
    d2 = (a + b cos theta) / sin theta along the second; each arm is trimmed
    past every such crossing, so acute junctions never overlap.

    The crossing only matters when it lies on both arms (d1 > 0 and d2 > 0).
    Otherwise it lies behind one arm's start, and the two road surfaces only
    overlap within b |tan theta| <= a of the node, which the baseline covers.
    Near-straight arms of different widths (theta close to pi) would
    otherwise get a trim of about (b - a) / sin theta, hundreds of meters.

    Raises ValueError for arms closer than MIN_ARM_ANGLE_DEG.
    """
    base = max(max(arm.left, arm.right) for arm in arms)
    need = {arm.edge_id: base for arm in arms}
    for first, second, theta in _neighbours(arms):
        sin, cos = math.sin(theta), math.cos(theta)
        a, b = first.left, second.right
        d1, d2 = (b + a * cos) / sin, (a + b * cos) / sin
        if d1 <= 0 or d2 <= 0:
            continue  # crossing behind one arm's start: within the baseline
        need[first.edge_id] = max(need[first.edge_id], d1)
        need[second.edge_id] = max(need[second.edge_id], d2)
    return {eid: d + margin for eid, d in need.items()}


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
    """The roads leaving a junction node as arms; with cross_section, with their
    left-turn pockets."""
    node = graph.nodes[node_id]
    loops = loop_edges(graph) if cross_section is not None else frozenset()
    pockets = (pocket_ends(graph, node_id, cross_section.turn_lanes)
               if cross_section is not None and cross_section.turn_lanes is not None else frozenset())
    arms = []
    for e in graph.incident_edges(node_id):
        right, left, reverse = oriented_lanes(e)
        start_node = e.to_node if reverse else e.from_node
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        heading = math.atan2(other.y - node.y, other.x - node.x)
        if cross_section is None:
            rw, lw = right * e.lane_width, left * e.lane_width
        else:
            rw, lw = half_widths(e, cross_section, right, left, at_junction=True, loop=e.id in loops)
        # Leaving the node along +s keeps the lane sides; along -s swaps them.
        if node_id != start_node:
            rw, lw = lw, rw
        if e.id in pockets:  # a left-turn pocket widens the incoming side: the arm's left
            lw += e.lane_width
        arms.append(Arm(e.id, heading, lw, rw))
    return arms


def node_trims(graph: RoadGraph, node_id: int, margin: float = DEFAULT_MARGIN,
               cross_section: Optional[CrossSectionParams] = None) -> dict[int, float]:
    """Per incident edge: how far from the junction node its road stops [m].

    The result is memoised on the graph, keyed on everything it reads (_trims_key): the
    HD repair asks for every junction's trims after each change, and most of them are
    unchanged (B9)."""
    cache = graph.__dict__.setdefault("_trims_cache", {})
    key = _trims_key(graph, node_id, margin, cross_section)
    if key not in cache:
        cache[key] = _node_trims(graph, node_id, margin, cross_section)
    return dict(cache[key])


def _trims_key(graph: RoadGraph, node_id: int, margin: float, cross_section: Optional[CrossSectionParams]) -> tuple:
    """What node_trims reads with a cross-section: the node, each incident edge with its
    far node's position and loop membership, roundabout entries, the pockets left out
    there, the margin and the parameters."""
    node = graph.nodes[node_id]
    rings = graph.metadata.get("roundabouts", [])
    loops = [set(r["ring"]) for r in rings] + [set(c["loop"]) for c in graph.metadata.get("culdesacs", [])]
    arms = []
    for e in graph.incident_edges(node_id):
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        arms.append((e.id, e.from_node, e.to_node, other.x, other.y, e.road_class, e.lanes_forward,
                     e.lanes_backward, e.lane_width, e.cross_section,
                     any(e.from_node in s and e.to_node in s for s in loops)))
    if cross_section is None:  # only the driving lanes count
        return node_id, node.x, node.y, margin, tuple(sorted(arms))
    left_out = tuple(sorted(e for n, e in graph.metadata.get("no_pockets", []) if n == node_id))
    return (node_id, node.x, node.y, node.kind, node.control, margin, repr(cross_section), tuple(sorted(arms)),
            any(node_id in r.get("entries", []) for r in rings), left_out)


def _node_trims(graph: RoadGraph, node_id: int, margin: float,
                cross_section: Optional[CrossSectionParams]) -> dict[int, float]:
    arms = node_arms(graph, node_id, cross_section)
    try:
        return junction_trims(arms, margin)
    except ValueError as err:
        raise ValueError(f"node {node_id}: {err}") from None
