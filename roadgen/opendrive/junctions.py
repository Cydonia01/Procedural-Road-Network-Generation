"""Junction construction: one single-lane connecting road per lane movement.

Conventions (right-hand traffic):
- A road whose reference line ends at the junction (contact "end") brings
  traffic in on its right lanes (-1, -2, ...) and takes it out on its left
  lanes (1, 2, ...). Contact "start" is the mirror image.
- Lane lists are ordered from the center line outward, i.e. from the driver's
  leftmost lane to the rightmost lane.
- A connecting road's reference line is the lane center path; its single
  lane -1 is centered on it with a laneOffset of half the lane width.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from scenariogeneration import xodr

from roadgen.opendrive.geometry import (
    Pose,
    hermite_poly3,
    offset_point,
    segment_start,
    wrap_angle,
)

Contact = Literal["start", "end"]
MovementKind = Literal["straight", "left", "right"]

# |heading change| below this counts as straight.
STRAIGHT_THRESHOLD = math.pi / 4
ROAD_TYPE = xodr.RoadType.town

_CONTACT = {"start": xodr.ContactPoint.start, "end": xodr.ContactPoint.end}


@dataclass(frozen=True)
class RoadEnd:
    """One normal road's end at a junction."""

    edge_id: int
    road_id: int
    contact: Contact
    pose: Pose  # reference line pose at the contact point, heading along +s
    right_lanes: int
    left_lanes: int
    lane_width: float
    speed_limit: float

    @property
    def in_lanes(self) -> list[int]:
        if self.contact == "end":
            return [-i for i in range(1, self.right_lanes + 1)]
        return list(range(1, self.left_lanes + 1))

    @property
    def out_lanes(self) -> list[int]:
        if self.contact == "end":
            return list(range(1, self.left_lanes + 1))
        return [-i for i in range(1, self.right_lanes + 1)]

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
        t = math.copysign((abs(lane_id) - 0.5) * self.lane_width, lane_id)
        x, y = offset_point(self.pose, t)
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


def classify(heading_in: float, heading_out: float) -> MovementKind:
    diff = wrap_angle(heading_out - heading_in)
    if abs(diff) < STRAIGHT_THRESHOLD:
        return "straight"
    return "left" if diff > 0 else "right"


def classify_exits(turns: dict[int, float]) -> dict[int, MovementKind]:
    """Kinds for all exits of one incoming road, given their turn angles.

    At most one exit is "straight": the one with the smallest turn, if within
    STRAIGHT_THRESHOLD. With five or more arms several exits can lie within
    the threshold; the others are left or right by the sign of their turn.
    """
    kinds: dict[int, MovementKind] = {k: ("left" if t > 0 else "right") for k, t in turns.items()}
    if turns:
        best = min(turns, key=lambda k: (abs(turns[k]), k))
        if abs(turns[best]) < STRAIGHT_THRESHOLD:
            kinds[best] = "straight"
    return kinds


def lane_pairs(kind: MovementKind, in_lanes: list[int], out_lanes: list[int]) -> list[tuple[int, int]]:
    if not in_lanes or not out_lanes:
        return []
    if kind == "straight":
        return list(zip(in_lanes, out_lanes))
    if kind == "right":
        return [(in_lanes[-1], out_lanes[-1])]
    return [(in_lanes[0], out_lanes[0])]


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


def plan_movements(junction_id: int, ends: list[RoadEnd], first_road_id: int) -> list[Movement]:
    """All lane movements through a junction, excluding U-turns, with road ids assigned.

    With exactly two arms the junction is just a bend or a lane-count change,
    so every lane continues regardless of the turn angle. With more arms the
    standard rules apply (right turns from the rightmost lane, left turns from
    the leftmost, straight lane-to-lane), and any incoming lane still without
    a movement is given one by fill_pair.
    """
    movements: list[Movement] = []
    continuation = len(ends) == 2
    for a in ends:
        pairs: dict[int, list[tuple[int, int]]] = {}
        kinds: dict[int, MovementKind] = {}
        options: dict[MovementKind, list[int]] = {}
        option_end: dict[MovementKind, int] = {}
        turns = {
            idx: wrap_angle(b.heading_out - a.heading_in)
            for idx, b in enumerate(ends)
            if a.edge_id != b.edge_id and b.out_lanes
        }
        exit_kinds = classify_exits(turns)
        for idx, b in enumerate(ends):
            if idx not in turns:
                continue
            kind = exit_kinds[idx]
            kinds[idx] = kind
            if continuation:
                pairs[idx] = continuation_pairs(a.in_lanes, b.out_lanes)
            else:
                pairs[idx] = lane_pairs(kind, a.in_lanes, b.out_lanes)
            if kind not in options:
                options[kind], option_end[kind] = b.out_lanes, idx

        covered = {lane for ps in pairs.values() for lane, _ in ps}
        for k, lane in enumerate(a.in_lanes):
            if lane in covered:
                continue
            fill = fill_pair(k, a.in_lanes, options)
            if fill is not None:
                kind, out_lane = fill
                pairs[option_end[kind]].append((lane, out_lane))

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


def build_junction(
    junction_id: int, ends: list[RoadEnd], first_road_id: int
) -> tuple[xodr.Junction, list[xodr.Road], list[Movement]]:
    by_edge = {e.edge_id: e for e in ends}
    junction = xodr.Junction(f"junction_{junction_id}", junction_id)
    roads: list[xodr.Road] = []
    movements = plan_movements(junction_id, ends, first_road_id)
    for m in movements:
        a, b = by_edge[m.from_edge], by_edge[m.to_edge]
        roads.append(_connecting_road(m, a, b))
        conn = xodr.Connection(m.from_road, m.connecting_road, xodr.ContactPoint.start)
        conn.add_lanelink(m.from_lane, -1)
        junction.add_connection(conn)
    return junction, roads, movements


def _connecting_road(m: Movement, a: RoadEnd, b: RoadEnd) -> xodr.Road:
    start = a.lane_pose(m.from_lane, incoming=True)
    end = b.lane_pose(m.to_lane, incoming=False)
    geometry, length = _path_geometry(start, end)
    planview = xodr.PlanView()
    planview.add_fixed_geometry(geometry, start.x, start.y, start.hdg)

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
    road.add_type(ROAD_TYPE, speed=min(a.speed_limit, b.speed_limit), speed_unit="m/s")
    return road


def _path_geometry(start: Pose, end: Pose) -> tuple[xodr.geometry._BaseGeometry, float]:
    """A <line> when end lies straight ahead with the same heading, else paramPoly3."""
    pose, length = segment_start((start.x, start.y), (end.x, end.y))
    if (
        abs(wrap_angle(end.hdg - start.hdg)) < 1e-9
        and abs(wrap_angle(pose.hdg - start.hdg)) < 1e-9
    ):
        return xodr.Line(length), length
    curve = hermite_poly3(start, end)
    return xodr.ParamPoly3(*curve.u, *curve.v, prange="normalized", length=curve.length), curve.length


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
    cross at distance (b + a cos theta) / sin theta along the first arm and
    (a + b cos theta) / sin theta along the second; each arm is trimmed past
    every such crossing, so acute junctions never overlap. Raises ValueError
    for arms closer than MIN_ARM_ANGLE_DEG.
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
            need[first.edge_id] = max(need[first.edge_id], (b + a * cos) / sin)
            need[second.edge_id] = max(need[second.edge_id], (a + b * cos) / sin)
    return {eid: d + margin for eid, d in need.items()}
