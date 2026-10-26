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
lanes than its entry, one incoming lane feeds two (or more) outgoing lanes,
so every outgoing lane is fed: the outermost incoming lane by default
(JunctionParams.lane_split "outer"), or the innermost ("inner"), with the
other lanes shifted outward so that no two connecting roads of one approach
cross. "none" leaves the extra lanes to the turns from other approaches.

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
LaneSplit = Literal["outer", "inner", "none"]
LANE_SPLITS = ("outer", "inner", "none")

_D = {k: v for k, v in section(DEFAULTS, "opendrive").items() if k != "junction_margin"}


@dataclass(frozen=True)
class JunctionParams:
    lane_split: LaneSplit = _D["lane_split"]  # which incoming lane feeds a straight exit's extra lanes
    u_turns: bool = _D["u_turns"]  # U-turn movements where the road is wide enough
    u_turn_radius: float = _D["u_turn_radius"]  # [m] tightest U-turn: lane centres 2x this apart

    def __post_init__(self) -> None:
        if self.lane_split not in LANE_SPLITS:
            raise ValueError(f"unknown lane_split {self.lane_split!r}; available: {LANE_SPLITS}")
        if self.u_turn_radius <= 0:
            raise ValueError("u_turn_radius must be positive")

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
    path: PathKind = "line"  # connecting-road shape: line, clothoid, or poly3 (G1 fallback)


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


def lane_pairs(kind: MovementKind, in_lanes: list[int], out_lanes: list[int],
               lane_split: LaneSplit = "none") -> list[tuple[int, int]]:
    if not in_lanes or not out_lanes:
        return []
    if kind == "straight":
        return straight_pairs(in_lanes, out_lanes, lane_split)
    if kind == "right":
        return [(in_lanes[-1], out_lanes[-1])]
    return [(in_lanes[0], out_lanes[0])]


def straight_pairs(in_lanes: list[int], out_lanes: list[int],
                   lane_split: LaneSplit = "none") -> list[tuple[int, int]]:
    """Lane-to-lane straight movements, centre outward. With more outgoing than
    incoming lanes, lane_split says which incoming lane also feeds the extra
    ones: "outer" the outermost (it fans out to the outer lanes), "inner" the
    innermost (the others shift outward); "none" leaves them unfed."""
    pairs = list(zip(in_lanes, out_lanes))
    extra = len(out_lanes) - len(in_lanes)
    if extra <= 0 or lane_split == "none":
        return pairs
    if lane_split == "outer":
        return pairs + [(in_lanes[-1], lane) for lane in out_lanes[len(in_lanes):]]
    inner = [(in_lanes[0], lane) for lane in out_lanes[:extra + 1]]
    return inner + list(zip(in_lanes[1:], out_lanes[extra + 1:]))


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
        if (abs(lane) - 0.5 + abs(out) - 0.5) * a.lane_width >= 2 * radius - 1e-9:
            return lane, out
    return None


def plan_movements(junction_id: int, ends: list[RoadEnd], first_road_id: int,
                   params: JunctionParams = JunctionParams()) -> list[Movement]:
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
                pairs[idx] = lane_pairs(kind, a.in_lanes, b.out_lanes, params.lane_split)
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


def build_junction(
    junction_id: int, ends: list[RoadEnd], first_road_id: int, plane: Optional[JunctionPlane] = None,
    params: JunctionParams = JunctionParams(),
) -> tuple[xodr.Junction, list[xodr.Road], list[Movement]]:
    """Junction, connecting roads and movements; with plane, connecting roads lie on it
    (height, grade and superelevation)."""
    by_edge = {e.edge_id: e for e in ends}
    junction = xodr.Junction(f"junction_{junction_id}", junction_id)
    roads: list[xodr.Road] = []
    movements = plan_movements(junction_id, ends, first_road_id, params)
    built = []
    for m in movements:
        a, b = by_edge[m.from_edge], by_edge[m.to_edge]
        road, kind = _connecting_road(m, a, b, plane)
        roads.append(road)
        m = replace(m, path=kind)
        built.append(m)
        conn = xodr.Connection(m.from_road, m.connecting_road, xodr.ContactPoint.start)
        conn.add_lanelink(m.from_lane, -1)
        junction.add_connection(conn)
    return junction, roads, built


def _connecting_road(m: Movement, a: RoadEnd, b: RoadEnd,
                     plane: Optional[JunctionPlane] = None) -> tuple[xodr.Road, PathKind]:
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
    return road, path.kind


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
