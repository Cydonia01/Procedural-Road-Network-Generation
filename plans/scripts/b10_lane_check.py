"""Stage B10: are the simulated vehicles only on driving lanes? (exit criterion, `city`)

  python plans/scripts/b10_lane_check.py NET.xodr NET.csv [EVERY]
  e.g. python plans/scripts/b10_lane_check.py out/stage_b/b10/city.xodr out/stage_b/b10/city.csv

Independent of esmini's own lane lookup (which snaps to driving lanes): builds every
lane's outline from the .xodr (opendrive.plot.lane_polygons, 1 m steps, junction fill
patches as driving surface) and tests each vehicle's reference point (the point
esmini keeps on its lane; the bounding-box centre lies 1.4 m ahead, past the end of a
dead-end road where cars stop) from esmini's per-step CSV (`simulate` writes NET.csv), every EVERY-th step (default 5).
Prints how many samples lie on a driving lane (within 0.3 m), and for the others the
lane types under them. Run in the dev container.
"""
from __future__ import annotations

import collections
import sys

from lxml import etree
from shapely import STRtree
from shapely.geometry import Point, Polygon

from roadgen.opendrive.plot import furniture, lane_polygons

TOL = 0.3  # [m] the outline is sampled every metre


def polygons(xodr: str) -> tuple[list[Polygon], list[str]]:
    polys, kinds = [], []
    for road in etree.parse(xodr).getroot().findall("road"):
        for kind, outline in lane_polygons(road, 1.0):
            if len(outline) >= 3:
                polys.append(Polygon(outline).buffer(0))
                kinds.append(kind)
        for patch in furniture(road)[2]:  # junction fill: driving surface between arms
            polys.append(Polygon(patch).buffer(0))
            kinds.append("driving")
    return polys, kinds


def samples(csv: str, every: int):
    lines = open(csv).read().splitlines()
    head = next(k for k, line in enumerate(lines) if line.startswith("Index"))
    cols = [c.strip() for c in lines[head].split(",")]
    n = sum(1 for c in cols if c.endswith("Entity_Name [-]"))
    for line in lines[head + 1::every]:
        vals = [v.strip() for v in line.split(",")]
        for i in range(1, n + 1):
            yield (vals[cols.index(f"#{i} Entity_Name [-]")], float(vals[cols.index(f"#{i} World_Position_X [m]")]),
                   float(vals[cols.index(f"#{i} World_Position_Y [m]")]))


def main() -> None:
    xodr, csv = sys.argv[1], sys.argv[2]
    every = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    polys, kinds = polygons(xodr)
    tree = STRtree(polys)
    on_driving, total, off = 0, 0, collections.Counter()
    for name, x, y in samples(csv, every):
        total += 1
        p = Point(x, y)
        near = tree.query(p.buffer(TOL))
        if any(kinds[k] == "driving" and polys[k].distance(p) <= TOL for k in near):
            on_driving += 1
        else:
            under = sorted({kinds[k] for k in near if polys[k].contains(p)}) or ["nothing"]
            off["/".join(under)] += 1
            print(f"off a driving lane: {name} at ({x:.1f}, {y:.1f}) on {'/'.join(under)}")
    print(f"{on_driving} of {total} samples on a driving lane ({100 * on_driving / max(total, 1):.2f} %); "
          f"others: {dict(off) or 'none'}")


if __name__ == "__main__":
    main()
