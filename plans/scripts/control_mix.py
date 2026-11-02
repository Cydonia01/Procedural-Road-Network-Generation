"""Stage B, B0: calibrate the density thresholds of junction control and cross-section profiles.

For the networks on maps it samples the population density at every junction
and every edge midpoint and prints, per candidate threshold [people/km^2]:

- the share of junctions of each class pair (highest x lowest class) at or
  above it (candidates for signal_density: lights at about a quarter of
  `city`'s avenue x street junctions);
- the share of road length at or above it (candidates for urban_density and
  suburban_density).

Usage (dev container): python plans/scripts/control_mix.py [--json OUT]
"""
import json
import sys

import numpy as np

sys.path.insert(0, "plans/scripts")
from side_room import RANK, build

from roadgen.geometry.chains import find_chains, junction_nodes

NETWORKS = [("city", "city", 7), ("mixed_city", "mixed_city", 1), ("highway_corridor", "highway_corridor", 1)]
THRESHOLDS = [1000, 1500, 2000, 3000, 4000, 5000, 6000, 8000]


def main():
    results = []
    for label, preset, seed in NETWORKS:
        graph, layers = build(preset, seed, True, {})
        pop = layers["population"]
        jn = junction_nodes(graph, find_chains(graph))
        by_pair: dict[str, list[float]] = {}
        for n in jn:
            classes = sorted((e.road_class for e in graph.incident_edges(n)), key=lambda c: -RANK[c])
            node = graph.nodes[n]
            by_pair.setdefault(f"{classes[0]}x{classes[-1]}", []).append(float(pop.sample(node.x, node.y)))
        lengths, dens = [], []
        for e in graph.edges.values():
            a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
            lengths.append(graph.edge_length(e.id))
            dens.append(float(pop.sample((a.x + b.x) / 2, (a.y + b.y) / 2)))
        lengths, dens = np.array(lengths), np.array(dens)
        row = {"network": label, "junctions": {k: len(v) for k, v in by_pair.items()},
               "junction_share_at_or_above": {
                   str(t): {k: round(float(np.mean(np.array(v) >= t)), 3) for k, v in by_pair.items()}
                   for t in THRESHOLDS},
               "length_share_at_or_above": {str(t): round(float(lengths[dens >= t].sum() / lengths.sum()), 3)
                                            for t in THRESHOLDS},
               "density_percentiles": {p: round(float(np.percentile(dens, p))) for p in (10, 25, 50, 75, 90)}}
        results.append(row)
        print(json.dumps(row), flush=True)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
