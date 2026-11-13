"""Lateral acceleration needed to drive each road at its posted speed.

For every road (optionally only normal roads), the peak of v(s)^2 |kappa(s)|
over its driving lanes, with v from the road's <type><speed> records and
kappa the lane-centre curvature, must stay within a_lat_max (the design
comfort limit, 3 m/s^2 by default). The builder sets speeds so that this
holds: design radii, advisory speeds on tight bends and, for junction turns,
speeds from the turn's curvature.

check_resultant_grade: where a road is banked, the resultant slope
sqrt(grade^2 + e^2) stays within design.max_resultant_grade (or the grade
itself where that is steeper).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Union

import numpy as np
from lxml import etree

from roadgen.config import DEFAULTS
from roadgen.metrics.geometry import road_peak
from roadgen.validate.continuity import RoadEval, road_evals

A_LAT_MAX = DEFAULTS["design"]["a_lat_max"]  # [m/s^2]
MAX_RESULTANT = DEFAULTS["design"]["max_resultant_grade"]  # rise / run
BANK_RAMP = DEFAULTS["design"]["bank_ramp_length"]  # [m]
RESULTANT_TOLERANCE = 0.002  # rise / run
TOLERANCE = 0.05  # [m/s^2]
SAMPLE_STEP = 0.5  # [m]

PathLike = Union[str, Path]


def check_dynamics(xodr_path: PathLike, a_lat_max: float = A_LAT_MAX,
                   connecting: bool = True) -> list[str]:
    return check_dynamics_tree(etree.parse(str(xodr_path)), a_lat_max, connecting)


def check_dynamics_tree(tree: etree._ElementTree, a_lat_max: float = A_LAT_MAX,
                        connecting: bool = True, evals: Optional[dict[str, RoadEval]] = None) -> list[str]:
    errors = []
    evals = evals if evals is not None else road_evals(tree)
    for r in tree.getroot().findall("road"):
        if not connecting and r.get("junction", "-1") != "-1":
            continue
        road = evals[r.get("id")]
        _, a = road_peak(road, SAMPLE_STEP)
        if a > a_lat_max + TOLERANCE:
            errors.append(f"road {road.id}: needs {a:.2f} m/s^2 of lateral acceleration at its posted "
                          f"speed (limit {a_lat_max:.2f})")
    return errors


def check_resultant_grade(xodr_path: PathLike, max_resultant: float = MAX_RESULTANT,
                          step: float = 1.0) -> list[str]:
    return check_resultant_grade_tree(etree.parse(str(xodr_path)), max_resultant, step)


def check_resultant_grade_tree(tree: etree._ElementTree, max_resultant: float = MAX_RESULTANT,
                               step: float = 1.0, evals: Optional[dict[str, RoadEval]] = None) -> list[str]:
    """Normal roads whose banking makes the resultant slope sqrt(grade^2 + e^2)
    steeper than max_resultant, or than the grade alone where that is steeper:
    the builder banks a curve on a grade only as far as the resultant allows.
    The bank_ramp_length next to a junction end is left out: there the road's
    cross slope blends into the junction plane's (at most junction_max_grade)."""
    errors = []
    evals = evals if evals is not None else road_evals(tree)
    for r in tree.getroot().findall("road"):
        if r.get("junction", "-1") != "-1" or r.find("lateralProfile/superelevation") is None:
            continue
        road = evals[r.get("id")]
        lo = BANK_RAMP if _at_junction(r, "predecessor") else 0.0
        hi = road.length - (BANK_RAMP if _at_junction(r, "successor") else 0.0)
        worst = None
        for s in np.arange(lo, hi, step):
            e = math.tan(road.superelevation(float(s)))
            if abs(e) < 1e-4:
                continue
            g = road.elevation(float(s))[1]
            excess = math.hypot(g, e) - max(max_resultant, abs(g))
            if excess > RESULTANT_TOLERANCE and (worst is None or excess > worst[0]):
                worst = (excess, float(s), g, e)
        if worst:
            _, s, g, e = worst
            errors.append(f"road {road.id}: banking {100 * abs(e):.1f} % on a {100 * abs(g):.1f} % grade at "
                          f"s={s:.0f} m gives a {100 * math.hypot(g, e):.1f} % resultant slope "
                          f"(limit {100 * max_resultant:.0f} %)")
    return errors


def _at_junction(road: etree._Element, kind: str) -> bool:
    link = road.find(f"link/{kind}")
    return link is not None and link.get("elementType") == "junction"
