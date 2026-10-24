"""Summary of the fitted network alignment of RoadGraph JSONs (stage A, A2 check).

Usage (dev container): python plans/scripts/alignment_summary.py GRAPH.json [...]
"""
import sys
import time

import numpy as np

from roadgen.geometry.alignment import network_alignment
from roadgen.graph.io import load_json

for path in sys.argv[1:]:
    g = load_json(path)
    t = time.perf_counter()
    na = network_alignment(g)
    dt = time.perf_counter() - t
    corners = [k for c in na.chains for k in c.corners]
    scales = np.array([k.scale for k in corners]) if corners else np.array([1.0])
    adv = [k for k in corners if k.advisory_speed is not None]
    ratio = np.array([k.advisory_speed / k.design_speed for k in adv]) if adv else np.array([1.0])
    two = sum(1 for n in na.junctions if g.degree(n) == 2)
    gaps = max((abs(c.alignment.length - c.station_breaks[-1] + c.trim_end) for c in na.chains), default=0)
    print(f"{path}: {len(g.edges)} edges -> {len(na.chains)} roads, {len(na.junctions)} junctions "
          f"({two} two-road kept), {len(corners)} corners in {dt:.2f} s; "
          f"design shape kept {np.mean(scales >= 1 - 1e-9):.0%}, median scale {np.median(scales):.2f}; "
          f"{len(adv)} advisories, advisory/design median {np.median(ratio):.2f} p10 {np.percentile(ratio, 10):.2f}; "
          f"min radius {min((k.radius for k in corners), default=0):.1f} m")
