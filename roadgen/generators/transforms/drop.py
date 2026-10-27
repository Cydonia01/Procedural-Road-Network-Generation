"""Drop road segments at random without stranding any traffic."""

from __future__ import annotations

import logging
from typing import Sequence

import networkx as nx
import numpy as np

from roadgen.graph.model import RoadClass, RoadGraph

log = logging.getLogger(__name__)


def drop_edges(graph: RoadGraph, prob: float, rng: np.random.Generator,
               road_classes: Sequence[RoadClass] = ("street",)) -> int:
    """Drop edges of road_classes, each with probability prob; returns how many.

    An edge is dropped only if every travel direction it carried is still
    possible on other roads, so connectivity (and, for one-way roads, strong
    connectivity between its ends) is kept. Candidates are drawn up front in
    edge-id order, so the outcome depends only on rng. Nodes left without
    roads are removed.
    """
    candidates = sorted(eid for eid, e in graph.edges.items() if e.road_class in road_classes)
    if prob == 0.0 or not candidates:
        return 0
    g = nx.MultiDiGraph()
    g.add_nodes_from(graph.nodes)
    for e in graph.edges.values():
        for u, v, lanes in ((e.from_node, e.to_node, e.lanes_forward), (e.to_node, e.from_node, e.lanes_backward)):
            if lanes > 0:
                g.add_edge(u, v, key=e.id)
    draws = rng.random(len(candidates))
    dropped = 0
    for eid, r in zip(candidates, draws):
        if r >= prob:
            continue
        e = graph.edges[eid]
        arcs = [(u, v) for u, v, lanes in (
            (e.from_node, e.to_node, e.lanes_forward), (e.to_node, e.from_node, e.lanes_backward)
        ) if lanes > 0]
        for u, v in arcs:
            g.remove_edge(u, v, key=eid)
        if all(nx.has_path(g, u, v) for u, v in arcs):
            del graph.edges[eid]
            dropped += 1
        else:
            for u, v in arcs:
                g.add_edge(u, v, key=eid)
    used = {n for e in graph.edges.values() for n in (e.from_node, e.to_node)}
    for nid in [n for n in graph.nodes if n not in used]:
        del graph.nodes[nid]
    log.debug("dropped %d of %d candidate edges", dropped, len(candidates))
    return dropped
