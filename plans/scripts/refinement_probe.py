"""Numbers for plans/2026-11-06-refinement-review.md: lane-count jumps, class
changes along straight continuations, dead ends and road-body overlaps in
saved RoadGraph JSON files (read only).

    python plans/scripts/refinement_probe.py out/<hash>/city.json ...

Approximations: edges are straight segments between their nodes. Body overlaps
come from roadgen.metrics.network.naturalness (height-aware: overpasses are not
overlaps).
"""

from __future__ import annotations

import math
import sys

from roadgen.graph.io import load_json
from roadgen.metrics.network import naturalness

STRAIGHT_DEG = 30.0  # deflection below which two arms read as one road [deg]


def _heading_out(g, eid: int, node: int) -> float:
    e = g.edges[eid]
    a, b = (e.from_node, e.to_node) if e.from_node == node else (e.to_node, e.from_node)
    na, nb = g.nodes[a], g.nodes[b]
    return math.atan2(nb.y - na.y, nb.x - na.x)


def probe(path: str) -> dict:
    g = load_json(path)
    adj: dict[int, list[int]] = {n: [] for n in g.nodes}
    for e in g.edges.values():
        adj[e.from_node].append(e.id)
        adj[e.to_node].append(e.id)
    dead = [n for n, es in adj.items() if len(es) == 1]
    straight_pairs = lane_jumps = class_changes = 0
    for n, es in adj.items():
        for i in range(len(es)):
            for j in range(i + 1, len(es)):
                d = abs((_heading_out(g, es[i], n) - _heading_out(g, es[j], n) + math.pi)
                        % (2 * math.pi) - math.pi)
                if math.pi - d > math.radians(STRAIGHT_DEG):
                    continue  # not a straight continuation through n
                straight_pairs += 1
                a, b = g.edges[es[i]], g.edges[es[j]]
                lane_jumps += a.total_lanes != b.total_lanes
                class_changes += a.road_class != b.road_class
    overlaps = naturalness(g)["body_overlaps"]  # height-aware (F15)
    return {"file": path, "nodes": len(g.nodes), "edges": len(g.edges),
            "dead_ends": len(dead), "dead_end_share": round(len(dead) / max(1, len(g.nodes)), 3),
            "straight_continuations": straight_pairs, "lane_jumps": lane_jumps,
            "class_changes": class_changes, "body_overlaps": overlaps}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(probe(p))
