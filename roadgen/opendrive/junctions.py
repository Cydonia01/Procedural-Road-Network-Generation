"""Junction construction: one single-lane connecting road per lane movement.

Conventions (right-hand traffic):
- A road whose reference line ends at the junction (contact "end") brings
  traffic in on its right lanes (-1, -2, ...) and takes it out on its left
  lanes (1, 2, ...). Contact "start" is the mirror image.
- Lane lists are ordered from the center line outward, i.e. from the driver's
  leftmost lane to the rightmost lane.
- A connecting road's reference line is the lane center path; its single
  lane -1 is centered on it with a laneOffset of half the lane width.

Lane splits (stage C5, JunctionArt stage 3): where a straight exit has more
lanes than its entry, the outermost incoming lane also feeds the extra
outgoing lanes, so every outgoing lane is fed (not at ramp nodes, where the
extra lanes are left to the merging ramp).

Ramp nodes (stage C9, Node.kind "ramp"): where interchange ramps leave or
join a highway, a junction keeps only straight-through movements, right
exits and right merges: no left turn across the centre line, and no
movement from one ramp (one-way road) to another. Only turns within
RAMP_STRAIGHT of straight ahead count as straight there, so a ramp joining
at 15-25 deg merges into the outermost lane as a right turn.

Corner roads (stage B3, HD maps on): where both neighbouring arms of a
corner have side lanes (bike, curb, sidewalk, shoulder) at the junction, a
connecting road with only those lanes joins them around the corner. Its
reference line is the right turn from arm a's outermost incoming lane to arm
b's outermost outgoing lane (the same path as that movement's connecting
road), its lanes start at that turn lane's outer edge (laneOffset), so the
curb closes on the turn lane and no junction surface is left without a lane.
Lane widths blend smoothly from one arm's to the other's. The side lanes lie
on the inside of the turn, towards its centre of curvature: where the turn is
tight, the outermost one (the sidewalk) narrows so that the lanes reach at
most CORNER_REACH of the way to that centre (offset lanes beyond it would
fold over themselves). Without such a
turn (a one-way arm) the path joins the two carriageway edges instead. A
corner closes the junction surface only where a movement drives that turn
(CornerRoad.closes); elsewhere (between two roads into the junction, say)
some surface between the curb and the movements stays without a lane.
Corner roads are not movements: nothing drives them.

Lane use (stage B8, lane_use): which movement kinds each incoming lane
feeds. A left-turn pocket (a lane of kind "pocket" in RoadEnd.cross, from
the turn lanes of the cross-section) feeds only the left turns (and a
U-turn); the other lanes then feed no left turn. Banned turns (B8,
plan_movements' banned: pairs of road ends) are left out of an approach's
exits. Without pockets and bans the movements are those of before stage B.

U-turns (stage C7, JunctionParams.u_turns, off by default): at a junction of
three or more roads, the innermost incoming lane of a road may turn back
into the first outgoing lane of the same road whose centre is at least
2 * u_turn_radius away, so only wide roads get one (with 3.5 m lanes, three
or more lanes each way). Elsewhere roundabouts and cul-de-sac bulbs turn
traffic around.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal, Optional

import numpy as np
from scenariogeneration import xodr

from roadgen.config import DEFAULTS, section
from roadgen.geometry import design as design_lib
from roadgen.geometry.clothoid import g2_hermite
from roadgen.geometry.design import DesignParams
from roadgen.geometry.planar import (
    Poly3Curve,
    Pose,
    hermite_poly3,
    offset_point,
    segment_start,
    wrap_angle,
)
from roadgen.geometry.primitives import Piece
from roadgen.geometry.vertical import JunctionPlane, path_profiles

Contact = Literal["start", "end"]
MovementKind = Literal["straight", "left", "right", "uturn"]
PathKind = Literal["line", "clothoid", "poly3"]

_D = {k: v for k, v in section(DEFAULTS, "opendrive").items() if k != "junction_margin"}


@dataclass(frozen=True)
class JunctionParams:
    u_turns: bool = _D["u_turns"]  # U-turn movements where the road is wide enough
    u_turn_radius: float = _D["u_turn_radius"]  # [m] tightest U-turn: lane centres 2x this apart

    def __post_init__(self) -> None:
        if self.u_turn_radius <= 0:
            raise ValueError("u_turn_radius must be positive")

# |heading change| below this counts as straight.
STRAIGHT_THRESHOLD = math.pi / 4
RAMP_STRAIGHT = math.radians(5.0)  # ... at ramp nodes
ROAD_TYPE = xodr.RoadType.town

_CONTACT = {"start": xodr.ContactPoint.start, "end": xodr.ContactPoint.end}


@dataclass(frozen=True)
class RoadEnd:
    """One normal road's end at a junction.

    Its driving lanes are -1 .. -right_lanes and 1 .. left_lanes at lane_width
    each, unless lane_t gives them explicitly (stage B: a median or a turn
    pocket shifts the ids, and widths may differ): driving lane id -> its
    centre's offset t from the reference line at the contact point.
    """

    edge_id: int
    road_id: int
    contact: Contact
    pose: Pose  # reference line pose at the contact point, heading along +s
    right_lanes: int
    left_lanes: int
    lane_width: float
    speed_limit: float
    lane_t: tuple[tuple[int, float], ...] = ()
    # Stage B: every lane at the contact point as (id, kind, inner t, outer t, height);
    # () = driving lanes only (no corner roads).
    cross: tuple[tuple[int, str, float, float, float], ...] = ()

    @property
    def key(self) -> tuple[int, str]:
        return self.road_id, self.contact

    @property
    def pocket_lanes(self) -> list[int]:
        """Incoming left-turn pocket lanes (wider than 0 at the contact point)."""
        incoming = set(self.in_lanes)
        return [x[0] for x in self.cross if x[1] == "pocket" and x[0] in incoming and abs(x[3] - x[2]) > 1e-6]

    def _ids(self, right: bool) -> list[int]:
        if self.lane_t:
            return sorted((lane for lane, _ in self.lane_t if (lane < 0) == right), key=abs)
        return [-i for i in range(1, self.right_lanes + 1)] if right else list(range(1, self.left_lanes + 1))

    @property
    def in_lanes(self) -> list[int]:
        return self._ids(right=self.contact == "end")

    @property
    def out_lanes(self) -> list[int]:
        return self._ids(right=self.contact != "end")

    def lane_offset(self, lane_id: int) -> float:
        """Offset t [m] of a driving lane's centre from the reference line (left positive)."""
        if self.lane_t:
            return dict(self.lane_t)[lane_id]
        return math.copysign((abs(lane_id) - 0.5) * self.lane_width, lane_id)

    @property
    def heading_in(self) -> float:
        """Travel heading of traffic arriving at the junction."""
        return self.pose.hdg if self.contact == "end" else wrap_angle(self.pose.hdg + math.pi)

    @property
    def heading_out(self) -> float:
        """Travel heading of traffic leaving the junction."""
        return wrap_angle(self.heading_in + math.pi)

    def lane_pose(self, lane_id: int, incoming: bool) -> Pose:
        """Lane center at the contact point, heading in the travel direction."""
        x, y = offset_point(self.pose, self.lane_offset(lane_id))
        return Pose(x, y, self.heading_in if incoming else self.heading_out)


@dataclass(frozen=True)
class Movement:
    junction_id: int
    connecting_road: int
    kind: MovementKind
    turn_angle: float  # heading change [rad], counter-clockwise positive, in (-pi, pi]
    from_edge: int
    from_road: int
    from_lane: int
    to_edge: int
    to_road: int
    to_lane: int
    path: PathKind = "line"  # connecting-road shape: line, clothoid, or poly3 (G1 fallback)


def classify(heading_in: float, heading_out: float) -> MovementKind:
    diff = wrap_angle(heading_out - heading_in)
    if abs(diff) < STRAIGHT_THRESHOLD:
        return "straight"
    return "left" if diff > 0 else "right"


def classify_exits(turns: dict[int, float], threshold: float = STRAIGHT_THRESHOLD) -> dict[int, MovementKind]:
    """Kinds for all exits of one incoming road, given their turn angles.

    At most one exit is "straight": the one with the smallest turn, if within
    STRAIGHT_THRESHOLD. With five or more arms several exits can lie within
    the threshold; the others are left or right by the sign of their turn.
    """
    kinds: dict[int, MovementKind] = {k: ("left" if t > 0 else "right") for k, t in turns.items()}
    if turns:
        best = min(turns, key=lambda k: (abs(turns[k]), k))
        if abs(turns[best]) < threshold:
            kinds[best] = "straight"
    return kinds


def lane_pairs(kind: MovementKind, in_lanes: list[int], out_lanes: list[int],
               split: bool = False) -> list[tuple[int, int]]:
    if not in_lanes or not out_lanes:
        return []
    if kind == "straight":
        return straight_pairs(in_lanes, out_lanes, split)
    if kind == "right":
        return [(in_lanes[-1], out_lanes[-1])]
    return [(in_lanes[0], out_lanes[0])]


def straight_pairs(in_lanes: list[int], out_lanes: list[int],
                   split: bool = False) -> list[tuple[int, int]]:
    """Lane-to-lane straight movements, centre outward. With more outgoing than
    incoming lanes and split, the outermost incoming lane also feeds the extra
    ones (it fans out to the outer lanes); without split they stay unfed."""
    pairs = list(zip(in_lanes, out_lanes))
    if not split or len(out_lanes) <= len(in_lanes):
        return pairs
    return pairs + [(in_lanes[-1], lane) for lane in out_lanes[len(in_lanes):]]


def continuation_pairs(in_lanes: list[int], out_lanes: list[int]) -> list[tuple[int, int]]:
    """Lane mapping where the road simply continues (a two-arm junction).

    Every incoming lane gets a connection, so no lane dead-ends: lanes pair up
    from the center outward, and surplus incoming lanes merge into the
    outermost outgoing lane.
    """
    if not out_lanes:
        return []
    return [(lane, out_lanes[min(k, len(out_lanes) - 1)]) for k, lane in enumerate(in_lanes)]


def fill_pair(
    k: int, in_lanes: list[int], options: dict[MovementKind, list[int]]
) -> tuple[MovementKind, int] | None:
    """A movement for incoming lane index k (0 = leftmost) that has none yet.

    Without this, lanes that are straight-only would dead-end where the road
    cannot continue straight (e.g. a multi-lane one-way avenue at a T). The
    lane goes straight if possible; otherwise its half of the road decides
    the turn direction, falling back to the other side. Lanes are matched
    from the turn side, merging into the outermost lane when they run out.
    Returns (kind, out lane) or None if the lane has no exit at all.
    """
    n = len(in_lanes)
    order: list[MovementKind] = ["straight"]
    order += ["left", "right"] if k < n / 2 else ["right", "left"]
    for kind in order:
        if kind not in options:
            continue
        out = options[kind]
        if kind == "right":
            from_right = n - 1 - k
            return kind, out[len(out) - 1 - min(from_right, len(out) - 1)]
        return kind, out[min(k, len(out) - 1)]
    return None


def u_turn_lane(a: RoadEnd, radius: float) -> Optional[tuple[int, int]]:
    """(in lane, out lane) of a U-turn back onto road a, or None if it is too narrow:
    from the innermost incoming lane into the first outgoing lane whose centre is
    at least 2 * radius away."""
    if not a.in_lanes or not a.out_lanes:
        return None
    lane = a.in_lanes[0]
    for out in a.out_lanes:
        if abs(a.lane_offset(lane) - a.lane_offset(out)) >= 2 * radius - 1e-9:
            return lane, out
    return None


def plan_movements(junction_id: int, ends: list[RoadEnd], first_road_id: int,
                   params: JunctionParams = JunctionParams(), ramp: bool = False,
                   banned: frozenset = frozenset()) -> list[Movement]:
    """All lane movements through a junction, excluding U-turns, with road ids assigned.

    With exactly two arms the junction is just a bend or a lane-count change,
    so every lane continues regardless of the turn angle. With more arms the
    standard rules apply (right turns from the rightmost lane, left turns from
    the leftmost, straight lane-to-lane), and any incoming lane still without
    a movement is given one by fill_pair. A left-turn pocket takes the left
    turns; banned: (from road end key, to road end key) pairs left out.
    """
    movements: list[Movement] = []
    continuation = len(ends) == 2
    for a in ends:
        pocket = [] if continuation else a.pocket_lanes
        through = [lane for lane in a.in_lanes if lane not in pocket]
        pairs: dict[int, list[tuple[int, int]]] = {}
        kinds: dict[int, MovementKind] = {}
        options: dict[MovementKind, list[int]] = {}
        option_end: dict[MovementKind, int] = {}
        turns = {
            idx: wrap_angle(b.heading_out - a.heading_in)
            for idx, b in enumerate(ends)
            if a.edge_id != b.edge_id and b.out_lanes and (a.key, b.key) not in banned
        }
        exit_kinds = classify_exits(turns, RAMP_STRAIGHT if ramp else STRAIGHT_THRESHOLD)
        if ramp:  # no left turns, no ramp to ramp
            one_way = a.right_lanes == 0 or a.left_lanes == 0
            turns = {idx: t for idx, t in turns.items() if exit_kinds[idx] != "left"
                     and not (one_way and (ends[idx].right_lanes == 0 or ends[idx].left_lanes == 0))}
        for idx, b in enumerate(ends):
            if idx not in turns:
                continue
            kind = exit_kinds[idx]
            kinds[idx] = kind
            if continuation:
                pairs[idx] = continuation_pairs(a.in_lanes, b.out_lanes)
            elif pocket and kind == "left":
                pairs[idx] = lane_pairs(kind, pocket, b.out_lanes)
            else:
                # No lane split at a ramp node: the acceleration lane is the entry ramp's.
                pairs[idx] = lane_pairs(kind, through, b.out_lanes, split=not ramp)
            if kind not in options:
                options[kind], option_end[kind] = b.out_lanes, idx

        covered = {lane for ps in pairs.values() for lane, _ in ps}
        fill_options = {k: v for k, v in options.items() if k != "left"} if pocket else options
        for k, lane in enumerate(through):
            if lane in covered:
                continue
            fill = fill_pair(k, through, fill_options)
            if fill is not None:
                kind, out_lane = fill
                pairs[option_end[kind]].append((lane, out_lane))

        uturn = u_turn_lane(a, params.u_turn_radius) if params.u_turns and not continuation else None
        if uturn is not None:
            pairs[ends.index(a)] = [uturn]
            kinds[ends.index(a)], turns[ends.index(a)] = "uturn", math.pi
        for idx, b in enumerate(ends):
            for in_lane, out_lane in sorted(set(pairs.get(idx, [])), key=lambda p: (abs(p[0]), abs(p[1]))):
                movements.append(
                    Movement(
                        junction_id=junction_id,
                        connecting_road=first_road_id + len(movements),
                        kind=kinds[idx],
                        turn_angle=round(turns[idx], 6),
                        from_edge=a.edge_id,
                        from_road=a.road_id,
                        from_lane=in_lane,
                        to_edge=b.edge_id,
                        to_road=b.road_id,
                        to_lane=out_lane,
                    )
                )
    return movements


def lane_use(movements: list[Movement]) -> dict[tuple[int, int], dict[int, list[str]]]:
    """(road, side: -1 arriving at the road's end, +1 at its start) -> incoming lane -> the
    movement kinds it feeds, sorted; from a junction's movements."""
    out: dict[tuple[int, int], dict[int, set[str]]] = {}
    for m in movements:
        out.setdefault((m.from_road, -1 if m.from_lane < 0 else 1), {}).setdefault(m.from_lane, set()).add(m.kind)
    return {k: {lane: sorted(kinds) for lane, kinds in sorted(v.items(), key=lambda kv: abs(kv[0]))}
            for k, v in out.items()}


def build_junction(
    junction_id: int, ends: list[RoadEnd], first_road_id: int, plane: Optional[JunctionPlane] = None,
    params: JunctionParams = JunctionParams(), ramp: bool = False, paths: Optional[dict] = None,
    banned: frozenset = frozenset(),
) -> tuple[xodr.Junction, list[xodr.Road], list[Movement]]:
    """Junction, connecting roads and movements; with plane, connecting roads lie on it
    (height, grade and superelevation); ramp: an interchange ramp node. paths, if given,
    gets each connecting road's lane-centre path as (n, 2) points (right of way, B5)."""
    by_edge = {e.edge_id: e for e in ends}
    junction = xodr.Junction(f"junction_{junction_id}", junction_id)
    roads: list[xodr.Road] = []
    movements = plan_movements(junction_id, ends, first_road_id, params, ramp, banned)
    built = []
    for m in movements:
        a, b = by_edge[m.from_edge], by_edge[m.to_edge]
        road, path = _connecting_road(m, a, b, plane)
        kind = path.kind
        if paths is not None:
            ss = np.linspace(0.0, path.length, max(2, int(np.ceil(path.length / PATH_STEP)) + 1))
            paths[m.connecting_road] = np.array([path.point(float(s))[:2] for s in ss])
        roads.append(road)
        m = replace(m, path=kind)
        built.append(m)
        conn = xodr.Connection(m.from_road, m.connecting_road, xodr.ContactPoint.start)
        conn.add_lanelink(m.from_lane, -1)
        junction.add_connection(conn)
    return junction, roads, built


@dataclass(frozen=True)
class CornerRoad:
    """A corner road of a junction: side lanes from one arm to the next."""

    junction_id: int
    road: int
    from_road: int
    to_road: int
    kinds: tuple[str, ...]  # lane kinds, outward
    path: PathKind
    closes: bool  # a movement drives the right turn the curb follows: no surface left without a lane


PATH_STEP = 1.0  # sampling of a turn path handed out for right of way [m]
SIDE_ORDER = ("biking", "parking", "shoulder", "curb", "sidewalk", "border")
WIDTH_EPS = 1e-6  # [m] narrower side lanes are absent
CORNER_REACH = 0.85  # corner side lanes reach at most this share of the way to the turn's centre of curvature
CORNER_SAMPLES = 17  # stations along a corner road where its lane widths are checked and fitted


def corner_roads(junction_id: int, ends: list[RoadEnd], first_road_id: int,
                 plane: Optional[JunctionPlane] = None, movements: tuple[Movement, ...] = ()
                 ) -> tuple[list[xodr.Road], list[xodr.Connection], list[CornerRoad]]:
    """Corner roads between neighbouring arms (counter-clockwise, at most pi apart)
    that both have side lanes at the junction; their roads, connections and records.
    movements: the junction's, to tell which corners close the junction surface."""
    driven = {(m.from_road, m.from_lane, m.to_road, m.to_lane) for m in movements}
    ordered = sorted((e for e in ends if e.cross), key=lambda e: (e.heading_out % (2 * math.pi), e.edge_id))
    roads: list[xodr.Road] = []
    connections: list[xodr.Connection] = []
    records: list[CornerRoad] = []
    if len(ordered) < 2:
        return roads, connections, records
    for i, a in enumerate(ordered):
        b = ordered[(i + 1) % len(ordered)]
        theta = (b.heading_out - a.heading_out) % (2 * math.pi)
        if not 1e-6 < theta <= math.pi + 1e-6:
            continue
        a_side = _side_lanes(a, 1 if a.contact == "start" else -1)  # left of a's outward direction
        b_side = _side_lanes(b, -1 if b.contact == "start" else 1)  # right of b's outward direction
        if not a_side or not b_side:
            continue
        road_id = first_road_id + len(roads)
        road, kinds, kind_path, links = _corner_road(junction_id, road_id, a, b, a_side, b_side, plane)
        roads.append(road)
        conn = xodr.Connection(a.road_id, road_id, xodr.ContactPoint.start)
        for from_lane, to_lane in links:
            conn.add_lanelink(from_lane, to_lane)
        connections.append(conn)
        closes = bool(a.in_lanes and b.out_lanes) and (
            a.road_id, a.in_lanes[-1], b.road_id, b.out_lanes[-1]) in driven
        records.append(CornerRoad(junction_id, road_id, a.road_id, b.road_id, kinds, kind_path, closes))
    return roads, connections, records


def _side_lanes(end: RoadEnd, sign: int) -> list[tuple[int, str, float, float, float]]:
    """Side lanes (not driving, not median, wider than 0) on one side of a road end, outward."""
    lanes = [x for x in end.cross if (x[0] > 0) == (sign > 0) and x[1] not in ("driving", "median", "pocket")
             and abs(x[3] - x[2]) > WIDTH_EPS]
    return sorted(lanes, key=lambda x: abs(x[0]))


def _corner_road(junction_id: int, road_id: int, a: RoadEnd, b: RoadEnd, a_side: list, b_side: list,
                 plane: Optional[JunctionPlane]) -> tuple[xodr.Road, tuple[str, ...], PathKind, list]:
    from roadgen.opendrive.lanes import LANE_TYPES

    if a.in_lanes and b.out_lanes:  # along the right turn, from its lane's outer edge
        start = a.lane_pose(a.in_lanes[-1], incoming=True)
        end = b.lane_pose(b.out_lanes[-1], incoming=False)
        w0, w1 = a.lane_width, b.lane_width
    else:  # between the carriageway edges
        start = Pose(*offset_point(a.pose, a_side[0][2]), a.heading_in)
        end = Pose(*offset_point(b.pose, b_side[0][2]), b.heading_out)
        w0 = w1 = 0.0
    path = turn_path(start, end)
    planview = xodr.PlanView()
    for geometry, pose in path.geometries:
        planview.add_fixed_geometry(geometry, pose.x, pose.y, pose.hdg)
    length = path.length

    by_kind_a = {kind: (lid, abs(t1 - t0), h) for lid, kind, t0, t1, h in a_side}
    by_kind_b = {kind: (lid, abs(t1 - t0), h) for lid, kind, t0, t1, h in b_side}
    kinds = tuple(k for k in SIDE_ORDER if k in by_kind_a or k in by_kind_b)
    blends = [_smoothstep(by_kind_a.get(kind, (0, 0.0, 0.0))[1], by_kind_b.get(kind, (0, 0.0, 0.0))[1], length)
              for kind in kinds]
    outer = _fit_to_curvature(path, blends, _smoothstep(-w0 / 2, -w1 / 2, length))
    section = xodr.LaneSection(0, xodr.Lane())
    links = []
    for k, kind in enumerate(kinds, 1):
        records = outer if k == len(kinds) and outer is not None else [(0.0, blends[k - 1])]
        lane = xodr.Lane(LANE_TYPES[kind], *records[0][1])
        for soffset, coeffs in records[1:]:
            lane.add_lane_width(*coeffs, soffset=soffset)
        height = max(by_kind_a.get(kind, (0, 0.0, 0.0))[2], by_kind_b.get(kind, (0, 0.0, 0.0))[2])
        if height > 0:
            lane.add_height(inner=height, outer=height)
        if kind in by_kind_a:
            lane.add_link("predecessor", by_kind_a[kind][0])
            links.append((by_kind_a[kind][0], -k))
        if kind in by_kind_b:
            lane.add_link("successor", by_kind_b[kind][0])
        section.add_right_lane(lane)
    lanes = xodr.Lanes()
    lanes.add_lanesection(section)
    lanes.add_laneoffset(xodr.LaneOffset(0, *_smoothstep(-w0 / 2, -w1 / 2, length)))
    road = xodr.Road(road_id, planview, lanes, road_type=junction_id,
                     name=f"j{junction_id}_corner_{a.road_id}_{b.road_id}")
    road.add_predecessor(xodr.ElementType.road, a.road_id, _CONTACT[a.contact])
    road.add_successor(xodr.ElementType.road, b.road_id, _CONTACT[b.contact])
    if plane is not None:
        elevation, superelevation = path_profiles(path.point, path.length, path.breaks, plane)
        for rec in elevation:
            road.add_elevation(*rec)
        for rec in superelevation:
            road.add_superelevation(*rec)
    return road, kinds, path.kind, links


def _cubic(c: tuple[float, float, float, float], s: float) -> float:
    return c[0] + c[1] * s + c[2] * s * s + c[3] * s ** 3


def _fit_to_curvature(path: TurnPath, blends: list[tuple[float, float, float, float]],
                      offset: tuple[float, float, float, float]
                      ) -> Optional[list[tuple[float, tuple[float, float, float, float]]]]:
    """Width records (sOffset, (a, b, c, d)) of the outermost corner lane, narrowed where
    the lanes would reach past CORNER_REACH of the way to the turn's centre of curvature
    (the side lanes lie to the right, inside a right turn); None where they never do.
    A cubic Hermite chain through CORNER_SAMPLES stations, flat at both ends."""
    ss = np.linspace(0.0, path.length, CORNER_SAMPLES)
    inner = np.array([sum(_cubic(c, s) for c in blends[:-1]) for s in ss])
    own = np.array([_cubic(blends[-1], s) for s in ss])
    room = []
    for s in ss:
        kappa = path.point(float(s))[3]
        t_in = -_cubic(offset, s)  # distance of the side lanes' inner edge to the right of the path
        room.append(np.inf if kappa >= -1e-9 else CORNER_REACH / -kappa - t_in)
    target = np.minimum(own, np.maximum(np.array(room) - inner, 0.0))
    if np.all(target >= own - 1e-9):
        return None
    slopes = np.gradient(target, ss)
    slopes[0] = slopes[-1] = 0.0
    out = []
    for k in range(len(ss) - 1):
        h = ss[k + 1] - ss[k]
        y0, y1, m0, m1 = target[k], target[k + 1], slopes[k], slopes[k + 1]
        if y0 == y1 or (m0 == 0.0 and m1 == 0.0):  # no overshoot on flat or two flat ends
            m0 = m1 = 0.0 if y0 == y1 else m0
        else:  # keep the piece within its two values (Fritsch-Carlson limit)
            limit = 3.0 * abs(y1 - y0) / h
            m0, m1 = (float(np.clip(m, -limit, limit)) for m in (m0, m1))
        c = (3 * (y1 - y0) / h - 2 * m0 - m1) / h
        d = (m0 + m1 - 2 * (y1 - y0) / h) / h ** 2
        out.append((float(ss[k]), (float(y0), float(m0), float(c), float(d))))
    return out


def _smoothstep(v0: float, v1: float, length: float) -> tuple[float, float, float, float]:
    """(a, b, c, d) of a cubic from v0 to v1 over length with zero slope at both ends, so
    widths and offsets that change around a corner keep the lanes' headings at the joins."""
    dv = v1 - v0
    return v0, 0.0, 3 * dv / length ** 2, -2 * dv / length ** 3


def _connecting_road(m: Movement, a: RoadEnd, b: RoadEnd,
                     plane: Optional[JunctionPlane] = None) -> tuple[xodr.Road, TurnPath]:
    path = turn_path(a.lane_pose(m.from_lane, incoming=True), b.lane_pose(m.to_lane, incoming=False))
    planview = xodr.PlanView()
    for geometry, pose in path.geometries:
        planview.add_fixed_geometry(geometry, pose.x, pose.y, pose.hdg)
    length = path.length

    w0, w1 = a.lane_width, b.lane_width
    dw = (w1 - w0) / length
    center = xodr.Lane()
    section = xodr.LaneSection(0, center)
    lane = xodr.Lane(xodr.LaneType.driving, a=w0, b=dw)
    lane.add_link("predecessor", m.from_lane)
    lane.add_link("successor", m.to_lane)
    section.add_right_lane(lane)
    lanes = xodr.Lanes()
    lanes.add_lanesection(section)
    lanes.add_laneoffset(xodr.LaneOffset(0, w0 / 2, dw / 2))

    road = xodr.Road(
        m.connecting_road,
        planview,
        lanes,
        road_type=m.junction_id,
        name=f"j{m.junction_id}_{m.kind}_{m.from_road}_{m.from_lane}_{m.to_road}_{m.to_lane}",
    )
    road.add_predecessor(xodr.ElementType.road, m.from_road, _CONTACT[a.contact])
    road.add_successor(xodr.ElementType.road, m.to_road, _CONTACT[b.contact])
    road.add_type(ROAD_TYPE, speed=turn_speed(path, min(a.speed_limit, b.speed_limit)), speed_unit="m/s")
    if plane is not None:
        elevation, superelevation = path_profiles(path.point, path.length, path.breaks, plane)
        for rec in elevation:
            road.add_elevation(*rec)
        for rec in superelevation:
            road.add_superelevation(*rec)
    return road, path


POLY3_SAMPLES = 65  # curvature samples of a paramPoly3 fallback


@dataclass(frozen=True)
class TurnPath:
    """A connecting road's reference line (its lane centre)."""

    geometries: list[tuple[xodr.geometry._BaseGeometry, Pose]]
    length: float
    max_curvature: float  # [1/m]
    kind: PathKind
    pieces: Optional[list[Piece]] = None  # line / clothoid paths
    poly: Optional[Poly3Curve] = None  # the paramPoly3 fallback, from geometries[0]'s pose

    @property
    def breaks(self) -> list[float]:
        """Station where each geometry starts."""
        if self.pieces is None:
            return [0.0]
        return list(np.cumsum([0.0] + [p.length for p in self.pieces[:-1]]))

    def point(self, s: float) -> tuple[float, float, float, float]:
        """(x, y, heading, curvature) at station s."""
        if self.pieces is not None:
            for piece in self.pieces:
                if s <= piece.length + 1e-9 or piece is self.pieces[-1]:
                    ds = min(max(s, 0.0), piece.length)
                    x, y = piece.xy(ds)
                    return float(x), float(y), float(piece.heading(ds)), float(piece.curvature(ds))
                s -= piece.length
        start = self.geometries[0][1]
        u, v = self.poly.u, self.poly.v
        p = min(max(s / self.length, 0.0), 1.0)
        pu, pv = (sum(c * p ** k for k, c in enumerate(w)) for w in (u, v))
        du, dv = (sum(k * c * p ** (k - 1) for k, c in enumerate(w) if k) for w in (u, v))
        ddu, ddv = 2 * u[2] + 6 * u[3] * p, 2 * v[2] + 6 * v[3] * p
        cos, sin = math.cos(start.hdg), math.sin(start.hdg)
        kappa = (du * ddv - dv * ddu) / math.hypot(du, dv) ** 3
        return (start.x + pu * cos - pv * sin, start.y + pu * sin + pv * cos,
                start.hdg + math.atan2(dv, du), kappa)


def piece_geometry(piece: Piece) -> xodr.geometry._BaseGeometry:
    """The OpenDRIVE geometry of a line / arc / spiral piece."""
    if piece.kind == "line":
        return xodr.Line(piece.length)
    if piece.kind == "arc":
        return xodr.Arc(piece.k0, length=piece.length)
    return xodr.Spiral(piece.k0, piece.k1, length=piece.length)


def turn_path(start: Pose, end: Pose) -> TurnPath:
    """Lane-centre path through a junction from start to end.

    A <line> when end lies straight ahead with the same heading. Otherwise
    three clothoids (G2 Hermite interpolation, roadgen.geometry.clothoid) with
    zero curvature at both ends, matching the straight road ends there, so
    curvature is continuous into and out of the junction. If the solver
    finds no good path, a cubic Hermite paramPoly3 (G1 only) is the fallback.
    """
    pose, length = segment_start((start.x, start.y), (end.x, end.y))
    if (
        abs(wrap_angle(end.hdg - start.hdg)) < 1e-9
        and abs(wrap_angle(pose.hdg - start.hdg)) < 1e-9
    ):
        return TurnPath([(xodr.Line(length), start)], length, 0.0, "line", [Piece(start, length)])
    pieces = g2_hermite(start, 0.0, end, 0.0)
    if pieces is not None:
        return TurnPath([(piece_geometry(p), p.start) for p in pieces], sum(p.length for p in pieces),
                        max(max(abs(p.k0), abs(p.k1)) for p in pieces), "clothoid", pieces)
    curve = hermite_poly3(start, end)
    geometry = xodr.ParamPoly3(*curve.u, *curve.v, prange="normalized", length=curve.length)
    return TurnPath([(geometry, start)], curve.length, _poly3_max_curvature(curve), "poly3", poly=curve)


def _poly3_max_curvature(curve) -> float:
    p = np.linspace(0.0, 1.0, POLY3_SAMPLES)
    u, v = curve.u, curve.v
    du, dv = u[1] + 2 * u[2] * p + 3 * u[3] * p * p, v[1] + 2 * v[2] * p + 3 * v[3] * p * p
    ddu, ddv = 2 * u[2] + 6 * u[3] * p, 2 * v[2] + 6 * v[3] * p
    return float(np.max(np.abs(du * ddv - dv * ddu) / np.hypot(du, dv) ** 3))


def turn_speed(path: TurnPath, limit: float, design: DesignParams = DesignParams()) -> float:
    """Posted speed of a connecting road [m/s]: the roads' limit, or the advisory
    speed of the turn's tightest radius where that is lower (flat junction)."""
    if path.max_curvature < 1e-9:
        return limit
    radius = 1.0 / path.max_curvature
    if design_lib.max_speed(radius, 0.0, design) >= limit:
        return limit
    return min(limit, design_lib.advisory_speed(radius, 0.0, design))
