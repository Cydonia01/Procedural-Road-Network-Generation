"""Lane layout for a single road cross-section (right-hand traffic).

Right lanes (negative ids) carry forward traffic along the reference line,
left lanes (positive ids) carry backward traffic. A one-way road puts all
lanes on the right. Each lane's road mark sits on its outer border; the
center lane's mark is the center line.
"""

from __future__ import annotations

from scenariogeneration import xodr

MARK_WIDTH = 0.15
BROKEN_LENGTH = 3.0
BROKEN_SPACE = 9.0


def solid_mark() -> xodr.RoadMark:
    return xodr.RoadMark(xodr.RoadMarkType.solid, MARK_WIDTH)


def broken_mark() -> xodr.RoadMark:
    mark = xodr.RoadMark(xodr.RoadMarkType.broken, MARK_WIDTH)
    mark.add_specific_road_line(
        xodr.RoadLine(MARK_WIDTH, BROKEN_LENGTH, BROKEN_SPACE, 0, 0)
    )
    return mark


def _driving_lane(lane_width: float, outermost: bool) -> xodr.Lane:
    lane = xodr.Lane(xodr.LaneType.driving, a=lane_width)
    lane.add_roadmark(solid_mark() if outermost else broken_mark())
    return lane


def make_lanes(
    lanes_forward: int, lanes_backward: int, lane_width: float
) -> xodr.Lanes:
    if lanes_forward < 0 or lanes_backward < 0:
        raise ValueError("lane counts must be non-negative")
    if lanes_forward == 0:
        # Keeps one-way roads on the right side: the caller must reverse the
        # reference line so the lanes become forward lanes.
        raise ValueError("lanes_forward must be >= 1; reverse the reference line")
    if lane_width <= 0:
        raise ValueError("lane_width must be positive")

    right, left = lanes_forward, lanes_backward
    center = xodr.Lane()
    center.add_roadmark(solid_mark())
    section = xodr.LaneSection(0, center)
    for i in range(right):
        section.add_right_lane(_driving_lane(lane_width, i == right - 1))
    for i in range(left):
        section.add_left_lane(_driving_lane(lane_width, i == left - 1))

    lanes = xodr.Lanes()
    lanes.add_lanesection(section)
    return lanes
