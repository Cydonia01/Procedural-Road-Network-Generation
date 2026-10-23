"""Chains: runs of edges through nodes that are road bends, not junctions.

A node with exactly two roads is *mergeable* when the road simply continues
through it: it is not forced to be a junction (Node.kind != "junction"), the
road turns by at most max_merge_deflection_deg there, and every travel
direction present on one side is present on the other (so no lane starts or
ends in the middle of a road). A chain is a maximal path of edges whose inner
nodes are all mergeable; the OpenDRIVE builder makes one road per chain.

A ring of mergeable nodes with no other way in is cut at its lowest node id,
which stays a (two-road) junction, so every chain has two ends. A chain may
not start and end at the same node either (esmini confuses the two ends of a
road that joins one junction twice): its middle inner node then stays a
junction too, splitting it into two roads.

Orientation: a one-way chain runs in its travel direction, so all its lanes are
on the right of the reference line; a two-way chain runs along its lowest-id
edge, so a single-edge road keeps its edge's direction. ChainEdge.forward tells whether an edge's from_node -> to_node
runs along the chain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from roadgen.config import DEFAULTS, section
from roadgen.geometry.planar import wrap_angle
from roadgen.graph.model import Edge, RoadGraph

_D = section(DEFAULTS, "design")


@dataclass(frozen=True)
class ChainEdge:
    edge_id: int
    forward: bool  # edge from_node -> to_node runs along the chain

    def lanes(self, edge: Edge) -> tuple[int, int]:
        """(right, left) lanes of the edge seen along the chain."""
        if self.forward:
            return edge.lanes_forward, edge.lanes_backward
        return edge.lanes_backward, edge.lanes_forward


@dataclass(frozen=True)
class Chain:
    nodes: tuple[int, ...]  # len(edges) + 1 node ids along the chain
    edges: tuple[ChainEdge, ...]

    @property
    def start(self) -> int:
        return self.nodes[0]

    @property
    def end(self) -> int:
        return self.nodes[-1]

    @property
    def inner(self) -> tuple[int, ...]:
        return self.nodes[1:-1]

    @property
    def edge_ids(self) -> tuple[int, ...]:
        return tuple(ce.edge_id for ce in self.edges)


def _other(edge: Edge, node_id: int) -> int:
    return edge.to_node if edge.from_node == node_id else edge.from_node


def deflection(graph: RoadGraph, node_id: int, a: Edge, b: Edge) -> float:
    """Signed heading change [rad] of travel from edge a through node_id into edge b."""
    n = graph.nodes[node_id]
    p, q = graph.nodes[_other(a, node_id)], graph.nodes[_other(b, node_id)]
    h_in = math.atan2(n.y - p.y, n.x - p.x)
    h_out = math.atan2(q.y - n.y, q.x - n.x)
    return wrap_angle(h_out - h_in)


def _directions(edge: Edge, node_id: int) -> tuple[bool, bool]:
    """(has lanes towards node_id, has lanes away from node_id)."""
    into = edge.lanes_forward if edge.to_node == node_id else edge.lanes_backward
    away = edge.lanes_backward if edge.to_node == node_id else edge.lanes_forward
    return into > 0, away > 0


def is_mergeable(graph: RoadGraph, node_id: int,
                 max_deflection_deg: float = _D["max_merge_deflection_deg"]) -> bool:
    node = graph.nodes[node_id]
    if node.kind == "junction":
        return False
    edges = graph.incident_edges(node_id)
    if len(edges) != 2:
        return False
    a, b = edges
    if abs(deflection(graph, node_id, a, b)) > math.radians(max_deflection_deg):
        return False
    # Traffic arriving on one side must be able to leave on the other, and vice versa.
    a_in, a_out = _directions(a, node_id)
    b_in, b_out = _directions(b, node_id)
    return a_in == b_out and a_out == b_in


def _walk(graph: RoadGraph, start: int, edge: Edge, mergeable: set[int]) -> tuple[list[int], list[Edge]]:
    nodes, edges = [start], [edge]
    node = _other(edge, start)
    while node in mergeable and node != start:
        nodes.append(node)
        nxt = next(e for e in graph.incident_edges(node) if e.id != edges[-1].id)
        edges.append(nxt)
        node = _other(nxt, node)
    nodes.append(node)
    return nodes, edges


def _orient(nodes: list[int], edges: list[Edge]) -> Chain:
    def along(ns: list[int], es: list[Edge]) -> tuple[ChainEdge, ...]:
        return tuple(ChainEdge(e.id, e.from_node == n) for n, e in zip(ns, es))

    chain_edges = along(nodes, edges)
    one_way = next((e for e in edges if e.is_one_way), None)
    if one_way is not None:
        ce = chain_edges[edges.index(one_way)]
        reverse = (one_way.lanes_forward == 0) == ce.forward  # travel runs against the chain
    else:  # along the lowest-id edge, so a single-edge road keeps its edge's direction
        first = min(chain_edges, key=lambda ce: ce.edge_id)
        reverse = not first.forward
    if reverse:
        nodes, edges = nodes[::-1], edges[::-1]
        chain_edges = along(nodes, edges)
    return Chain(tuple(nodes), chain_edges)


def find_chains(graph: RoadGraph,
                max_deflection_deg: float = _D["max_merge_deflection_deg"]) -> list[Chain]:
    """All chains of the graph, ordered by their smallest edge id; every edge is in one."""
    mergeable = {n for n in graph.nodes if is_mergeable(graph, n, max_deflection_deg)}
    while True:
        chains = _chains(graph, mergeable)
        loops = [c for c in chains if c.start == c.end]
        if not loops:
            return chains
        for c in loops:
            mergeable.discard(c.nodes[len(c.nodes) // 2])


def _chains(graph: RoadGraph, mergeable: set[int]) -> list[Chain]:
    """Chains for a given set of mergeable nodes (rings cut at their lowest node)."""
    mergeable = set(mergeable)
    chains: list[Chain] = []
    seen: set[int] = set()

    def collect(starts: list[int]) -> None:
        for n in starts:
            for e in graph.incident_edges(n):
                if e.id in seen:
                    continue
                nodes, edges = _walk(graph, n, e, mergeable)
                seen.update(x.id for x in edges)
                chains.append(_orient(nodes, edges))

    collect(sorted(n for n in graph.nodes if n not in mergeable and graph.degree(n) > 0))
    # Whatever is left are rings of mergeable nodes: cut each at its lowest node.
    while len(seen) < len(graph.edges):
        cut = min(n for e in graph.edges.values() if e.id not in seen for n in (e.from_node, e.to_node))
        mergeable.discard(cut)
        collect([cut])
    return sorted(chains, key=lambda c: min(c.edge_ids))


def junction_nodes(graph: RoadGraph, chains: list[Chain]) -> list[int]:
    """Nodes that become OpenDRIVE junctions: chain ends with two or more roads."""
    ends = {n for c in chains for n in (c.start, c.end)}
    return sorted(n for n in ends if graph.degree(n) >= 2)
