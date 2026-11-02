"""Stage B8: esmini close-ups of a left-turn pocket with its lane arrows, and of a
junction-surface fill patch, in one network.

  python plans/scripts/b8_closeups.py out/stage_b/b8/city.xodr

Writes OUTDIR/closeups/<name>_pocket.png and <name>_fill.png (OUTDIR = the .xodr's
folder): top views over the 20 m arrows of the first approach with a pocket and over
the largest fill patch. Run in the dev container.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from lxml import etree
from shapely.geometry import Polygon

from roadgen.sim.screenshot import screenshot
from roadgen.validate.continuity import RoadEval


def main() -> None:
    xodr = Path(sys.argv[1])
    root = etree.parse(str(xodr)).getroot()
    side = json.loads(xodr.with_suffix(".roadmap.json").read_text())
    roads = {r.get("id"): r for r in root.findall("road")}
    out = xodr.parent / "closeups"
    use = next(u for u in side["lane_use"] if u["pockets"])
    road = roads[str(use["road"])]
    ev = RoadEval(road)
    arrows = [o for o in road.findall("objects/object") if o.get("name", "").startswith("arrow")]
    s = float(arrows[0].get("s")) if arrows else (ev.length - 20.0 if use["contact"] == "end" else 20.0)
    x, y, h = ev.reference(s)
    z = ev.elevation(s)[0]
    print(screenshot(out / f"{xodr.stem}_pocket.png", xodr=xodr, camera=(x, y, z + 25.0, h, math.pi / 2)))
    best = None
    for r in roads.values():
        for o in r.findall("objects/object"):
            if o.get("type") != "patch":
                continue
            pts = [(float(c.get("u")), float(c.get("v"))) for c in o.findall("outlines/outline/cornerLocal")]
            area = Polygon(pts).area
            if best is None or area > best[0]:
                best = (area, r, pts)
    area, r, pts = best
    ev = RoadEval(r)
    x0, y0, h0 = ev.reference(0.0)
    cu, cv = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    x, y = x0 + cu * math.cos(h0) - cv * math.sin(h0), y0 + cu * math.sin(h0) + cv * math.cos(h0)
    print(f"largest patch {area:.1f} m2")
    print(screenshot(out / f"{xodr.stem}_fill.png", xodr=xodr, camera=(x, y, ev.elevation(0.0)[0] + 30.0, h0, math.pi / 2)))


if __name__ == "__main__":
    main()
