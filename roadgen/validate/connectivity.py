"""Traffic connectivity of a RoadGraph: can every car get everywhere?

Vertices of the traffic graph are directed traversals (edge, direction) that
have lanes in that direction. A traversal arriving at a node continues onto
any other edge leaving that node (no U-turns), which mirrors the movements
the OpenDRIVE builder creates.

Errors are:
- traps: traffic arrives on an edge and has no way out (dead ends, one-way
  sinks), so vehicles get stuck;
- unreachable roads: traffic leaves on an edge that nothing ever feeds;
- the node-level lane-direction graph not being strongly connected.

Traversal groups that differ only by direction of travel (e.g. clockwise vs.
counter-clockwise on a pure loop, where reversing needs a U-turn) strand no
vehicle, so they are logged but not reported as errors.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import networkx as nx

from roadgen.graph.model import RoadGraph

log = logging.getLogger(__name__)

Traversal = tuple[int, bool]  # (edge id, True = from_node -> to_node)


def lane_digraph(graph: RoadGraph) -> nx.DiGraph:
    """Node-level directed graph: u -> v when some lane drives from u to v."""
    g = nx.DiGraph()
    g.add_nodes_from(graph.nodes)
    for e in graph.edges.values():
        if e.lanes_forward > 0:
            g.add_edge(e.from_node, e.to_node)
        if e.lanes_backward > 0:
            g.add_edge(e.to_node, e.from_node)
    return g


def traffic_digraph(graph: RoadGraph) -> nx.DiGraph:
    """Traversal-level directed graph without U-turns (see module docstring)."""
    g = nx.DiGraph()
    leaving: dict[int, list[Traversal]] = defaultdict(list)
    for e in graph.edges.values():
        for forward, lanes, tail in (
            (True, e.lanes_forward, e.from_node),
            (False, e.lanes_backward, e.to_node),
        ):
            if lanes > 0:
                g.add_node((e.id, forward))
                leaving[tail].append((e.id, forward))
    for t in list(g.nodes):
        edge = graph.edges[t[0]]
        head = edge.to_node if t[1] else edge.from_node
        for nxt in leaving[head]:
            if nxt[0] != t[0]:
                g.add_edge(t, nxt)
    return g


def _head(graph: RoadGraph, t: Traversal) -> int:
    e = graph.edges[t[0]]
    return e.to_node if t[1] else e.from_node


def _tail(graph: RoadGraph, t: Traversal) -> int:
    e = graph.edges[t[0]]
    return e.from_node if t[1] else e.to_node


def check_traffic_connectivity(graph: RoadGraph) -> list[str]:
    """Return problems as messages; empty means no traps and full reachability."""
    g = traffic_digraph(graph)
    if g.number_of_nodes() == 0 or nx.is_strongly_connected(g):
        return []

    traps: dict[int, set[int]] = defaultdict(set)  # node -> edges arriving with no exit
    unreachable: dict[int, set[int]] = defaultdict(set)  # node -> edges leaving, never fed
    for t in g.nodes:
        if g.out_degree(t) == 0:
            traps[_head(graph, t)].add(t[0])
        if g.in_degree(t) == 0:
            unreachable[_tail(graph, t)].add(t[0])

    errors = [
        f"node {n}: trap, traffic arriving on edge(s) {sorted(es)} has no way out"
        for n, es in sorted(traps.items())
    ]
    errors += [
        f"node {n}: no way in for traffic leaving on edge(s) {sorted(es)}"
        for n, es in sorted(unreachable.items())
    ]

    nodes = lane_digraph(graph)
    used = nodes.subgraph(n for n in nodes if nodes.degree(n) > 0)
    if used.number_of_nodes() and not nx.is_strongly_connected(used):
        comps = sorted(nx.strongly_connected_components(used), key=len, reverse=True)
        outside = sorted(set(used) - comps[0])
        errors.append(
            f"lane-direction graph is not strongly connected: {len(comps)} components; "
            f"nodes outside the main one: {outside}"
        )

    groups = nx.number_strongly_connected_components(g)
    if not errors:
        log.info(
            "traffic graph has %d direction-of-travel groups (no traps; reversing "
            "direction would need a U-turn somewhere)", groups,
        )
    for err in errors:
        log.debug("connectivity: %s", err)
    return errors
