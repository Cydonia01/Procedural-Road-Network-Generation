"""Signs and markings: pure placement (stage B5, B7).

From a junction's control (roadgen.opendrive.control), its road ends and a
road's speed profile, records of what stands or is painted where: road,
station s, offset t, orientation, catalogue code. The builder turns them into
OpenDRIVE <signal> and <object> elements and numbers them.

Crosswalks (B7): at the junction end of a road with sidewalks on both sides
there, CROSSWALK_GAP before the road end, from curb to curb, 3 m deep on
streets and 4 m on avenues and highways (CROSSWALK_DEPTH). At lights a
Fussgaengerfurt on every arm: two solid lines 0.3 m wide along the
crosswalk's edges, no sign. Without lights a zebra (Z 293: 0.5 m stripes and
gaps) with sign 350-10 facing each direction of travel, only where
JunctionControlParams.zebra allows it (by default avenue arms in the urban
profile). Skipped where the road has too little room (CROSSWALK_ROOM), and
at junctions without control.

Per approach (a road end with incoming lanes), by its role:
- stop: a stop line (roadMark object "stopLine": a solid white marking
  0.5 m deep across the approach lanes) and sign 206;
- yield: a yield line (Z 341, "yieldLine": 0.5 m dashes, 0.25 m gaps,
  0.3 m deep) and sign 205; at roundabout entries also 215;
- signal: a stop line and a traffic light (OpenDRIVE 1000001, dynamic) on
  the right roadside at it, plus one over the middle of the approach lanes
  (OVERHEAD_Z) where the approach has overhead_min_lanes or more;
- major: sign 301 (priority at the next junction), PRIORITY_SIGN_DISTANCE
  before the road end (at most half the road), except on roundabout rings
  (the builder replaces it with 306 / 307 along runs of priority roads).
The lines stand LINE_SETBACK before the road end, or STOP_GAP behind a
crosswalk. Right before left and uncontrolled approaches get nothing.

Along a road (B7), per direction of travel, except on roundabout rings and
cul-de-sac loops:
- speed limit sign 274 (value in km/h, rounded down to 10) where the posted
  speed changes, and SPEED_AFTER_JUNCTION after each junction on roads
  longer than SPEED_SIGN_MIN_ROAD; an advisory corner speed counts only if it
  is at least 10 km/h below the limit (else the limit stays posted);
- curve warning 103-10 / 103-20 (left / right) before every advisory corner,
  or 105-10 / 105-20 (double curve, first left / right) where the next
  advisory corner starts within DOUBLE_CURVE of its end; the advisory
  deceleration lead before the corner (at least WARNING_MIN_LEAD), on the
  post above a 274 standing there.

Lines and crosswalks are objects with an open outline across the road and
<marking>s along it (the encoding esmini draws as paint, B0). Signs stand
SIGN_OFFSET outside the carriageway edge (driving, parking, bike and shoulder
lanes) at their station, facing their traffic, with a <validity> over its
lanes. German codes, country DE: subtype "-1" for codes without a DE
subtype; 103, 105, 350 with theirs; 274 with an empty subtype and its value
(esmini looks signs up as type[.subtype][-value], B0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from roadgen.opendrive.control import Control

LINE_SETBACK = 1.0  # stop and yield lines this far before the road end [m]
STOP_GAP = 1.0  # ... or this far behind a crosswalk [m]
CROSSWALK_GAP = 0.5  # crosswalks this far before the road end [m]
CROSSWALK_DEPTH = {"street": 3.0, "avenue": 4.0, "highway": 4.0}  # along the road [m]
ZEBRA = 0.5  # stripes and gaps of a zebra (Z 293) [m]
FURT_LINE = 0.3  # the two edge lines of a crossing at lights [m]
CROSSWALK_ROOM = 2.0  # a crosswalk needs this much road beyond it and its stop line [m]
SIGN_OFFSET = 0.5  # signs this far outside the carriageway edge [m]
PRIORITY_SIGN_DISTANCE = 30.0  # sign 301 this far before the junction (towns) [m]
SIGN_SIZE = 0.6  # sign face [m]
LIGHT_Z, OVERHEAD_Z = 3.0, 5.5  # light heads above the road: roadside and over the lanes [m]
LIGHT_SIZE = (0.8, 0.3)  # light head height and width [m]
LIGHT_TYPE = "1000001"  # OpenDRIVE three-lamp vehicle light (country "OpenDRIVE")
STACK = 0.7  # a second sign on the same post sits this much higher [m]
CARRIAGEWAY = frozenset({"driving", "parking", "biking", "shoulder", "median"})
SPEED_AFTER_JUNCTION = 10.0  # speed sign this far after a junction [m]
SPEED_SIGN_MIN_ROAD = 100.0  # ... on roads at least this long [m]
SPEED_SIGN_SPACING = 20.0  # no speed sign after a junction this close to a change sign [m]
ADVISORY_SIGN_DROP = 10  # an advisory speed is posted only this far below the limit [km/h]
DOUBLE_CURVE = 200.0  # two advisory corners this close get one double-curve sign [m]
WARNING_MIN_LEAD = 10.0  # curve warnings at least this far before the corner [m]
END_KEEP = 1.0  # along-road signs stay this far from the road ends [m]


@dataclass(frozen=True)
class SignalRecord:
    road: int
    s: float  # [m]
    t: float  # [m]
    orientation: str  # "+": for traffic along +s, "-": against it
    type: str  # DE catalogue code
    validity: tuple[int, int]  # lanes it applies to (from, to)
    junction: int  # -1: along the road
    z: float = 2.0  # sign centre above the road [m]
    country: str = "DE"
    subtype: str = "-1"
    dynamic: bool = False  # a light
    value: Optional[int] = None  # speed limit [km/h]

    @property
    def contact(self) -> str:
        """The road end the signal serves: "+" faces traffic towards the road's end."""
        return "end" if self.orientation == "+" else "start"


@dataclass(frozen=True)
class ObjectRecord:
    road: int
    s: float  # [m]
    t: float  # [m], the object's centre
    name: str  # stopLine | yieldLine | crosswalk
    half_span: float  # it reaches half_span either side of t [m]
    depth: float  # along the road [m]
    line: float  # dash length [m]; 0 = solid
    space: float  # gap [m]
    validity: Optional[tuple[int, int]]
    junction: int
    type: str = "roadMark"  # OpenDRIVE object type: roadMark | crosswalk
    edges: bool = False  # crosswalk at lights: two edge lines instead of zebra stripes


@dataclass(frozen=True)
class EndGeometry:
    """What placement needs of a road end: its station, travel sign and lane edges."""

    road: int
    contact: str  # start | end
    s_end: float  # station of the contact point [m]
    length: float  # road length [m]
    in_lanes: tuple[int, ...]  # incoming driving lanes, centre outward
    lane_edges: dict[int, tuple[float, float]]  # incoming driving lane -> (inner t, outer t)
    carriageway_edge: Callable[[float], float]  # s -> |t| of the carriageway's outer edge, incoming side [m]
    setback: float = LINE_SETBACK  # stop and yield lines this far before the road end [m]

    @property
    def sign(self) -> int:
        """+1 where incoming traffic drives along +s (contact end), -1 against it."""
        return 1 if self.contact == "end" else -1

    def back(self, distance: float) -> float:
        """Station distance [m] before the road end, kept on the road."""
        s = self.s_end - self.sign * min(distance, self.length)
        return min(max(s, 0.0), self.length)


def carriageway_edge(lanes_at: Callable[[float], list], side: int) -> Callable[[float], float]:
    """s -> |t| of the carriageway's outer edge on one side (+1 left, -1 right) [m]."""
    def edge(s: float) -> float:
        return max((abs(t1) for lid, kind, t0, t1, _ in lanes_at(s)
                    if (lid > 0) == (side > 0) and kind in CARRIAGEWAY and abs(t1 - t0) > 1e-9), default=0.0)
    return edge


def end_geometry(end, length: float, lanes_at: Callable[[float], list],
                 setback: float = LINE_SETBACK) -> Optional[EndGeometry]:
    """EndGeometry of a builder RoadEnd (roadgen.opendrive.junctions), or None without
    incoming lanes; lanes_at: the road's LaneLayout.lanes_at, (id, kind, inner t, outer t,
    height) of every lane at a station."""
    lanes = tuple(end.in_lanes)
    if not lanes:
        return None
    side = 1 if lanes[0] > 0 else -1
    if end.cross:
        edges = {lid: (t0, t1) for lid, kind, t0, t1, _ in end.cross if lid in lanes}
    else:
        half = end.lane_width / 2
        edges = {lid: (end.lane_offset(lid) - side * half, end.lane_offset(lid) + side * half) for lid in lanes}
    return EndGeometry(end.road_id, end.contact, 0.0 if end.contact == "start" else length, length, lanes,
                       edges, carriageway_edge(lanes_at, side), setback)


@dataclass(frozen=True)
class Crosswalk:
    """A crosswalk at a road end: from curb to curb, depth along the road."""

    road: int
    contact: str
    s: float  # centre station [m]
    t_lo: float  # right curb [m]
    t_hi: float  # left curb [m]
    depth: float  # [m]

    @property
    def setback(self) -> float:
        """Stop and yield lines this far before the road end [m]: behind the crosswalk."""
        return CROSSWALK_GAP + self.depth + STOP_GAP + 0.25

    @property
    def length(self) -> float:
        """Curb to curb [m]."""
        return self.t_hi - self.t_lo


def crosswalk_at(end, length: float, road_class: str, room: float) -> Optional[Crosswalk]:
    """The crosswalk at a builder RoadEnd, or None: it needs sidewalks on both sides at the
    end and room [m] of road (the whole road, or half of it where both ends are junctions)
    for itself, its stop line and CROSSWALK_ROOM."""
    if not end.cross:
        return None
    depth = CROSSWALK_DEPTH[road_class]
    sides = {}
    for sign in (-1, 1):
        lanes = [x for x in end.cross if (x[0] > 0) == (sign > 0) and abs(x[3] - x[2]) > 1e-9]
        if not any(kind == "sidewalk" for _, kind, _, _, _ in lanes):
            return None
        curbs = [t0 for _, kind, t0, _, _ in lanes if kind not in CARRIAGEWAY]
        sides[sign] = max(curbs) if sign < 0 else min(curbs)
    if room < CROSSWALK_GAP + depth + STOP_GAP + 0.5 + CROSSWALK_ROOM:
        return None
    s_end = 0.0 if end.contact == "start" else length
    sign = 1 if end.contact == "end" else -1
    return Crosswalk(end.road_id, end.contact, s_end - sign * (CROSSWALK_GAP + depth / 2), sides[-1], sides[1], depth)


def crosswalk_furniture(junction_id: int, cw: Crosswalk, lights: bool, directions: tuple[int, ...],
                        edge: Callable[[int], Callable[[float], float]],
                        validity: Callable[[int], Optional[tuple[int, int]]]
                        ) -> tuple[list[SignalRecord], list[ObjectRecord]]:
    """The crosswalk object, and a 350-10 facing each direction of travel at a zebra;
    directions: +1 / -1 where the road has traffic along / against s; edge(side): the
    carriageway edge of a side; validity(direction): its lanes."""
    t = (cw.t_lo + cw.t_hi) / 2
    if lights:
        obj = ObjectRecord(cw.road, cw.s, t, "crosswalk", cw.length / 2, cw.depth, 0.0, 0.0, None, junction_id,
                           "crosswalk", edges=True)
        return [], [obj]
    obj = ObjectRecord(cw.road, cw.s, t, "crosswalk", cw.length / 2, cw.depth, ZEBRA, ZEBRA, None, junction_id,
                       "crosswalk")
    signals = []
    for d in directions:
        side = -d  # traffic along +s keeps right: negative t
        lanes = validity(d)
        if lanes is None:
            continue
        signals.append(SignalRecord(cw.road, cw.s, side * (edge(side)(cw.s) + SIGN_OFFSET), "+" if d > 0 else "-",
                                    "350", lanes, junction_id, subtype="10"))
    return signals, [obj]


def approach_furniture(junction_id: int, control: Control, geom: EndGeometry, roundabout: bool,
                       overhead_min_lanes: Optional[int] = None) -> tuple[list[SignalRecord], list[ObjectRecord]]:
    """Signs, lights and lines of one approach (see the module docstring); overhead_min_lanes:
    the lane count from which an approach also gets a light over its lanes (None: never)."""
    role = control.role((geom.road, geom.contact))
    signals: list[SignalRecord] = []
    objects: list[ObjectRecord] = []
    validity = (min(geom.in_lanes, key=abs), max(geom.in_lanes, key=abs))
    orientation = "+" if geom.sign > 0 else "-"
    side = 1 if geom.in_lanes[0] > 0 else -1

    def sign(code: str, s: float, z: float = 2.0) -> None:
        roadside = side * (geom.carriageway_edge(s) + SIGN_OFFSET)
        signals.append(SignalRecord(geom.road, s, roadside, orientation, code, validity, junction_id, z))

    s_line = geom.back(geom.setback)
    if role in ("stop", "yield", "signal"):
        ts = [t for edge in geom.lane_edges.values() for t in edge]
        lo, hi = min(ts), max(ts)
        if role == "yield":
            line = ObjectRecord(geom.road, s_line, (lo + hi) / 2, "yieldLine", (hi - lo) / 2, 0.3, 0.5, 0.25,
                                validity, junction_id)
        else:
            line = ObjectRecord(geom.road, s_line, (lo + hi) / 2, "stopLine", (hi - lo) / 2, 0.5, 0.0, 0.0,
                                validity, junction_id)
        objects.append(line)
    if role == "stop":
        sign("206", s_line)
    elif role == "yield":
        sign("205", s_line)
        if roundabout:
            sign("215", s_line, 2.0 + STACK)  # above the 205, on its post
    elif role == "signal":
        signals.append(SignalRecord(geom.road, s_line, side * (geom.carriageway_edge(s_line) + SIGN_OFFSET),
                                    orientation, LIGHT_TYPE, validity, junction_id, LIGHT_Z, "OpenDRIVE",
                                    dynamic=True))
        if overhead_min_lanes is not None and len(geom.in_lanes) >= overhead_min_lanes:
            signals.append(SignalRecord(geom.road, s_line, line.t, orientation, LIGHT_TYPE, validity, junction_id,
                                        OVERHEAD_Z, "OpenDRIVE", dynamic=True))
    elif role == "major" and not roundabout:
        sign("301", geom.back(min(PRIORITY_SIGN_DISTANCE, geom.length / 2)))
    return signals, objects


# --- along the road ----------------------------------------------------------------


def kmh(speed: float) -> int:
    """A speed [m/s] as a posted limit [km/h]: to the nearest km/h (limits are stored
    rounded, 22.2 m/s for 80 km/h), then down to 10."""
    return math.floor(round(speed * 3.6) / 10.0) * 10


@dataclass(frozen=True)
class RoadSide:
    """One direction of travel on a road: what along-road signs need."""

    road: int
    direction: int  # +1 along s, -1 against it
    length: float  # [m]
    lanes_at: Callable[[float], list]  # the road's LaneLayout.lanes_at
    from_junction: bool  # traffic enters the road from a junction

    def place(self, code: str, s: float, z: float = 2.0, subtype: str = "-1",
              value: Optional[int] = None) -> SignalRecord:
        """A sign at s (kept END_KEEP from the road ends) on the right of this direction,
        valid for its driving lanes there."""
        s = min(max(s, END_KEEP), self.length - END_KEEP)
        side = -self.direction
        ids = [lid for lid, kind, *_ in self.lanes_at(s) if kind == "driving" and (lid > 0) == (side > 0)]
        validity = (min(ids, key=abs), max(ids, key=abs))
        return SignalRecord(self.road, s, side * (carriageway_edge(self.lanes_at, side)(s) + SIGN_OFFSET),
                            "+" if self.direction > 0 else "-", code, validity, -1, z, subtype=subtype, value=value)


def speed_signs(side: RoadSide, profile: list[tuple[float, float]], limit: Callable[[float], float]
                ) -> list[SignalRecord]:
    """Sign 274 where the posted speed changes along side's direction, and after the
    junction it starts from on long roads (see the module docstring); profile: the road's
    (station, speed [m/s]) records, limit(s): the edges' speed limit [m/s]."""
    bounds = [s for s, _ in profile] + [side.length]
    segments = [(bounds[k], bounds[k + 1], v) for k, (_, v) in enumerate(profile)]
    if side.direction < 0:
        segments = segments[::-1]

    def posted(seg: tuple[float, float, float]) -> int:
        a, b, v = seg
        lim = limit((a + b) / 2)
        return kmh(v) if (lim - v) * 3.6 >= ADVISORY_SIGN_DROP - 1e-6 else kmh(lim)

    out = []
    current = posted(segments[0])
    for seg in segments[1:]:
        value = posted(seg)
        if value != current:
            at = seg[0] if side.direction > 0 else seg[1]
            out.append(side.place("274", at, subtype="", value=value))
            current = value
    if side.from_junction and side.length >= SPEED_SIGN_MIN_ROAD:
        at = SPEED_AFTER_JUNCTION if side.direction > 0 else side.length - SPEED_AFTER_JUNCTION
        if all(abs(r.s - at) > SPEED_SIGN_SPACING for r in out):
            out.insert(0, side.place("274", at, subtype="", value=posted(segments[0])))
    return out


def curve_warnings(side: RoadSide, corners: list[tuple[float, float, float, float]],
                   limit: Callable[[float], float], decel: float) -> list[SignalRecord]:
    """103-10 / 103-20 (or 105-10 / 105-20 for two corners within DOUBLE_CURVE) before
    every advisory corner, in side's direction; corners: (s_start, s_end, deflection
    [rad, + left along s], advisory speed [m/s])."""
    ordered = sorted(corners, key=lambda c: c[0] * side.direction)
    out = []
    skip = False
    for k, (s0, s1, deflection, advisory) in enumerate(ordered):
        if skip:
            skip = False
            continue
        entry, exit_ = (s0, s1) if side.direction > 0 else (s1, s0)
        v = limit(entry)
        lead = max((v * v - advisory * advisory) / (2 * decel), WARNING_MIN_LEAD)
        left = (deflection > 0) == (side.direction > 0)
        code, subtype = "103", "10" if left else "20"
        if k + 1 < len(ordered):
            nxt = ordered[k + 1]
            next_entry = nxt[0] if side.direction > 0 else nxt[1]
            if abs(next_entry - exit_) <= DOUBLE_CURVE:
                code, skip = "105", True
        out.append(side.place(code, entry - side.direction * lead, 2.0 + STACK, subtype))
    return out
