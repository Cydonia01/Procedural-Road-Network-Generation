"""Stage B, B1 result: real junction trims with cross-sections on the example networks.

For each network (the side_room.py set) and profile (urban, suburban, rural):
edges too short before the fit, edges the fit repairs (as cli.fit_graph does),
mean trim before and after, how many curb returns were shrunk to fit, and
whether the builder's alignment then succeeds. Compare with side_room.py's
estimates (baselines/2026-10-29-stage-b-side-room.json).

Usage (dev container): python plans/scripts/b1_trims.py [--json OUT]
"""
import copy
import json
import sys
import time

import numpy as np

sys.path.insert(0, "plans/scripts")
from side_room import NETWORKS, build

from roadgen.generators.repair import (
    MAX_DEGREE,
    repair_short_edges,
    short_edges,
    stub_roads,
)
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.cross_section import CrossSectionParams
from roadgen.geometry.junction_space import node_corners

PROFILES = ("urban", "suburban", "rural")


def mean_trim(na) -> float:
    return round(float(np.mean([t for d in na.trims.values() for t in d.values()])), 2)


def main():
    results = []
    for label, preset, seed, maps, ov in NETWORKS:
        graph, _ = build(preset, seed, maps, ov)
        today = network_alignment(graph)
        row = {"network": label, "junction_roads": sum(len(d) for d in today.trims.values()),
               "mean_trim_today": mean_trim(today), "profiles": {}}
        for profile in PROFILES:
            cs = CrossSectionParams(profile=profile)
            g = copy.deepcopy(graph)
            t0 = time.time()
            short = short_edges(g, cross_section=cs)
            known = frozenset(stub_roads(g, dead_ends=True))
            max_degree = max([MAX_DEGREE] + [g.degree(n) for n in g.nodes])
            fixed = repair_short_edges(g, junction_margin=2.0, max_degree=max_degree, cross_section=cs,
                                       keep_stubs=known, dead_ends=True)
            secs = round(time.time() - t0, 1)
            try:
                na = network_alignment(g, cross_section=cs)
                ok, trim = True, mean_trim(na)
                corners = [c for n in na.junctions for c in node_corners(g, n, cs)]
                shrunk = sum(c.radius < c.wanted - 1e-9 for c in corners)
            except ValueError as err:
                ok, trim, corners, shrunk = str(err)[:100], None, [], 0
            row["profiles"][profile] = {
                "short_before": len(short), "repaired": fixed,
                "repaired_share": round(fixed / max(row["junction_roads"], 1), 4),
                "builds": ok, "mean_trim": trim, "corners": len(corners), "corners_shrunk": shrunk,
                "fit_s": secs}
        results.append(row)
        print(json.dumps(row), flush=True)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
