"""Road cross-sections: what a road has besides its driving lanes (stage B).

Neutral geometry like junction_space: the OpenDRIVE builder lays out the
lanes from it, and the junction trims, the alignment's clearance and the
repair take a road's widths from it, so they all agree.

The roads carry driving lanes only (polish N1: no parking, bike lanes,
shoulders, curbs or sidewalks; none of them is driven). Two-way roads with at
least median_min_lanes lanes each way get a median of their class's width at
the centre line; it tapers out median_end before a junction (or, later,
becomes a turn pocket). An edge's profile (Edge.cross_section, else the
default; urban, suburban, rural from `annotate`) is its area type: the road
marks read it, and the profile none has no median.

Left-turn pockets (stage B8, TurnLaneParams): an extra innermost lane on a
two-way road's approach to a junction, over its last pocket_length after a
pocket_taper, for left turns only. Decided from the graph alone
(pocket_ends), before the trims, which count its width: on approaches of the
pocket_classes that have a left exit, at junctions of three or more roads
that are not ramp nodes or roundabout entries and whose Node.control does
not rule out a pocket, where the edge has room for the pocket, its taper and
POCKET_ROOM at the far end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from roadgen.config import DEFAULTS, section
from roadgen.graph.model import Edge, RoadGraph

PROFILES = ("urban", "suburban", "rural", "none")  # area types (annotate: by population); none: no median
CLASSES = ("street", "avenue", "highway")

_D = section(DEFAULTS, "cross_section")
_DT = section(DEFAULTS, "turn_lanes")
POCKET_ROOM = 40.0  # an edge with a pocket keeps this much beyond it (the far junction's trim and zone) [m]
LEFT_EXIT = (math.pi / 4, math.pi * 0.95)  # a turn this far to the left is a left exit [rad]


@dataclass(frozen=True)
class TurnLaneParams:
    left_pockets: bool = _DT["left_pockets"]  # left-turn pockets on junction approaches
    pocket_classes: tuple[str, ...] = _DT["pocket_classes"]  # road classes whose approaches get them
    pocket_length: tuple[float, float, float] = _DT["pocket_length"]  # street, avenue, highway [m]
    pocket_taper: float = _DT["pocket_taper"]  # [m]

    def __post_init__(self) -> None:
        if set(self.pocket_classes) - set(CLASSES):
            raise ValueError(f"turn_lanes.pocket_classes: unknown classes; available: {CLASSES}")
        if len(self.pocket_length) != 3 or min(self.pocket_length) <= 0 or self.pocket_taper <= 0:
            raise ValueError("turn_lanes needs 3 pocket lengths > 0 (street, avenue, highway) and pocket_taper > 0")

    def length(self, road_class: str) -> float:
        return float(self.pocket_length[CLASSES.index(road_class)])

@dataclass(frozen=True)
class CrossSectionParams:
    profile: str = _D["profile"]  # profile of edges without Edge.cross_section
    median: tuple[float, float, float] = _D["median"]  # street, avenue, highway: total width [m]
    median_min_lanes: int = _D["median_min_lanes"]  # lanes each way a road needs for a median
    median_end: float = _D["median_end"]  # medians end this far before a junction [m]
    # Turn pockets (stage B8; not a config key: HdMapParams sets it from turn_lanes); None = none.
    turn_lanes: Optional[TurnLaneParams] = field(default=None, metadata={"config": False})

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"unknown cross-section profile {self.profile!r}; available: {PROFILES}")
        if len(self.median) != 3 or min(self.median) < 0 or self.median_min_lanes < 1 or self.median_end < 0:
            raise ValueError("median needs 3 widths >= 0 (street, avenue, highway), median_min_lanes >= 1 "
                             "and median_end >= 0")

    def edge_profile(self, edge: Edge) -> str:
        """The edge's profile: Edge.cross_section, else the default."""
        profile = edge.cross_section or self.profile
        if profile not in PROFILES:
            raise ValueError(f"edge {edge.id}: unknown cross-section profile {profile!r}; available: {PROFILES}")
        return profile

    def median_width(self, edge: Edge) -> float:
        """Total median width of the edge's road [m]; 0 = none (or the profile none)."""
        two_way = min(edge.lanes_forward, edge.lanes_backward) >= self.median_min_lanes
        if not two_way or self.edge_profile(edge) == "none":
            return 0.0
        return float(self.median[CLASSES.index(edge.road_class)])


def half_widths(edge: Edge, params: CrossSectionParams, right_lanes: int, left_lanes: int,
                at_junction: bool = False, loop: bool = False) -> tuple[float, float]:
    """(right, left) half widths of the edge's road from its reference line [m], given its
    driving lanes on each side (see oriented_lanes): the lanes and half the median (none at
    a junction, where it has ended, nor on a loop)."""
    half = 0.0 if at_junction or loop else params.median_width(edge) / 2
    return right_lanes * edge.lane_width + half, left_lanes * edge.lane_width + half


def outer_half_width(edge: Edge, params: CrossSectionParams, loop: bool = False) -> float:
    """Widest side of the whole road from its reference line [m]."""
    if edge.is_one_way:
        right, left = edge.total_lanes, 0
    else:
        right, left = edge.lanes_forward, edge.lanes_backward
    return max(half_widths(edge, params, right, left, loop=loop))


def loop_edges(graph: RoadGraph, edges: Optional[Iterable[Edge]] = None) -> frozenset[int]:
    """Edges of roundabout rings and cul-de-sac loops (graph.metadata "roundabouts" /
    "culdesacs"): both ends on the same loop. edges: only look at these (e.g. a node's
    arms) instead of the whole graph."""
    loops = [set(r["ring"]) for r in graph.metadata.get("roundabouts", [])]
    loops += [set(c["loop"]) for c in graph.metadata.get("culdesacs", [])]
    return frozenset(e.id for e in (graph.edges.values() if edges is None else edges)
                     if any(e.from_node in nodes and e.to_node in nodes for nodes in loops))


def pocket_ends(graph: RoadGraph, node_id: int, params: TurnLaneParams) -> frozenset[int]:
    """Edges whose approach to node_id gets a left-turn pocket (see the module docstring)."""
    node = graph.nodes[node_id]
    incident = graph.incident_edges(node_id)
    if not params.left_pockets or node.kind == "ramp" or len(incident) < 3 \
            or node.control in ("none", "right_before_left"):
        return frozenset()
    for r in graph.metadata.get("roundabouts", []):
        if node_id in r.get("entries", []):
            return frozenset()
    headings = {}
    for e in incident:
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        headings[e.id] = math.atan2(other.y - node.y, other.x - node.x)  # leaving the node
    left_out = {e for n, e in graph.metadata.get("no_pockets", []) if n == node_id}  # by the repair
    out = []
    for e in incident:
        if e.road_class not in params.pocket_classes or e.is_one_way or e.id in left_out:
            continue
        arriving = headings[e.id] + math.pi  # travel heading of traffic reaching the node
        left_exit = False
        for f in incident:
            if f.id == e.id or not _leaves(f, node_id):
                continue
            turn = math.atan2(math.sin(headings[f.id] - arriving), math.cos(headings[f.id] - arriving))
            left_exit = left_exit or LEFT_EXIT[0] < turn < LEFT_EXIT[1]
        room = params.length(e.road_class) + params.pocket_taper + POCKET_ROOM
        if left_exit and graph.edge_length(e.id) >= room:
            out.append(e.id)
    return frozenset(out)


def _leaves(edge: Edge, node_id: int) -> bool:
    """Whether traffic can leave node_id along edge."""
    return edge.lanes_forward > 0 if edge.from_node == node_id else edge.lanes_backward > 0
