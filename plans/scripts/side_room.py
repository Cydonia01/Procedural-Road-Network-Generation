"""Stage B discussion: what wider roads (side lanes, corner radii) do to the junction trims,
and which junction-control cases the example networks contain.

For each network it recomputes every junction's trims (as
roadgen.geometry.junction_space does) with extra width outside the driving
lanes and, optionally, a curb-return fillet of radius R between neighbouring
arms (tangent length R / tan(theta / 2) beyond the point where the two curb
lines cross), then counts the edges the builder would reject (trims >= edge)
and the stub roads (< 5 m left, the repair's MIN_ROAD_LENGTH). Straight
edges, no fitting, so the counts are estimates.

Usage (dev container): python plans/scripts/side_room.py [--json OUT]
(output of 2026-10-29: plans/baselines/2026-10-29-stage-b-side-room.json)
"""
import json
import math
import sys
from collections import Counter

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import get_generator, load_params
from roadgen.geometry.chains import find_chains, junction_nodes
from roadgen.geometry.junction_space import MIN_ARM_ANGLE_DEG, oriented_lanes
from roadgen.maps.base import make_map
from roadgen.maps.model import MAP_KINDS, GridSpec, MapSet

MARGIN = DEFAULTS["opendrive"]["junction_margin"]
MIN_ROAD = 5.0
RANK = {"street": 0, "avenue": 1, "highway": 2}

# Extra width per side [m]: (carriageway outside the driving lanes, i.e. inside the curb;
# curb + footway outside it; median half width added at the centre line).
SCENARIOS = {
    "today": {c: (0.0, 0.0, 0.0) for c in RANK},
    "curb+sidewalk": {"street": (0.0, 0.15 + 2.0, 0.0), "avenue": (0.0, 0.15 + 3.0, 0.0),
                      "highway": (2.5, 0.0, 0.0)},
    "full": {"street": (2.2, 0.15 + 2.0, 0.0), "avenue": (1.5 + 2.4, 0.15 + 3.0, 0.0),
             "highway": (3.0, 0.0, 1.0)},
    "full, no parking at junctions": {"street": (0.0, 0.15 + 2.0, 0.0), "avenue": (1.5, 0.15 + 3.0, 0.0),
                                      "highway": (3.0, 0.0, 1.0)},
}
RADII = {"none": None, "streetgen": {"street": 3.0, "avenue": 4.9, "highway": 7.6}}

NETWORKS = [  # (label, preset, seed, maps, overrides)
    ("grid", "grid", 0, False, {}),
    ("manhattan_like", "manhattan_like", 1, False, {}),
    ("grid_organic", "grid_organic", 2, False, {}),
    ("organic_town", "organic_town", 1, False, {}),
    ("paris", "paris", 1, False, {}),
    ("suburb", "suburb", 1, False, {}),
    ("city (maps)", "city", 7, True, {}),
    ("mixed_city (maps)", "mixed_city", 1, True, {}),
    ("highway_corridor (maps)", "highway_corridor", 1, True, {}),
]


def build(preset, seed, maps, overrides):
    gen = get_generator("lsystem")
    params = load_params(gen.params_type, preset=preset, defaults=section(DEFAULTS, "generators", "lsystem"))
    if overrides:
        params = type(params)(**{**params.__dict__, **overrides})
    if not maps:
        return gen.generate(params, seed), None
    g = DEFAULTS["maps"]["grid"]
    grid = GridSpec.from_bounds(gen.map_bounds(params), g["margin"], g["cell_size"])
    layers = {}
    for kind in MAP_KINDS:
        name = DEFAULTS["maps"]["pipeline"][kind]
        layers[kind] = make_map(kind, name, grid, seed, defaults=section(DEFAULTS, "maps", kind, name),
                                upstream=MapSet(**layers))
    return gen.generate(params, seed, maps=MapSet(**layers)), layers


def arms(graph, node_id, extra):
    """(edge, heading, curb left, curb right, outer left, outer right, class) per arm."""
    node = graph.nodes[node_id]
    out = []
    for e in graph.incident_edges(node_id):
        right, left, reverse = oriented_lanes(e)
        start = e.to_node if reverse else e.from_node
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        hdg = math.atan2(other.y - node.y, other.x - node.x)
        inner, outside, med = extra[e.road_class]
        if left:  # two-way: median at the centre line, side lanes outside each direction
            cr, cl = right * e.lane_width + inner + med, left * e.lane_width + inner + med
        else:  # one-way: the reference line is the left edge; side lanes on both edges
            cr, cl = right * e.lane_width + inner, inner
        ol, orr = cl + outside, cr + outside
        if node_id != start:
            cl, cr, ol, orr = cr, cl, orr, ol
        out.append((e.id, hdg, cl, cr, ol, orr, e.road_class))
    return out


def trims(graph, node_id, extra, radii):
    a = arms(graph, node_id, extra)
    base = max(max(x[4], x[5]) for x in a)
    need = {x[0]: base for x in a}
    ordered = sorted(a, key=lambda x: (x[1] % (2 * math.pi), x[0]))
    for i, first in enumerate(ordered):
        second = ordered[(i + 1) % len(ordered)]
        theta = (second[1] - first[1]) % (2 * math.pi)
        if theta < math.radians(MIN_ARM_ANGLE_DEG) or theta >= math.pi - 1e-9:
            continue
        sin, cos = math.sin(theta), math.cos(theta)
        for ka, kb, fillet in ((2, 3, True), (4, 5, False)):  # curb lines (+ fillet), outer edges
            pa, pb = first[ka], second[kb]
            d1, d2 = (pb + pa * cos) / sin, (pa + pb * cos) / sin
            if d1 <= 0 or d2 <= 0:
                continue
            t = 0.0
            if fillet and radii:
                r = min(radii[first[6]], radii[second[6]], key=lambda v: v)
                t = r / math.tan(theta / 2)
            need[first[0]] = max(need[first[0]], d1 + t)
            need[second[0]] = max(need[second[0]], d2 + t)
    return {k: v + MARGIN for k, v in need.items()}


def short_counts(graph, extra, radii):
    chains = find_chains(graph)
    jn = junction_nodes(graph, chains)
    tr = {n: trims(graph, n, extra, radii) for n in jn}
    short = stub = ends = 0
    for c in chains:
        first, last = c.edges[0].edge_id, c.edges[-1].edge_id
        t0, t1 = tr.get(c.start, {}).get(first, 0.0), tr.get(c.end, {}).get(last, 0.0)
        if not (t0 or t1):
            continue
        ends += 1
        if first == last:
            room = graph.edge_length(first) - t0 - t1
            short += room <= 0
            stub += 0 < room < MIN_ROAD and c.start in tr and c.end in tr
        else:
            short += (t0 >= graph.edge_length(first)) or (t1 >= graph.edge_length(last))
    mean_trim = float(np.mean([v for d in tr.values() for v in d.values()])) if tr else 0.0
    return {"roads_at_junctions": ends, "unbuildable": int(short), "stubs_lt_5m": int(stub),
            "mean_trim_m": round(mean_trim, 1)}


def control_cases(graph, layers):
    """Junctions by leg count and class pair (highest x lowest class of its roads), and density."""
    chains = find_chains(graph)
    jn = junction_nodes(graph, chains)
    pairs, legs, dens = Counter(), Counter(), {}
    pop = layers.get("population") if layers else None
    for n in jn:
        classes = sorted((e.road_class for e in graph.incident_edges(n)), key=lambda c: -RANK[c])
        key = f"{classes[0]}x{classes[-1]}"
        pairs[key] += 1
        legs[graph.degree(n)] += 1
        if pop is not None:
            node = graph.nodes[n]
            dens.setdefault(key, []).append(float(pop.sample(node.x, node.y)))
    md = graph.metadata
    out = {"junctions": len(jn), "class_pairs": dict(pairs), "legs": dict(sorted(legs.items())),
           "roundabouts": len(md.get("roundabouts", [])),
           "interchanges": len(md.get("interchanges", [])),
           "one_way_edges": sum(e.is_one_way for e in graph.edges.values())}
    if dens:
        out["density_p50_by_pair"] = {k: round(float(np.median(v))) for k, v in dens.items()}
    return out


def main():
    results = []
    for label, preset, seed, maps, ov in NETWORKS:
        graph, layers = build(preset, seed, maps, ov)
        row = {"network": label, "seed": seed, **control_cases(graph, layers), "trims": {}}
        for sname, extra in SCENARIOS.items():
            for rname, radii in RADII.items():
                row["trims"][f"{sname} / radii {rname}"] = short_counts(graph, extra, radii)
        results.append(row)
        print(json.dumps(row), flush=True)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
