"""Drive every junction movement with esmini's RoadManager library.

For each movement in the builder's sidecar, a position is placed in the
incoming lane 20 m (at most half the road, and never before the lane runs at
full width) before the junction and moved
forward in small steps with esmini's junction selector aimed at the movement,
through the connecting road and 20 m beyond.
A movement passes when esmini routes it over the expected connecting road
into the expected lane, the position never jumps, and esmini never reports
an error. Reaching the end of a dead-end target road just ends the drive.
Where one incoming lane splits into several lanes of the same road (a lane
split, stage C5), esmini's selector, which only knows the turn angle, can't
choose between them: any connecting road of that split, into its own lane,
counts.

This exercises the same lane-following logic odrviewer uses for its traffic.
"""

from __future__ import annotations

import ctypes
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from roadgen.opendrive.builder import sidecar_path
from roadgen.sim.esmini import esmini_library

PathLike = Union[str, Path]

APPROACH = 20.0  # m before the junction (at most half the incoming road)
STEP = 0.5  # m per move
DRIVE_ON = 20.0  # m driven on after the connecting road (less if the target road ends)
JUMP_TOL = 0.05  # m deviation from STEP that counts as a jump
END_OF_ROAD = -2  # esmini Position::ReturnCode::ERROR_END_OF_ROAD

# esmini's junctionSelectorAngle, measured counter-clockwise from straight
# ahead (verified empirically against esmini 3.9).
SELECTOR_ANGLE = {"straight": 0.0, "left": math.pi / 2, "right": 3 * math.pi / 2, "uturn": math.pi}


class _PositionData(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_double) for n in ("x", "y", "z", "h", "p", "r", "hRelative")
    ] + [
        ("roadId", ctypes.c_uint32),
        ("junctionId", ctypes.c_uint32),
        ("laneId", ctypes.c_int),
        ("laneOffset", ctypes.c_double),
        ("s", ctypes.c_double),
    ]


def _load_rm(path: Optional[PathLike] = None) -> ctypes.CDLL:
    lib = ctypes.CDLL(str(path or esmini_library("esminiRMLib")))
    lib.RM_Init.argtypes = [ctypes.c_char_p]
    lib.RM_SetLanePosition.argtypes = [
        ctypes.c_int, ctypes.c_uint32, ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_bool,
    ]
    lib.RM_PositionMoveForward.argtypes = [ctypes.c_int, ctypes.c_double, ctypes.c_double]
    lib.RM_GetPositionData.argtypes = [ctypes.c_int, ctypes.POINTER(_PositionData)]
    return lib


@dataclass
class DriveResult:
    movement: dict
    route: list[int]
    final_lane: Optional[int]  # lane on entering the target road, None if never reached
    max_jump: float
    min_return_code: int
    # (connecting road, to lane) of the movement and of the other branches of its lane split
    accepted: tuple[tuple[int, int], ...] = ()

    @property
    def ok(self) -> bool:
        m = self.movement
        accepted = self.accepted or ((m["connecting_road"], m["to_lane"]),)
        return (
            len(self.route) >= 3 and self.route[0] == m["from_road"] and self.route[2] == m["to_road"]
            and (self.route[1], self.final_lane) in accepted
            and self.max_jump < JUMP_TOL
            and self.min_return_code >= 0
        )

    def describe(self) -> str:
        m = self.movement
        return (
            f"{m['kind']} road {m['from_road']} lane {m['from_lane']} -> "
            f"road {m['to_road']} lane {m['to_lane']} via {m['connecting_road']}: "
            f"route {self.route}, lane on entry {self.final_lane}, "
            f"max jump {self.max_jump:.4f} m, min rc {self.min_return_code}"
        )


def drive_movements(xodr_path: PathLike, lib_path: Optional[PathLike] = None) -> list[DriveResult]:
    xodr_path = Path(xodr_path)
    movements = json.loads(sidecar_path(xodr_path).read_text())["movements"]
    roads = {int(r.get("id")): r for r in etree.parse(str(xodr_path)).getroot().findall("road")}
    lengths = {rid: float(r.get("length")) for rid, r in roads.items()}
    lib = _load_rm(lib_path)
    if lib.RM_Init(str(xodr_path).encode()) != 0:
        raise RuntimeError(f"esmini RoadManager failed to load {xodr_path}")
    splits: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
    for m in movements:
        splits.setdefault((m["from_road"], m["from_lane"], m["to_road"]), []).append(
            (m["connecting_road"], m["to_lane"]))
    try:
        results = []
        for m in movements:
            r = _drive(lib, m, lengths[m["from_road"]], lengths[m["connecting_road"]],
                       _full_width_run(roads[m["from_road"]], m["from_lane"]),
                       _full_width_run(roads[m["to_road"]], m["to_lane"], at_exit=False))
            r.accepted = tuple(splits[(m["from_road"], m["from_lane"], m["to_road"])])
            results.append(r)
        return results
    finally:
        lib.RM_Close()


def _full_width_run(road: etree._Element, lane: int, at_exit: bool = True) -> float:
    """How far the lane runs at constant width from the road end where its traffic
    leaves (at_exit; s = length for right lanes, 0 for left lanes) or enters:
    an approach must start, and a drive on must end, within that stretch,
    not in a taper or where the lane does not exist."""
    length = float(road.get("length"))
    sections = road.findall("lanes/laneSection")
    bounds = [float(sec.get("s")) for sec in sections] + [length]
    full = []
    for k, sec in enumerate(sections):
        el = sec.find(f"*/lane[@id='{lane}']")
        widths = el.findall("width") if el is not None else []
        full.append(bool(widths) and all(abs(float(w.get(c, 0))) < 1e-12 for w in widths for c in "bcd"))
    from_end = (lane < 0) == at_exit
    order = range(len(sections) - 1, -1, -1) if from_end else range(len(sections))
    run = 0.0
    for k in order:
        if not full[k]:
            break
        run += bounds[k + 1] - bounds[k]
    return run


def _drive(lib: ctypes.CDLL, m: dict, road_length: float, connecting_length: float,
           lane_run: Optional[float] = None, target_run: Optional[float] = None) -> DriveResult:
    # Aim the junction selector at the movement's actual heading change when
    # known (several exits can share a kind at 5-way junctions).
    if "turn_angle" in m:
        selector = m["turn_angle"] % (2 * math.pi)
    else:
        selector = SELECTOR_ANGLE[m["kind"]]
    handle = lib.RM_CreatePosition()
    # Right lanes travel along +s towards the end, left lanes towards s = 0.
    # Start on the road itself, also when it is shorter than APPROACH.
    approach = min(APPROACH, road_length / 2, (lane_run if lane_run is not None else road_length) - STEP)
    s0 = road_length - approach if m["from_lane"] < 0 else approach
    lib.RM_SetLanePosition(handle, m["from_road"], m["from_lane"], 0.0, s0, True)
    data = _PositionData()
    lib.RM_GetPositionData(handle, ctypes.byref(data))
    route, prev = [data.roadId], (data.x, data.y)
    max_jump, min_rc = 0.0, 0
    entry_lane = None
    # Through the junction however long its connecting road is, then a bit further.
    drive_on = DRIVE_ON if target_run is None else max(STEP, min(DRIVE_ON, target_run - STEP))
    distance = approach + connecting_length + drive_on
    for _ in range(math.ceil(distance / STEP)):
        rc = lib.RM_PositionMoveForward(handle, STEP, selector)
        if rc == END_OF_ROAD and len(route) >= 3:
            break  # the target road is a short dead end: the movement is done
        min_rc = min(min_rc, rc)
        lib.RM_GetPositionData(handle, ctypes.byref(data))
        max_jump = max(max_jump, abs(math.hypot(data.x - prev[0], data.y - prev[1]) - STEP))
        if data.roadId != route[-1]:
            route.append(data.roadId)
            # The target road may be short (e.g. a ring spur), so read the lane
            # on entry rather than after the full drive distance.
            if len(route) == 3:
                entry_lane = data.laneId
        prev = (data.x, data.y)
    return DriveResult(m, route, entry_lane, max_jump, min_rc)


LANE_STEP = 1.0  # [m] per move when driving a road's lanes
POS_TOL = 0.05  # [m] esmini's lane centre vs our own evaluation of the .xodr


@dataclass
class LaneDriveResult:
    road: int
    lane: int
    length: float
    max_deviation: float  # [m] between esmini's and our lane-centre position (x, y, z)
    left_lane: bool  # esmini changed lane or road before the end
    min_return_code: int

    @property
    def ok(self) -> bool:
        return self.max_deviation < POS_TOL and not self.left_lane and self.min_return_code >= 0

    def describe(self) -> str:
        return (f"road {self.road} lane {self.lane} ({self.length:.1f} m): max deviation "
                f"{self.max_deviation:.4f} m, left lane {self.left_lane}, min rc {self.min_return_code}")


def drive_lanes(xodr_path: PathLike, lib_path: Optional[PathLike] = None) -> list[LaneDriveResult]:
    """Drive every lane that runs a whole normal road, from end to end.

    Checks that esmini keeps to the lane through all lane sections (tapers
    and curves included) and that its position agrees with the .xodr as
    evaluated by roadgen.validate.continuity (line, arc, spiral geometry,
    lane offsets and widths, elevation and superelevation).
    """
    from roadgen.validate.continuity import RoadEval

    xodr_path = Path(xodr_path)
    roads = [RoadEval(r) for r in etree.parse(str(xodr_path)).getroot().findall("road")
             if r.get("junction", "-1") == "-1"]
    lib = _load_rm(lib_path)
    if lib.RM_Init(str(xodr_path).encode()) != 0:
        raise RuntimeError(f"esmini RoadManager failed to load {xodr_path}")
    try:
        return [_drive_lane(lib, road, lane) for road in roads for lane in _whole_lanes(road)]
    finally:
        lib.RM_Close()


def _whole_lanes(road) -> list[int]:
    """Driving lanes present in every lane section of the road."""
    sets = [{int(lane.get("id")) for lane in sec.iterfind("*/lane")
             if lane.get("id") != "0" and lane.get("type") == "driving"} for sec in road.sections]
    return sorted(set.intersection(*sets)) if sets else []


def _drive_lane(lib: ctypes.CDLL, road, lane: int) -> LaneDriveResult:
    rid = int(road.id)
    margin = min(1.0, road.length / 4)
    forward = lane < 0  # right lanes travel along +s
    s0 = margin if forward else road.length - margin
    handle = lib.RM_CreatePosition()
    lib.RM_SetLanePosition(handle, rid, lane, 0.0, s0, True)
    data = _PositionData()
    max_dev, left, min_rc = 0.0, False, 0
    # esmini moves along the lane centre, which is shorter or longer than the
    # reference line on curves: stop by s, a step before the road's end.
    for k in range(4 * math.ceil(road.length / LANE_STEP) + 4):
        if k:
            rc = lib.RM_PositionMoveForward(handle, LANE_STEP, 0.0)
            min_rc = min(min_rc, rc)
        lib.RM_GetPositionData(handle, ctypes.byref(data))
        if data.roadId != rid or data.laneId != lane:
            left = True
            break
        x, y = road.lane_center(lane, data.s)
        z = road.lane_height(lane, data.s)
        max_dev = max(max_dev, math.hypot(data.x - x, data.y - y), abs(data.z - z))
        remaining = road.length - data.s if forward else data.s
        if remaining < 2 * LANE_STEP:
            break
    return LaneDriveResult(rid, lane, road.length, max_dev, left, min_rc)
