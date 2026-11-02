"""Stage B3: junction surface that no lane owns, in built .xodr files.

For each file: corner roads (and how many follow a driven right turn, so they
close their corner), and the holes in the union of all lane polygons that lie
inside a junction (within the convex hull of its connecting and corner roads),
split into: next to a closing corner road (should be none), next to another
corner road (between one-way roads where no movement drives the corner), and
the rest, slivers between turning lanes. Other holes are city blocks and
islands. Holes under MIN_AREA are seams.

Usage (dev container): python plans/scripts/b3_gaps.py FILE.xodr ... [--json OUT]
"""
import json
import sys
from pathlib import Path

from lxml import etree
from shapely.geometry import Polygon
from shapely.ops import unary_union

from roadgen.opendrive.plot import lane_polygons

MIN_AREA = 0.5  # [m^2]
INSIDE = 0.95  # share of a hole within a junction's hull that makes it a junction gap


def gaps(path: Path) -> dict:
    root = etree.parse(str(path)).getroot()
    sidecar = json.loads(path.with_suffix(".roadmap.json").read_text())
    corners = sidecar.get("corner_roads", [])
    closing = {str(c["road"]) for c in corners if c["closes"]}
    open_ = {str(c["road"]) for c in corners if not c["closes"]}
    polys = {r.get("id"): unary_union([Polygon(p).buffer(0) for _, p in lane_polygons(r, 0.5)])
             for r in root.findall("road")}
    members: dict[str, list] = {}
    for r in root.findall("road"):
        if r.get("junction", "-1") != "-1":
            members.setdefault(r.get("junction"), []).append(polys[r.get("id")])
    hulls = [unary_union(ps).convex_hull for ps in members.values()]
    u = unary_union([p.buffer(0.02) for p in polys.values()])
    holes = [Polygon(i) for g in getattr(u, "geoms", [u]) for i in g.interiors if Polygon(i).area > MIN_AREA]
    near_closing = unary_union([polys[r] for r in closing]) if closing else None
    near_open = unary_union([polys[r] for r in open_]) if open_ else None
    kinds = {"closing_corner": [], "other_corner": [], "interior": [], "blocks_islands": []}
    for h in holes:
        if not any(h.intersection(hull).area >= INSIDE * h.area for hull in hulls):
            kinds["blocks_islands"].append(h.area)
        elif near_closing is not None and h.distance(near_closing) < 0.1:
            kinds["closing_corner"].append(h.area)
        elif near_open is not None and h.distance(near_open) < 0.1:
            kinds["other_corner"].append(h.area)
        else:
            kinds["interior"].append(h.area)
    return {"xodr": path.name, "junctions": len(root.findall("junction")), "corner_roads": len(corners),
            "closing": len(closing),
            "holes": {k: {"n": len(v), "area_m2": round(sum(v), 1), "max_m2": round(max(v, default=0.0), 1)}
                      for k, v in kinds.items()}}


def main():
    args = [a for a in sys.argv[1:]]
    out = None
    if "--json" in args:
        out = args[args.index("--json") + 1]
        args = args[:args.index("--json")]
    results = [gaps(Path(p)) for p in args]
    for r in results:
        print(json.dumps(r), flush=True)
    if out:
        Path(out).write_text(json.dumps(results, indent=1) + "\n")


if __name__ == "__main__":
    main()
