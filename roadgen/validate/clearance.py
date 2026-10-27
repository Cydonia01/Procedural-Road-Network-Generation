"""Vertical clearance of an .xodr: roads that cross outside junctions must be apart.

Two normal roads (not connecting roads of a junction) whose reference lines
cross in plan meet nowhere: one is an overpass over the other (stage C8). At
every such crossing the two reference heights must differ by at least
min_gap; otherwise the network has two roads on top of each other. A
contact within END_TOL of either road's end is not a crossing (a dead end
reaching another road is the generator's layout, not an overpass).

Works on the .xodr alone, like the other file validators.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Union

import numpy as np
from lxml import etree
from shapely.geometry import LineString
from shapely.strtree import STRtree

from roadgen.validate.continuity import RoadEval

PathLike = Union[str, Path]
MIN_GAP = 5.0  # [m] vertical clearance between the surfaces of crossing roads
SAMPLE_STEP = 2.0  # [m] along each reference line
END_TOL = 1.0  # [m] contacts this close to a road's end are not crossings


def check_clearance(xodr_path: PathLike, min_gap: float = MIN_GAP) -> list[str]:
    return check_clearance_tree(etree.parse(str(xodr_path)), min_gap)


def check_clearance_tree(tree: etree._ElementTree, min_gap: float = MIN_GAP) -> list[str]:
    """Errors for roads crossing outside junctions with less than min_gap between them."""
    roads = [RoadEval(r) for r in tree.getroot().findall("road") if r.get("junction", "-1") == "-1"]
    lines = []
    for road in roads:
        s = np.linspace(0.0, road.length, max(2, math.ceil(road.length / SAMPLE_STEP) + 1))
        lines.append(LineString([road.reference(float(v))[:2] for v in s]))
    tree_ = STRtree(lines)
    errors = []
    for i, road in enumerate(roads):
        for j in tree_.query(lines[i], predicate="crosses"):
            if j <= i:
                continue
            for pt in _points(lines[i].intersection(lines[j])):
                sa, sb = lines[i].project(pt), lines[j].project(pt)
                if min(sa, road.length - sa, sb, roads[j].length - sb) < END_TOL:
                    continue
                za = road.elevation(sa)[0]
                zb = roads[j].elevation(sb)[0]
                if abs(za - zb) < min_gap - 1e-6:
                    errors.append(f"roads {road.id} and {roads[j].id} cross at ({pt.x:.1f}, {pt.y:.1f}) "
                                  f"only {abs(za - zb):.2f} m apart (need {min_gap} m)")
    return errors


def _points(geom) -> list:
    if geom.is_empty:
        return []
    if geom.geom_type == "Point":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Point"]
