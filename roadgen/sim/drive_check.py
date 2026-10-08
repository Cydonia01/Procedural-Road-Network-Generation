"""Drive every junction movement with esmini's RoadManager library.

For each movement in the builder's sidecar, a position is placed in the
incoming lane 20 m before the junction and moved forward in small steps with
esmini's junction selector aimed at the movement. A movement passes when
esmini routes it over the expected connecting road into the expected lane,
the position never jumps, and esmini never reports an error.

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

APPROACH = 20.0  # m before the junction
STEP = 0.5  # m per move
DISTANCE = 60.0  # m driven in total
JUMP_TOL = 0.05  # m deviation from STEP that counts as a jump

# esmini's junctionSelectorAngle, measured counter-clockwise from straight
# ahead (verified empirically against esmini 3.9).
SELECTOR_ANGLE = {"straight": 0.0, "left": math.pi / 2, "right": 3 * math.pi / 2}


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

    @property
    def ok(self) -> bool:
        m = self.movement
        return (
            self.route[:3] == [m["from_road"], m["connecting_road"], m["to_road"]]
            and self.final_lane == m["to_lane"]
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
    lengths = {
        int(r.get("id")): float(r.get("length"))
        for r in etree.parse(str(xodr_path)).getroot().findall("road")
    }
    lib = _load_rm(lib_path)
    if lib.RM_Init(str(xodr_path).encode()) != 0:
        raise RuntimeError(f"esmini RoadManager failed to load {xodr_path}")
    try:
        return [_drive(lib, m, lengths[m["from_road"]]) for m in movements]
    finally:
        lib.RM_Close()


def _drive(lib: ctypes.CDLL, m: dict, road_length: float) -> DriveResult:
    # Aim the junction selector at the movement's actual heading change when
    # known (several exits can share a kind at 5-way junctions).
    if "turn_angle" in m:
        selector = m["turn_angle"] % (2 * math.pi)
    else:
        selector = SELECTOR_ANGLE[m["kind"]]
    handle = lib.RM_CreatePosition()
    # Right lanes travel along +s towards the end, left lanes towards s = 0.
    s0 = road_length - APPROACH if m["from_lane"] < 0 else APPROACH
    lib.RM_SetLanePosition(handle, m["from_road"], m["from_lane"], 0.0, s0, True)
    data = _PositionData()
    lib.RM_GetPositionData(handle, ctypes.byref(data))
    route, prev = [data.roadId], (data.x, data.y)
    max_jump, min_rc = 0.0, 0
    entry_lane = None
    for _ in range(int(DISTANCE / STEP)):
        min_rc = min(min_rc, lib.RM_PositionMoveForward(handle, STEP, selector))
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
