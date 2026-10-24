"""Which nodes cause the build failures of the lsystem sweep? (stage A, A0 finding)

Usage (dev container): python plans/scripts/trim_failures.py SEED [SEED ...]
"""
import math
import sys

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import get_generator, load_params
from roadgen.maps.base import make_map
from roadgen.maps.model import MAP_KINDS, GridSpec, MapSet
from roadgen.geometry.junction_space import node_trims

for seed in map(int, sys.argv[1:]):
    gen = get_generator("lsystem")
    params = load_params(gen.params_type, preset="city", defaults=section(DEFAULTS, "generators", "lsystem"))
    g = DEFAULTS["maps"]["grid"]
    grid = GridSpec.from_bounds(gen.map_bounds(params), g["margin"], g["cell_size"])
    layers = {}
    for kind in MAP_KINDS:
        name = DEFAULTS["maps"]["pipeline"][kind]
        layers[kind] = make_map(kind, name, grid, seed, defaults=section(DEFAULTS, "maps", kind, name),
                                upstream=MapSet(**layers))
    graph = gen.generate(params, seed, maps=MapSet(**layers))
    trims = {n: node_trims(graph, n) for n in graph.nodes if graph.degree(n) >= 2}
    for eid in sorted(graph.edges):
        e = graph.edges[eid]
        t = [trims.get(n, {}).get(eid, 0.0) for n in (e.from_node, e.to_node)]
        if sum(t) < graph.edge_length(eid):
            continue
        n = e.from_node if t[0] >= t[1] else e.to_node
        node = graph.nodes[n]
        arms = []
        for a in graph.incident_edges(n):
            o = graph.nodes[a.to_node if a.from_node == n else a.from_node]
            arms.append((round(math.degrees(math.atan2(o.y - node.y, o.x - node.x)) % 360, 1),
                         a.lanes_forward, a.lanes_backward))
        print(f"seed {seed}: edge {eid} ({graph.edge_length(eid):.0f} m) trims {t[0]:.0f}+{t[1]:.0f}; "
              f"worst node {n} degree {graph.degree(n)} arms (deg, fwd, bwd) {sorted(arms)}")
