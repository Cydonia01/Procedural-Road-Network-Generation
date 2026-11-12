"""Road hierarchy from strokes: lanes by what a road does in the network.

A *stroke* is a chain of edges that continue each other (Porta et al. 2006,
"named streets"). At a node with two roads both belong to one stroke (a bend
is still the same road). At a node with three or more roads, pairs of arms
join greedily, straightest first, while they turn less than max_deflection.

Strokes are ranked by length x betweenness: the sum over their edges of edge
length times sampled edge betweenness (shortest paths weighted by length).
The best strokes become arterials until they hold arterial_share of the
length, then collectors up to collector_share more, the rest local roads.
Every edge of a stroke gets the stroke's class, lanes and speed, so lane
counts change only where strokes meet at junctions with three or more roads.

Fixed edges (highways, ring roads) keep what they have and take no part,
except that a stroke running straight on from a highway at a junction (and
straighter than the highway goes on, if it does), or meeting a highway's
end at a two-road node, is at least arterial: a freeway runs out into an
arterial, not into a local street.
Edges that are one-way keep their direction and put all lanes forward;
asymmetric lane tuples are oriented along the stroke.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

import networkx as nx

from roadgen.graph.model import RoadClass, RoadGraph

log = logging.getLogger(__name__)

ARTERIAL, COLLECTOR, LOCAL = "arterial", "collector", "local"
LEVELS = (ARTERIAL, COLLECTOR, LOCAL)


@dataclass(frozen=True)
class Level:
    road_class: RoadClass
    lanes: tuple[int, int]  # (forward, backward) along the stroke
    speed: float  # [m/s]


@dataclass(frozen=True)
class HierarchyParams:
    levels: dict[str, Level]  # ARTERIAL, COLLECTOR, LOCAL
    max_deflection_deg: float = 35.0  # arms joined into one stroke turn less than this [deg]
    arterial_share: float = 0.15  # share of the ranked length that is arterial
    collector_share: float = 0.25  # share that is collector (after the arterials)
    samples: int = 200  # source nodes of the sampled betweenness; 0 = exact


def _away(graph: RoadGraph, eid: int, node: int) -> tuple[float, float]:
    """Unit vector from node along edge eid."""
    e = graph.edges[eid]
    other = graph.nodes[e.to_node if e.from_node == node else e.from_node]
    here = graph.nodes[node]
    dx, dy = other.x - here.x, other.y - here.y
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d


def _deflection(u: tuple[float, float], v: tuple[float, float]) -> float:
    """Turn [rad] when arriving along -u and leaving along v (0 = straight on)."""
    dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.pi - math.acos(dot)


def stroke_links(graph: RoadGraph, edges: Iterable[int], max_deflection: float) -> dict[int, list[tuple[int, int]]]:
    """Pairs of edges joined at each node: node -> [(edge, edge)]. max_deflection in rad."""
    eligible = set(edges)
    at: dict[int, list[int]] = {}
    degree: dict[int, int] = {}
    for eid in sorted(graph.edges):
        e = graph.edges[eid]
        for n in {e.from_node, e.to_node}:
            degree[n] = degree.get(n, 0) + 1
            if eid in eligible:
                at.setdefault(n, []).append(eid)
    links: dict[int, list[tuple[int, int]]] = {}
    for node in sorted(at):
        arms = at[node]
        if len(arms) < 2:
            continue
        if degree[node] == 2:
            links[node] = [(arms[0], arms[1])]
            continue
        cands = []
        for i in range(len(arms)):
            for j in range(i + 1, len(arms)):
                d = _deflection(_away(graph, arms[i], node), _away(graph, arms[j], node))
                if d < max_deflection:
                    cands.append((d, arms[i], arms[j]))
        used: set[int] = set()
        for _, a, b in sorted(cands):
            if a not in used and b not in used:
                used |= {a, b}
                links.setdefault(node, []).append((a, b))
    return links


def strokes(graph: RoadGraph, edges: Iterable[int], max_deflection: float) -> list[list[int]]:
    """Edge ids per stroke, each sorted; strokes sorted by their first edge."""
    edges = sorted(set(edges))
    parent = {e: e for e in edges}

    def find(e: int) -> int:
        while parent[e] != e:
            parent[e] = parent[parent[e]]
            e = parent[e]
        return e

    for pairs in stroke_links(graph, edges, max_deflection).values():
        for a, b in pairs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for e in edges:
        groups.setdefault(find(e), []).append(e)
    return sorted(groups.values())


def _orientation(graph: RoadGraph, stroke: list[int],
                 links: dict[int, list[tuple[int, int]]]) -> dict[int, bool]:
    """Whether each edge of a stroke runs along the stroke's direction (from_node first)."""
    members = set(stroke)
    nbrs: dict[int, list[tuple[int, int]]] = {e: [] for e in stroke}  # edge -> [(other edge, node)]
    for node, pairs in links.items():
        for a, b in pairs:
            if a in members:
                nbrs[a].append((b, node))
                nbrs[b].append((a, node))
    along = {stroke[0]: True}
    todo = [stroke[0]]
    while todo:
        e = todo.pop()
        edge = graph.edges[e]
        head = edge.to_node if along[e] else edge.from_node  # where e leaves along the stroke
        for other, node in nbrs[e]:
            if other in along:
                continue
            o = graph.edges[other]
            # other continues from node: it runs along if it starts there, given e ends there
            leaves = o.from_node == node
            along[other] = leaves if node == head else not leaves
            todo.append(other)
    return along


def assign_hierarchy(graph: RoadGraph, params: HierarchyParams, seed: int,
                     fixed: Iterable[int] = ()) -> dict[int, str]:
    """Set class, lanes and speed of every edge not in fixed; returns edge -> level."""
    fixed = set(fixed)
    free = [eid for eid in sorted(graph.edges) if eid not in fixed]
    if not free:
        return {}
    max_def = math.radians(params.max_deflection_deg)
    links = stroke_links(graph, free, max_def)
    groups = strokes(graph, free, max_def)
    g = nx.Graph()
    for eid in sorted(graph.edges):
        e = graph.edges[eid]
        g.add_edge(e.from_node, e.to_node, id=eid, length=graph.edge_length(eid))
    k = params.samples if params.samples and params.samples < g.number_of_nodes() else None
    between = nx.edge_betweenness_centrality(g, k=k, weight="length", seed=seed)
    by_edge = {g.edges[uv]["id"]: b for uv, b in between.items()}
    length = {eid: graph.edge_length(eid) for eid in free}
    scored = []
    for stroke in groups:
        total = sum(length[e] for e in stroke)
        score = sum(length[e] * by_edge.get(e, 0.0) for e in stroke)
        scored.append((-score, -total, stroke[0], stroke, total))
    scored.sort()
    all_length = sum(length.values()) or 1.0
    bounds = ((ARTERIAL, params.arterial_share),
              (COLLECTOR, params.arterial_share + params.collector_share))
    after_highway = _highway_continuations(graph, fixed, free, max_def)
    out: dict[int, str] = {}
    cum = 0.0
    for _, _, _, stroke, total in scored:
        level = next((name for name, share in bounds if cum < share * all_length - 1e-9), LOCAL)
        if after_highway.intersection(stroke):
            level = ARTERIAL
        cum += total
        spec = params.levels[level]
        along = _orientation(graph, stroke, links) if spec.lanes[0] != spec.lanes[1] else {}
        for eid in stroke:
            e = graph.edges[eid]
            e.road_class = spec.road_class
            e.speed_limit = spec.speed
            if e.is_one_way:
                fwd, bwd = sum(spec.lanes), 0
                if e.lanes_forward == 0:
                    fwd, bwd = bwd, fwd
            elif along.get(eid, True):
                fwd, bwd = spec.lanes
            else:
                bwd, fwd = spec.lanes
            e.lanes_forward, e.lanes_backward = fwd, bwd
            out[eid] = level
    counts = {lv: sum(1 for v in out.values() if v == lv) for lv in LEVELS}
    log.debug("hierarchy: %d strokes, edges per level %s", len(groups), counts)
    return out


def _highway_continuations(graph: RoadGraph, fixed: set[int], free: Iterable[int],
                           max_deflection: float) -> set[int]:
    """Free edges running straight on (within max_deflection) from a fixed highway
    edge, straighter than the highway itself goes on at that node (if it does), and
    the one other road where a highway ends at a two-road node."""
    free = set(free)
    at: dict[int, list[int]] = {}
    for eid, e in graph.edges.items():
        for n in {e.from_node, e.to_node}:
            at.setdefault(n, []).append(eid)
    out = set()
    for node, arms in at.items():
        highways = [h for h in arms if h in fixed and graph.edges[h].road_class == "highway"]
        for h in highways:
            u = _away(graph, h, node)
            if len(arms) == 2 and len(highways) == 1:  # a bend: the other road is its continuation
                out |= {f for f in arms if f in free}
                continue
            on = min((_deflection(u, _away(graph, o, node)) for o in highways if o != h), default=math.pi)
            out |= {f for f in arms if f in free and _deflection(u, _away(graph, f, node)) < min(max_deflection, on)}
    return out
