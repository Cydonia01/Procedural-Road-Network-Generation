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
pockets).

Loops (roundabout rings and cul-de-sac bulbs, listed in the graph metadata)
are one-way and run counter-clockwise around an island: outside they keep
only the profile's curb and sidewalk, on the island side only its curb.

Corner radius: the curb return between two neighbouring arms of a junction,
by road class, the smaller of the two (StreetGen sec. 2.4; 3, 4.9 and 7.6 m
in Paris), shrunk where the arms are short (sec. 2.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from roadgen.config import DEFAULTS, section
from roadgen.graph.model import Edge, RoadGraph

SideKind = Literal["parking", "biking", "curb", "sidewalk", "shoulder", "border"]
SIDE_KINDS = ("parking", "biking", "curb", "sidewalk", "shoulder", "border")
CARRIAGEWAY_KINDS = frozenset({"parking", "biking", "shoulder"})  # paved, inside the curb line
PROFILES = ("urban", "suburban", "rural", "none")
SHOULDERS_END = frozenset({"urban", "suburban"})  # profiles whose shoulders end before junctions
CLASSES = ("street", "avenue", "highway")

_D = section(DEFAULTS, "cross_section")

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
             at_loop: bool = False) -> tuple[SideLane, ...]:
        """Side lanes of one side; at a junction without parking, without shoulders (at a
        ramp node, or in the urban and suburban profiles) and without bike lanes where a
        loop starts (at_loop)."""
        lanes = self.right if side == "right" else self.left
        if not at_junction:
            return lanes
        gone = {"parking"} | ({"biking"} if at_loop else set())
        if ramp or self.profile in SHOULDERS_END:
            gone.add("shoulder")
        return tuple(s for s in lanes if s.kind not in gone)

    def widths(self, side: str, at_junction: bool = False, ramp: bool = False,
               at_loop: bool = False) -> tuple[float, float, float]:
        """(median half width, carriageway side width, outer side width) [m]: what the road
        adds on one side to its driving lanes, inside the curb line and in all."""
        lanes = self.side(side, at_junction, ramp, at_loop)
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
                     at_loop: bool = False) -> tuple[float, float, float, float]:
    """(right curb, left curb, right outer, left outer) half widths of the edge's road [m],
    from its reference line, given its driving lanes on each side (see oriented_lanes).
    loop: the edge is on a loop; at_loop: the junction is where a loop starts."""
    xs = edge_cross_section(edge, params, loop)
    rw, lw = right_lanes * edge.lane_width, left_lanes * edge.lane_width
    rm, ri, ro = xs.widths("right", at_junction, ramp, at_loop)
    lm, li, lo = xs.widths("left", at_junction, ramp, at_loop)
    return rw + rm + ri, lw + lm + li, rw + rm + ro, lw + lm + lo


def outer_half_width(edge: Edge, params: CrossSectionParams, loop: bool = False) -> float:
    """Widest side of the whole road from its reference line, side lanes included [m]."""
    if edge.is_one_way:
        right, left = edge.total_lanes, 0
    else:
        right, left = edge.lanes_forward, edge.lanes_backward
    _, _, r_out, l_out = side_half_widths(edge, params, right, left, loop=loop)
    return max(r_out, l_out)
