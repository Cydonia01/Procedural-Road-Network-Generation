"""Lateral acceleration needed to drive each road at its posted speed.

For every road (optionally only normal roads), the peak of v(s)^2 |kappa(s)|
over its driving lanes, with v from the road's <type><speed> records and
kappa the lane-centre curvature, must stay within a_lat_max (the design
comfort limit, 3 m/s^2 by default). The builder sets speeds so that this
holds: design radii, advisory speeds on tight bends and, for junction turns,
speeds from the turn's curvature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from lxml import etree

from roadgen.config import DEFAULTS
from roadgen.metrics.geometry import road_peak
from roadgen.validate.continuity import RoadEval

A_LAT_MAX = DEFAULTS["design"]["a_lat_max"]  # [m/s^2]
TOLERANCE = 0.05  # [m/s^2]
SAMPLE_STEP = 0.5  # [m]

PathLike = Union[str, Path]


def check_dynamics(xodr_path: PathLike, a_lat_max: float = A_LAT_MAX,
                   connecting: bool = True) -> list[str]:
    return check_dynamics_tree(etree.parse(str(xodr_path)), a_lat_max, connecting)


def check_dynamics_tree(tree: etree._ElementTree, a_lat_max: float = A_LAT_MAX,
                        connecting: bool = True) -> list[str]:
    errors = []
    for r in tree.getroot().findall("road"):
        if not connecting and r.get("junction", "-1") != "-1":
            continue
        road = RoadEval(r)
        _, a = road_peak(road, SAMPLE_STEP)
        if a > a_lat_max + TOLERANCE:
            errors.append(f"road {road.id}: needs {a:.2f} m/s^2 of lateral acceleration at its posted "
                          f"speed (limit {a_lat_max:.2f})")
    return errors
