"""Stage B (after B3): corner roads whose outermost lane edge folds over itself
(side lanes reaching past the centre of curvature of a tight right turn).

Usage (dev container): python plans/scripts/corner_folds.py FILE.xodr ...
"""
import sys

import numpy as np
from lxml import etree

from roadgen.opendrive.plot import _offset, _width
from roadgen.validate.continuity import RoadEval

for path in sys.argv[1:]:
    root = etree.parse(path).getroot()
    corners = [r for r in root.findall("road") if "_corner_" in (r.get("name") or "")]
    folded, worst = 0, []
    for road in corners:
        ev = RoadEval(road)
        ss = np.linspace(0, ev.length, 60)
        sec = ev.sections[0]
        lanes = sorted(sec.findall("right/lane"), key=lambda l: abs(int(l.get("id"))))
        pts = []
        for s in ss:
            x, y, h = ev.reference(s)
            t = _offset(ev, s) - sum(_width(l, s) for l in lanes)
            pts.append((x - t * np.sin(h), y + t * np.cos(h), h))
        pts = np.array(pts)
        d = np.diff(pts[:, :2], axis=0)
        tang = np.column_stack([np.cos(pts[:-1, 2]), np.sin(pts[:-1, 2])])
        back = np.sum(d * tang, axis=1) < 0  # the outer edge runs backwards: folded
        if back.any():
            folded += 1
            worst.append(back.mean())
    print(f"{path}: corner roads {len(corners)}, outer edge folded in {folded} ({100*folded/len(corners):.0f} %), median folded share {np.median(worst) if worst else 0:.2f}")
