"""Road marks of the HD maps (stage B4): the German catalogue (StVO, RMS), all white.

Each lane's mark lies on its outer border; the centre lane's mark is the
centre line. Along a road the marks change with the road class of the edge
under them and near junctions, so a lane can have several <roadMark>
records in one lane section (sOffset).

- Centre line of a two-way road: broken (Z 340: line / gap 3 / 6 m on
  streets and avenues, 6 / 12 m on highways); solid (Z 295) on roads with
  solid_centre_lanes or more lanes each way. Where the road has a median
  lane the median's own edge lines replace it, and where the median is 0 m
  wide (it tapers out before junctions) the centre line is solid solid.
  Streets in the profiles no_street_centre (urban and suburban: German
  residential practice) have none. A one-way road's centre lane is its left
  edge: solid.
- Lane lines between driving lanes: broken, solid with laneChange="none"
  over the last no_change_length of each approach to a junction of three or
  more roads (the no-lane-change zone, also on the centre line).
- Edge line: solid on the outermost driving lane.

Connecting roads have no marks (junction interiors are unmarked).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Union

from scenariogeneration import xodr

from roadgen.config import DEFAULTS, section
from roadgen.geometry.alignment import ChainAlignment
from roadgen.geometry.cross_section import CLASSES, PROFILES
from roadgen.geometry.profiles import STATION_EPS, LaneSection, WidthPiece, WidthPieces
from roadgen.graph.model import RoadGraph

_D = section(DEFAULTS, "road_marks")

DOUBLE_LINE_LENGTH = 10.0  # a solid line written as <line> with no space [m]
APPROACH_MIN_ROADS = 3  # junctions with at least this many roads get no-lane-change zones
Spec = tuple  # a mark: ("broken", width, line, gap) | ("solid", width) | ("solid solid", width) | ("none",)


@dataclass(frozen=True)
class RoadMarkParams:
    width: tuple[float, float, float] = _D["width"]  # line width, street, avenue, highway [m]
    broken_town: tuple[float, float] = _D["broken_town"]  # broken line on streets and avenues: line, gap [m]
    broken_highway: tuple[float, float] = _D["broken_highway"]  # ... on highways [m]
    no_change_length: tuple[float, float, float] = _D["no_change_length"]  # solid before junctions [m]
    solid_centre_lanes: int = _D["solid_centre_lanes"]  # lanes each way from which the centre line is solid
    no_street_centre: tuple[str, ...] = _D["no_street_centre"]  # cross-section profiles whose streets have none

    def __post_init__(self) -> None:
        if len(self.width) != 3 or min(self.width) <= 0:
            raise ValueError("road_marks.width needs 3 widths > 0 (street, avenue, highway)")
        for name in ("broken_town", "broken_highway"):
            if len(getattr(self, name)) != 2 or min(getattr(self, name)) <= 0:
                raise ValueError(f"road_marks.{name} needs a line and a gap length > 0")
        if len(self.no_change_length) != 3 or min(self.no_change_length) < 0:
            raise ValueError("road_marks.no_change_length needs 3 lengths >= 0 (street, avenue, highway)")
        if self.solid_centre_lanes < 1:
            raise ValueError("road_marks.solid_centre_lanes must be >= 1")
        unknown = set(self.no_street_centre) - set(PROFILES)
        if unknown:
            raise ValueError(f"road_marks.no_street_centre: unknown profiles {sorted(unknown)}; available: {PROFILES}")


@dataclass(frozen=True)
class RoadMarks:
    """What a chain road's marks depend on along it."""

    params: RoadMarkParams
    classes: tuple[tuple[float, str, str], ...]  # (station where it starts, road class, profile), in s order
    right_zones: tuple[tuple[float, float], ...]  # no-lane-change zones of the right (forward) lanes
    left_zones: tuple[tuple[float, float], ...]  # ... of the left (backward) lanes

    def _at(self, s: float) -> tuple[float, str, str]:
        rec = self.classes[0]
        for r in self.classes:
            if r[0] <= s + STATION_EPS:
                rec = r
        return rec

    def road_class(self, s: float) -> str:
        return self._at(s)[1]

    def centre_line(self, s: float) -> bool:
        """Whether a two-way road has a centre line at s (not a street in a no_street_centre profile)."""
        _, cls, profile = self._at(s)
        return not (cls == "street" and profile in self.params.no_street_centre)

    def width(self, s: float) -> float:
        return float(self.params.width[CLASSES.index(self.road_class(s))])

    def broken(self, s: float) -> Spec:
        line, gap = self.params.broken_highway if self.road_class(s) == "highway" else self.params.broken_town
        return ("broken", self.width(s), float(line), float(gap))

    def cuts(self) -> set[float]:
        """Stations where a mark may change: class changes and zone ends."""
        return ({r[0] for r in self.classes} | {s for z in self.right_zones + self.left_zones for s in z})


def road_marks(graph: RoadGraph, ca: ChainAlignment, junctions: frozenset[int],
               params: RoadMarkParams, profile: str) -> RoadMarks:
    """The marks' inputs of a chain road: the class and cross-section profile of each
    edge's stretch (Edge.cross_section, else the default profile) and the no-lane-change
    zones before its junction ends (right lanes drive towards the end, left lanes towards
    the start)."""
    edges = [graph.edges[eid] for eid in ca.chain.edge_ids]
    classes = tuple((ca.edge_range(e.id)[0] if k else 0.0, e.road_class, e.cross_section or profile)
                    for k, e in enumerate(edges))

    def zone(node: int, cls: str, at_start: bool) -> tuple[tuple[float, float], ...]:
        if node not in junctions or graph.degree(node) < APPROACH_MIN_ROADS:
            return ()
        length = min(float(params.no_change_length[CLASSES.index(cls)]), ca.length)
        if length <= STATION_EPS:
            return ()
        return ((0.0, length),) if at_start else ((ca.length - length, ca.length),)

    return RoadMarks(params, classes, zone(ca.chain.end, edges[-1].road_class, False),
                     zone(ca.chain.start, edges[0].road_class, True))


def _in(zones: tuple[tuple[float, float], ...], s: float) -> bool:
    return any(a - STATION_EPS <= s <= b + STATION_EPS for a, b in zones)


def _wide(piece: Union[WidthPiece, WidthPieces], s: float) -> bool:
    return piece.width(s) > 1e-9


def _piece_cuts(piece: Union[WidthPiece, WidthPieces]) -> set[float]:
    pieces = piece.pieces if isinstance(piece, WidthPieces) else (piece,)
    return {p.s0 for p in pieces} | {p.s1 for p in pieces}


def _runs(s0: float, s1: float, cuts: set[float], spec_at) -> list[tuple[float, Spec]]:
    """(station, mark) wherever the mark changes over [s0, s1], from spec_at(s) at the
    middle of each stretch between cuts."""
    pts = [s0] + sorted(c for c in cuts if s0 + STATION_EPS < c < s1 - STATION_EPS) + [s1]
    out: list[tuple[float, Spec]] = []
    for a, b in pairwise(pts):
        spec = spec_at(0.5 * (a + b))
        if not out or out[-1][1] != spec:
            out.append((a, spec))
    return out


def centre_marks(rm: RoadMarks, sec: LaneSection) -> list[xodr.RoadMark]:
    """Marks of the centre lane over a lane section."""
    right, left = sec.roles("right"), sec.roles("left")
    n_right = sum(kind == "driving" for kind, _ in right)
    n_left = sum(kind == "driving" for kind, _ in left)
    median = sec.right[0] if right and right[0][0] == "median" else None
    zones = rm.right_zones + rm.left_zones
    cuts = rm.cuts() | (_piece_cuts(median) if median is not None else set())

    def spec(s: float) -> Spec:
        if n_left == 0:  # one-way road: its left edge
            return ("solid", rm.width(s))
        if median is not None:
            return ("none",) if _wide(median, s) else ("solid solid", rm.width(s))
        if not rm.centre_line(s):
            return ("none",)
        if min(n_right, n_left) >= rm.params.solid_centre_lanes or _in(zones, s):
            return ("solid", rm.width(s))
        return rm.broken(s)

    return _to_marks(_runs(sec.s0, sec.s1, cuts, spec), sec.s0)


def lane_marks(rm: RoadMarks, sec: LaneSection, side: str, position: int,
               piece: Union[WidthPiece, WidthPieces]) -> list[xodr.RoadMark]:
    """Marks on the outer border of the lane at position (1 = next to the centre) of one
    side of a lane section."""
    roles = sec.roles(side)
    kind = roles[position - 1][0]
    outer_driving = max((k for k, (kd, _) in enumerate(roles, 1) if kd == "driving"), default=0)
    zones = rm.right_zones if side == "right" else rm.left_zones
    if kind == "median":
        def spec(s: float) -> Spec:
            return ("solid", rm.width(s)) if _wide(piece, s) else ("none",)
        cuts = rm.cuts() | _piece_cuts(piece)
    elif kind == "pocket":  # a lane line to the through lanes, solid in the zone
        def spec(s: float) -> Spec:
            return ("solid", rm.width(s)) if _in(zones, s) else rm.broken(s)
        cuts = rm.cuts()
    elif kind == "driving" and position == outer_driving:
        def spec(s: float) -> Spec:
            return ("solid", rm.width(s))
        cuts = rm.cuts()
    elif kind == "driving":
        def spec(s: float) -> Spec:
            return ("solid", rm.width(s)) if _in(zones, s) else rm.broken(s)
        cuts = rm.cuts()
    else:
        raise ValueError(f"unknown lane kind {kind!r}")
    return _to_marks(_runs(sec.s0, sec.s1, cuts, spec), sec.s0)


def _to_marks(runs: list[tuple[float, Spec]], s0: float) -> list[xodr.RoadMark]:
    return [_mark(spec, round(s - s0, 6)) for s, spec in runs]


def _mark(spec: Spec, soffset: float) -> xodr.RoadMark:
    kind = spec[0]
    if kind == "none":
        return xodr.RoadMark(xodr.RoadMarkType.none, soffset=soffset)
    width = spec[1]
    if kind == "broken":
        mark = xodr.RoadMark(xodr.RoadMarkType.broken, width, soffset=soffset, color=xodr.RoadMarkColor.white,
                             laneChange=xodr.LaneChange.both)
        mark.add_specific_road_line(xodr.RoadLine(width, spec[2], spec[3], 0, 0))
        return mark
    if kind == "solid":
        return xodr.RoadMark(xodr.RoadMarkType.solid, width, soffset=soffset, color=xodr.RoadMarkColor.white,
                             laneChange=xodr.LaneChange.none)
    # Two explicit lines, a line width apart (<type>), so no reader has to guess the gap.
    mark = xodr.RoadMark(xodr.RoadMarkType.solid_solid, round(3 * width, 6), soffset=soffset,
                         color=xodr.RoadMarkColor.white, laneChange=xodr.LaneChange.none)
    for side in (-1, 1):
        mark.add_specific_road_line(xodr.RoadLine(width, DOUBLE_LINE_LENGTH, 0.0, side * width, 0))
    return mark
