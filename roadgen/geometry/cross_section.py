"""Road cross-sections: the lanes beside the driving lanes (stage B).

Neutral geometry like junction_space: the OpenDRIVE builder lays out the
side lanes from it, and the junction trims, the alignment's clearance and
the repair take a road's widths from it, so they all agree.

A profile (urban, suburban, rural, none) gives, per road class, the side
lanes on each side of the road from the driving lanes outward, as
(kind, width [m], height [m]); the height raises curbs and sidewalks. A
one-way road has them on both edges. Two-way roads with at least
median_min_lanes lanes each way also get a median of their class's width at
the centre line. An edge's profile is Edge.cross_section, or the params'
default profile.

At a junction a road is narrower than along its length: parking lanes end
parking_clear before it (curb extensions), shoulders end before it in the
urban and suburban profiles (rural roads keep their paved shoulder through
the corner) and before every ramp node (the gore between highway and ramp),
bike lanes before roundabout entries
(FHWA roundabout guide), and medians taper out (or, later, become turn
pockets). Where the side lanes leave a block too short for its junctions,
the repair first ends only that road end's bike lanes before the junction
(graph metadata "no_bike_lanes", bike_ends: the curb, sidewalk and curb return
stay), and if that is not enough makes it bare (graph metadata "no_side_lanes",
[node, edge] pairs; bare_ends): parking, bike lanes, sidewalks and shoulders
all end before the junction, the curb stays, and its corners get no class curb
return (stage B9, prompt 13).

Loops (roundabout rings and cul-de-sac bulbs, listed in the graph metadata)
are one-way and run counter-clockwise around an island: outside they keep
only the profile's curb and sidewalk, on the island side only its curb.

Corner radius: the curb return between two neighbouring arms of a junction,
by road class, the smaller of the two (StreetGen sec. 2.4; 3, 4.9 and 7.6 m
in Paris), shrunk where the arms are short (sec. 2.5).

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
from typing import Literal, Optional

from roadgen.config import DEFAULTS, section
from roadgen.graph.model import Edge, RoadGraph

SideKind = Literal["parking", "biking", "curb", "sidewalk", "shoulder", "border"]
SIDE_KINDS = ("parking", "biking", "curb", "sidewalk", "shoulder", "border")
CARRIAGEWAY_KINDS = frozenset({"parking", "biking", "shoulder"})  # paved, inside the curb line
PROFILES = ("urban", "suburban", "rural", "none")
SHOULDERS_END = frozenset({"urban", "suburban"})  # profiles whose shoulders end before junctions
BARE_DROP = frozenset({"parking", "biking", "sidewalk", "shoulder"})  # side lanes a bare road end leaves out
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

Layout = tuple[tuple[str, float, float], ...]  # (kind, width [m], height [m]) from the driving lanes outward


@dataclass(frozen=True)
class SideLane:
    kind: SideKind
    width: float  # [m]
    height: float = 0.0  # above the road surface [m]


@dataclass(frozen=True)
class CrossSectionParams:
    profile: str = _D["profile"]  # profile of edges without Edge.cross_section
    urban_street: Layout = _D["urban_street"]
    urban_avenue: Layout = _D["urban_avenue"]
    urban_highway: Layout = _D["urban_highway"]
    suburban_street: Layout = _D["suburban_street"]
    suburban_avenue: Layout = _D["suburban_avenue"]
    suburban_highway: Layout = _D["suburban_highway"]
    rural_street: Layout = _D["rural_street"]
    rural_avenue: Layout = _D["rural_avenue"]
    rural_highway: Layout = _D["rural_highway"]
    median: tuple[float, float, float] = _D["median"]  # street, avenue, highway: total width [m]
    median_min_lanes: int = _D["median_min_lanes"]  # lanes each way a road needs for a median
    parking_clear: float = _D["parking_clear"]  # parking ends this far before a junction [m]
    side_taper: float = _D["side_taper"]  # side lanes that end narrow over this length [m]
    tunnel_shoulder: float = _D["tunnel_shoulder"]  # shoulders inside tunnels [m]
    corner_radius: tuple[float, float, float] = _D["corner_radius"]  # street, avenue, highway [m]
    max_corner_share: float = _D["max_corner_share"]  # most of the shorter arm a curb return takes
    corner_roads: bool = _D["corner_roads"]  # side lanes around junction corners (off: throughput benchmarks)
    # Turn pockets (stage B8; not a config key: HdMapParams sets it from turn_lanes); None = none.
    turn_lanes: Optional[TurnLaneParams] = field(default=None, metadata={"config": False})

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"unknown cross-section profile {self.profile!r}; available: {PROFILES}")
        for profile in PROFILES[:-1]:
            for cls in CLASSES:
                for lane in getattr(self, f"{profile}_{cls}"):
                    kind, width, height = lane
                    if kind not in SIDE_KINDS:
                        raise ValueError(f"{profile}_{cls}: unknown side lane kind {kind!r}; available: {SIDE_KINDS}")
                    if width <= 0 or height < 0:
                        raise ValueError(f"{profile}_{cls}: side lanes need width > 0 and height >= 0")
        if len(self.median) != 3 or min(self.median) < 0 or self.median_min_lanes < 1:
            raise ValueError("median needs 3 widths >= 0 (street, avenue, highway) and median_min_lanes >= 1")
        if len(self.corner_radius) != 3 or min(self.corner_radius) < 0:
            raise ValueError("corner_radius needs 3 radii >= 0 (street, avenue, highway)")
        if min(self.parking_clear, self.side_taper, self.tunnel_shoulder) < 0 or not 0 < self.max_corner_share <= 1:
            raise ValueError("need parking_clear, side_taper, tunnel_shoulder >= 0 and 0 < max_corner_share <= 1")

    def sides(self, profile: str, road_class: str) -> tuple[SideLane, ...]:
        """Side lanes of one side of a road, from the driving lanes outward."""
        if profile not in PROFILES:
            raise ValueError(f"unknown cross-section profile {profile!r}; available: {PROFILES}")
        if profile == "none":
            return ()
        return tuple(SideLane(kind, float(w), float(h)) for kind, w, h in getattr(self, f"{profile}_{road_class}"))

    def radius(self, road_class: str) -> float:
        """Curb-return radius of the class [m]."""
        return float(self.corner_radius[CLASSES.index(road_class)])


@dataclass(frozen=True)
class EdgeCrossSection:
    """What one edge's road has beside its driving lanes, on the right and on the left of
    its reference line (the driving lanes' right; a one-way road's lanes are all right)."""

    profile: str
    right: tuple[SideLane, ...]  # from the driving lanes outward
    left: tuple[SideLane, ...]
    median: float  # total width at the centre line [m]; 0 = none
    radius: float  # curb-return radius at its junctions [m]; 0 = none

    def side(self, side: str, at_junction: bool = False, ramp: bool = False,
             at_loop: bool = False, bare: bool = False) -> tuple[SideLane, ...]:
        """Side lanes of one side; at a junction without parking, without shoulders (at a
        ramp node, or in the urban and suburban profiles) and without bike lanes where a
        loop starts (at_loop); at a bare road end (see bare_ends) only the curb."""
        lanes = self.right if side == "right" else self.left
        if not at_junction:
            return lanes
        gone = {"parking"} | ({"biking"} if at_loop else set()) | (set(BARE_DROP) if bare else set())
        if ramp or self.profile in SHOULDERS_END:
            gone.add("shoulder")
        return tuple(s for s in lanes if s.kind not in gone)

    def widths(self, side: str, at_junction: bool = False, ramp: bool = False,
               at_loop: bool = False, bare: bool = False) -> tuple[float, float, float]:
        """(median half width, carriageway side width, outer side width) [m]: what the road
        adds on one side to its driving lanes, inside the curb line and in all."""
        lanes = self.side(side, at_junction, ramp, at_loop, bare)
        inner = 0.0
        for s in lanes:
            if s.kind not in CARRIAGEWAY_KINDS:
                break
            inner += s.width
        half_median = 0.0 if at_junction else self.median / 2
        return half_median, inner, sum(s.width for s in lanes)


def loop_edges(graph: RoadGraph) -> frozenset[int]:
    """Edges of roundabout rings and cul-de-sac loops (graph.metadata "roundabouts" /
    "culdesacs"): both ends on the same loop."""
    loops = [set(r["ring"]) for r in graph.metadata.get("roundabouts", [])]
    loops += [set(c["loop"]) for c in graph.metadata.get("culdesacs", [])]
    return frozenset(e.id for e in graph.edges.values()
                     if any(e.from_node in nodes and e.to_node in nodes for nodes in loops))


def edge_cross_section(edge: Edge, params: CrossSectionParams, loop: bool = False) -> EdgeCrossSection:
    """The edge's profile (Edge.cross_section or the default) laid out for its class; loop:
    the edge is part of a roundabout ring or cul-de-sac loop (see loop_edges)."""
    profile = edge.cross_section or params.profile
    if profile not in PROFILES:
        raise ValueError(f"edge {edge.id}: unknown cross-section profile {profile!r}; available: {PROFILES}")
    k = CLASSES.index(edge.road_class)
    sides = params.sides(profile, edge.road_class)
    radius = params.radius(edge.road_class) if profile != "none" else 0.0
    if loop:  # counter-clockwise around an island, which is on the left
        outside = tuple(s for s in sides if s.kind not in CARRIAGEWAY_KINDS)
        return EdgeCrossSection(profile, outside, tuple(s for s in sides if s.kind == "curb"), 0.0, radius)
    two_way = min(edge.lanes_forward, edge.lanes_backward) >= params.median_min_lanes
    median = params.median[k] if profile != "none" and two_way else 0.0
    return EdgeCrossSection(profile, sides, sides, median, radius)


def side_half_widths(edge: Edge, params: CrossSectionParams, right_lanes: int, left_lanes: int,
                     at_junction: bool = False, ramp: bool = False, loop: bool = False,
                     at_loop: bool = False, bare: bool = False) -> tuple[float, float, float, float]:
    """(right curb, left curb, right outer, left outer) half widths of the edge's road [m],
    from its reference line, given its driving lanes on each side (see oriented_lanes).
    loop: the edge is on a loop; at_loop: the junction is where a loop starts; bare: a
    bare road end (see bare_ends)."""
    xs = edge_cross_section(edge, params, loop)
    rw, lw = right_lanes * edge.lane_width, left_lanes * edge.lane_width
    rm, ri, ro = xs.widths("right", at_junction, ramp, at_loop, bare)
    lm, li, lo = xs.widths("left", at_junction, ramp, at_loop, bare)
    return rw + rm + ri, lw + lm + li, rw + rm + ro, lw + lm + lo


def outer_half_width(edge: Edge, params: CrossSectionParams, loop: bool = False) -> float:
    """Widest side of the whole road from its reference line, side lanes included [m]."""
    if edge.is_one_way:
        right, left = edge.total_lanes, 0
    else:
        right, left = edge.lanes_forward, edge.lanes_backward
    _, _, r_out, l_out = side_half_widths(edge, params, right, left, loop=loop)
    return max(r_out, l_out)


def bike_ends(graph: RoadGraph, node_id: int) -> frozenset[int]:
    """Edges whose bike lanes end before node_id, the curb and sidewalk going on round the
    corner (the repair's graph metadata "no_bike_lanes", tried before a bare end)."""
    return frozenset(e for n, e in graph.metadata.get("no_bike_lanes", []) if n == node_id)


def bare_ends(graph: RoadGraph, node_id: int) -> frozenset[int]:
    """Edges whose road ends bare at node_id: only the curb reaches the junction (the
    repair's graph metadata "no_side_lanes")."""
    return frozenset(e for n, e in graph.metadata.get("no_side_lanes", []) if n == node_id)


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
