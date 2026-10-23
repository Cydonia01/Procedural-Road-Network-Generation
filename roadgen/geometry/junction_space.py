"""How much room a junction needs: where the roads meeting at a node must stop.

Pure geometry on arm headings and road widths, shared by the OpenDRIVE
builder, the 3D ground and the generators' repair pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
    cross at distance d1 = (b + a cos theta) / sin theta along the first arm and
    d2 = (a + b cos theta) / sin theta along the second; each arm is trimmed past
    every such crossing, so acute junctions never overlap.

    The crossing only matters when it lies on both arms (d1 > 0 and d2 > 0).
    Otherwise it lies behind one arm's start, and the two road surfaces only
    overlap within b |tan theta| <= a of the node, which the baseline covers.
    Near-straight arms of different widths (theta close to pi) would
    otherwise get a trim of about (b - a) / sin theta, hundreds of meters.

    Raises ValueError for arms closer than MIN_ARM_ANGLE_DEG.
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
            d1, d2 = (b + a * cos) / sin, (a + b * cos) / sin
            if d1 <= 0 or d2 <= 0:
                continue  # crossing behind one arm's start: within the baseline
            need[first.edge_id] = max(need[first.edge_id], d1)
            need[second.edge_id] = max(need[second.edge_id], d2)
    return {eid: d + margin for eid, d in need.items()}
