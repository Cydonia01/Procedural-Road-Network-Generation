"""Holes in the union of every lane polygon of an .xodr near a point: road surface no lane owns.

Stage B (2026-10-30, before B1): measures the junction corner gap that the B0
spike found, on out/spike/junction.xodr and on today's builder output.

Usage (dev container): python plans/scripts/junction_gaps.py FILE.xodr X Y RADIUS
"""
import json
import sys

import numpy as np
from lxml import etree
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from roadgen.validate.continuity import RoadEval, _piecewise, _poly


def offset(ev, s):
    rec = _piecewise(ev.offsets, s, "s")
    return _poly(rec, s - float(rec.get("s"))) if rec is not None else 0.0


def inner_edge(ev, lid, s):
    sign = -1 if lid < 0 else 1
    if abs(lid) == 1:
        return offset(ev, s)
    return 2 * ev._lane_t(lid - sign, s) - inner_edge(ev, lid - sign, s)


def lane_polys(ev, step=0.25):
    L = ev.length
    ss = np.linspace(0, L, max(2, int(L / step) + 1))
    ids = set().union(*(ev.lane_ids(min(s, L - 1e-6)) for s in ss))
    out = []
    for lid in sorted(ids):
        a, b = [], []
        for s in ss:
            if lid not in ev.lane_ids(min(s, L - 1e-6)):
                continue
            x, y, h = ev.reference(s)
            t_in = inner_edge(ev, lid, s)
            t_out = 2 * ev._lane_t(lid, s) - t_in
            for t, lst in ((t_in, a), (t_out, b)):
                lst.append((x - t * np.sin(h), y + t * np.cos(h)))
        if len(a) >= 2:
            p = Polygon(a + b[::-1]).buffer(0)
            if p.area > 1e-6:
                out.append(p)
    return out


path, cx, cy, r = sys.argv[1], *map(float, sys.argv[2:5])
root = etree.parse(path).getroot()
polys = [p for road in root.findall("road") for p in lane_polys(RoadEval(road))]
u = unary_union([p.buffer(0.02) for p in polys])  # close sliver seams between touching lanes
area = box(cx - r, cy - r, cx + r, cy + r)
holes = [Polygon(i) for g in getattr(u, "geoms", [u]) for i in g.interiors]
holes = [h for h in holes if h.intersects(area) and h.area > 0.5]
print(json.dumps({"lanes": len(polys), "holes": len(holes),
                  "areas_m2": sorted(round(h.area, 1) for h in holes)}))
