"""How much room a junction needs: where the roads meeting at a node must stop.

Pure geometry on arm headings and road widths, shared by the OpenDRIVE
builder, the 3D ground and the generators' repair pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from roadgen.config import DEFAULTS
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

    Baseline: the widest half cross-section at the node, as for right-angle
    junctions. For two neighboring arms an angle theta apart, the facing road
    edges (offset a = left width of the first, b = right width of the second)
    cross at distance d1 = (b + a cos theta) / sin theta along the first arm and
    d2 = (a + b cos theta) / sin theta along the second; each arm is trimmed past
    every such crossing, so acute junctions never overlap.

    The crossing only matters when it lies on both arms (d1 > 0 and d2 > 0).
    Otherwise it lies behind one arm's start, and the two road surfaces only
    overlap within b |tan theta| <= a of the node, which the baseline covers.
    Near-straight arms of different widths (theta close to pi) would
    otherwise get a trim of about (b - a) / sin theta, hundreds of meters.

    Raises ValueError for arms closer than MIN_ARM_ANGLE_DEG.
    """
    base = max(max(arm.left, arm.right) for arm in arms)
    need = {arm.edge_id: base for arm in arms}
    ordered = sorted(arms, key=lambda arm: (arm.heading % (2 * math.pi), arm.edge_id))
    if len(ordered) >= 2:
        for i, first in enumerate(ordered):
            second = ordered[(i + 1) % len(ordered)]
            theta = (second.heading - first.heading) % (2 * math.pi)
            if theta < math.radians(MIN_ARM_ANGLE_DEG):
                raise ValueError(
                    f"edges {first.edge_id} and {second.edge_id} meet at {math.degrees(theta):.1f} deg "
                    f"(< {MIN_ARM_ANGLE_DEG} deg); the junction cannot be built"
                )
            if theta >= math.pi - 1e-9:
                continue  # facing edges diverge: no constraint
            a, b = first.left, second.right
            sin, cos = math.sin(theta), math.cos(theta)
            d1, d2 = (b + a * cos) / sin, (a + b * cos) / sin
            if d1 <= 0 or d2 <= 0:
                continue  # crossing behind one arm's start: within the baseline
            need[first.edge_id] = max(need[first.edge_id], d1)
            need[second.edge_id] = max(need[second.edge_id], d2)
    return {eid: d + margin for eid, d in need.items()}


def oriented_lanes(edge: Edge) -> tuple[int, int, bool]:
    """(right_lanes, left_lanes, reversed) for the road built from edge.

    Backward-only one-way edges get a reversed reference line so that all
    lanes sit on the right side.
    """
    if edge.lanes_forward == 0:
        return edge.lanes_backward, 0, True
    return edge.lanes_forward, edge.lanes_backward, False


def node_trims(graph: RoadGraph, node_id: int, margin: float = DEFAULT_MARGIN) -> dict[int, float]:
    """Per incident edge: how far from the junction node its road stops [m]."""
    node = graph.nodes[node_id]
    arms = []
    for e in graph.incident_edges(node_id):
        right, left, reverse = oriented_lanes(e)
        start_node = e.to_node if reverse else e.from_node
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        heading = math.atan2(other.y - node.y, other.x - node.x)
        # Leaving the node along +s keeps the lane sides; along -s swaps them.
        lw, rw = left * e.lane_width, right * e.lane_width
        if node_id != start_node:
            lw, rw = rw, lw
        arms.append(Arm(e.id, heading, lw, rw))
    try:
        return junction_trims(arms, margin)
    except ValueError as err:
        raise ValueError(f"node {node_id}: {err}") from None
