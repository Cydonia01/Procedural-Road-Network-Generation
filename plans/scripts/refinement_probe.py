"""Numbers for plans/2026-11-06-refinement-review.md: lane-count jumps, class
changes along straight continuations, dead ends and road-body overlaps in
saved RoadGraph JSON files (read only).

    python plans/scripts/refinement_probe.py out/<hash>/city.json ...

Approximations: edges are straight segments between their nodes; a body is
the segment buffered by the driving half width (lanes * lane_width / 2).
"""

from __future__ import annotations

import math
import sys

from shapely.geometry import LineString
from shapely.strtree import STRtree

from roadgen.graph.io import load_json

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
    lines, owners = [], []
    for e in g.edges.values():
        na, nb = g.nodes[e.from_node], g.nodes[e.to_node]
        hw = e.total_lanes * e.lane_width / 2
        lines.append(LineString([(na.x, na.y), (nb.x, nb.y)]).buffer(hw, cap_style="flat"))
        owners.append(e)
    tree = STRtree(lines)
    overlaps = 0
    for i, j in zip(*tree.query(lines, predicate="intersects")):
        if i >= j:
            continue
        a, b = owners[i], owners[j]
        if {a.from_node, a.to_node} & {b.from_node, b.to_node}:
            continue  # neighbours at a junction: their trims handle that
        if lines[i].intersection(lines[j]).area > 1.0:
            overlaps += 1
    return {"file": path, "nodes": len(g.nodes), "edges": len(g.edges),
            "dead_ends": len(dead), "dead_end_share": round(len(dead) / max(1, len(g.nodes)), 3),
            "straight_continuations": straight_pairs, "lane_jumps": lane_jumps,
            "class_changes": class_changes, "body_overlaps": overlaps}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(probe(p))
