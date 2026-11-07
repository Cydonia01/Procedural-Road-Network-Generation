"""Clearance of an .xodr: roads that cross outside junctions must be apart, in
height (check_clearance) and in plan (check_body_overlaps).

Two normal roads (not connecting roads of a junction) whose reference lines
cross in plan meet nowhere: one is an overpass over the other (stage C8). At
every such crossing the two reference heights must differ by at least
min_gap; otherwise the network has two roads on top of each other. A
contact within END_TOL of either road's end is not a crossing (a dead end
reaching another road is the generator's layout, not an overpass).

Road bodies (stage R2): the driving lanes of two normal roads must not
overlap by more than min_area unless the roads meet (one links to the other,
or both end at the same junction, where the junction trims separate them) or
pass over each other with min_gap of height between them.

Works on the .xodr alone, like the other file validators.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Union

import numpy as np
from lxml import etree
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
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


def _neighbours(road: etree._Element) -> set[str]:
    """Roads and junctions ("j<id>") a road's ends link to."""
    out = set()
    for end in ("predecessor", "successor"):
        link = road.find(f"link/{end}")
        if link is not None:
            out.add(("j" if link.get("elementType") == "junction" else "") + link.get("elementId", ""))
    return out


def check_body_overlaps(xodr_path: PathLike, min_area: float = 1.0, min_gap: float = MIN_GAP) -> list[str]:
    return check_body_overlaps_tree(etree.parse(str(xodr_path)), min_area, min_gap)


def check_body_overlaps_tree(tree: etree._ElementTree, min_area: float = 1.0,
                             min_gap: float = MIN_GAP) -> list[str]:
    """Pairs of normal roads whose driving lanes overlap by more than min_area [m^2]
    without meeting, each as one message."""
    from roadgen.opendrive.plot import (
        lane_polygons,  # plot imports the validators' evaluator
    )

    roads = [r for r in tree.getroot().findall("road") if r.get("junction", "-1") == "-1"]
    bodies, owners = [], []
    for road in roads:
        parts = [Polygon(pts) for kind, pts in lane_polygons(road, step=SAMPLE_STEP) if kind == "driving"]
        parts = [p if p.is_valid else p.buffer(0) for p in parts if len(p.exterior.coords) >= 4]
        if parts:
            bodies.append(unary_union(parts))
            owners.append(road)
    if len(bodies) < 2:  # nothing can overlap (and shapely rejects an empty query)
        return []
    links = [_neighbours(r) for r in owners]
    evals: dict[int, RoadEval] = {}
    index = STRtree(bodies)
    errors = []
    for i, j in zip(*index.query(bodies, predicate="intersects")):
        if j <= i:
            continue
        a, b = owners[i], owners[j]
        if a.get("id") in links[j] or b.get("id") in links[i] or links[i] & links[j] & {
                x for x in links[i] if x.startswith("j")}:
            continue
        overlap = bodies[i].intersection(bodies[j])
        if overlap.area <= min_area:
            continue
        # An overpass: the two roads are min_gap apart in height over the overlap.
        pt = overlap.representative_point()
        ea = evals.setdefault(i, RoadEval(a))
        eb = evals.setdefault(j, RoadEval(b))
        sa = _project_s(ea, bodies[i], pt)
        sb = _project_s(eb, bodies[j], pt)
        if abs(ea.elevation(sa)[0] - eb.elevation(sb)[0]) >= min_gap - 1e-6:
            continue
        errors.append(f"roads {a.get('id')} and {b.get('id')} overlap by {overlap.area:.1f} m^2 "
                      f"at ({pt.x:.1f}, {pt.y:.1f}) without a junction")
    return errors


def _project_s(road: RoadEval, body, pt) -> float:
    """s of the reference-line point nearest pt."""
    s = np.linspace(0.0, road.length, max(2, math.ceil(road.length / SAMPLE_STEP) + 1))
    line = LineString([road.reference(float(v))[:2] for v in s])
    return float(min(road.length, line.project(pt)))
