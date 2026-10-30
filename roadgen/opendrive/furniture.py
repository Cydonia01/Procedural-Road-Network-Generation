"""Signs and markings at junction approaches (stage B5): pure placement.

From a junction's control (roadgen.opendrive.control) and its road ends,
records of what stands or is painted where: road, station s, offset t,
orientation, catalogue code. The builder turns them into OpenDRIVE
<signal> and <object> elements and numbers them.

Per approach (a road end with incoming lanes), by its role:
- stop: a stop line (roadMark object "stopLine": a solid white marking
  0.5 m deep across the approach lanes, LINE_SETBACK before the road end)
  and sign 206;
- yield: a yield line (Z 341, "yieldLine": 0.5 m dashes, 0.25 m gaps,
  0.3 m deep) and sign 205; at roundabout entries also 215;
- signal: a stop line (the lights come with B6);
- major: sign 301 (priority at the next junction), PRIORITY_SIGN_DISTANCE
  before the road end (at most half the road), except on roundabout rings.
Right before left and uncontrolled approaches get nothing.

Lines are objects with an open outline of two corners from the inner to
the outer edge of the approach's driving lanes and one <marking> along it
(the encoding esmini draws as paint, B0). Signs stand SIGN_OFFSET outside
the carriageway edge (driving, parking, bike and shoulder lanes) at their
station, facing the
approaching traffic, with a <validity> over its lanes. German codes:
country DE, subtype "-1" (none of these has a DE subtype).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from roadgen.opendrive.control import Control

LINE_SETBACK = 1.0  # stop and yield lines this far before the road end [m]
SIGN_OFFSET = 0.5  # signs this far outside the carriageway edge [m]
PRIORITY_SIGN_DISTANCE = 30.0  # sign 301 this far before the junction (towns) [m]
SIGN_SIZE = 0.6  # sign face [m]
STACK = 0.7  # a second sign on the same post sits this much higher [m]
CARRIAGEWAY = frozenset({"driving", "parking", "biking", "shoulder", "median"})


@dataclass(frozen=True)
class SignalRecord:
    road: int
    s: float  # [m]
    t: float  # [m]
    orientation: str  # "+": for traffic along +s, "-": against it
    type: str  # DE catalogue code
    validity: tuple[int, int]  # lanes it applies to (from, to)
    junction: int
    z: float = 2.0  # sign centre above the road [m]
    country: str = "DE"
    subtype: str = "-1"


@dataclass(frozen=True)
class ObjectRecord:
    road: int
    s: float  # [m]
    t: float  # [m], the line's centre
    name: str  # stopLine | yieldLine
    half_span: float  # the line reaches half_span either side of t [m]
    depth: float  # along the road [m]
    line: float  # dash length [m]; 0 = solid
    space: float  # gap [m]
    validity: tuple[int, int]
    junction: int


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

    @property
    def sign(self) -> int:
        """+1 where incoming traffic drives along +s (contact end), -1 against it."""
        return 1 if self.contact == "end" else -1

    def back(self, distance: float) -> float:
        """Station distance [m] before the road end, kept on the road."""
        s = self.s_end - self.sign * min(distance, self.length)
        return min(max(s, 0.0), self.length)


def end_geometry(end, length: float, lanes_at: Callable[[float], list]) -> Optional[EndGeometry]:
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

    def carriageway_edge(s: float) -> float:
        return max((abs(t1) for lid, kind, t0, t1, _ in lanes_at(s)
                    if (lid > 0) == (side > 0) and kind in CARRIAGEWAY and abs(t1 - t0) > 1e-9), default=0.0)

    return EndGeometry(end.road_id, end.contact, 0.0 if end.contact == "start" else length, length, lanes,
                       edges, carriageway_edge)


def approach_furniture(junction_id: int, control: Control, geom: EndGeometry, roundabout: bool
                       ) -> tuple[list[SignalRecord], list[ObjectRecord]]:
    """Signs and lines of one approach (see the module docstring)."""
    role = control.role((geom.road, geom.contact))
    signals: list[SignalRecord] = []
    objects: list[ObjectRecord] = []
    validity = (min(geom.in_lanes, key=abs), max(geom.in_lanes, key=abs))
    orientation = "+" if geom.sign > 0 else "-"
    side = 1 if geom.in_lanes[0] > 0 else -1

    def sign(code: str, s: float, z: float = 2.0) -> None:
        roadside = side * (geom.carriageway_edge(s) + SIGN_OFFSET)
        signals.append(SignalRecord(geom.road, s, roadside, orientation, code, validity, junction_id, z))

    s_line = geom.back(LINE_SETBACK)
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
    elif role == "major" and not roundabout:
        sign("301", geom.back(min(PRIORITY_SIGN_DISTANCE, geom.length / 2)))
    return signals, objects
