"""Post-generation repair: every edge long enough for the junctions at its ends.

A generator places nodes without knowing how much road each junction will
take: the OpenDRIVE builder trims a road back to every junction it ends at
(roadgen.geometry.junction_space). An edge between two junctions whose trims
add up to more than its length cannot be built, and one that keeps less than
MIN_ROAD_LENGTH between them gives a stub road that cannot change from one
junction's slope and banking to the other's (stage C: found on pattern-field
cities on hilly maps). This pass, run at the end of
a generator, fixes exactly those edges and touches nothing else, so a network
that could be built before is returned unchanged:

1. collapse the edge: its two nodes merge into one at its midpoint (the lower
   id survives), if the merged node keeps at most max_degree roads, no two
   of them become duplicates, and its arms stay at least MIN_ARM_ANGLE_DEG
   apart;
2. otherwise remove the edge, if its two nodes stay connected;
3. otherwise leave it (the builder will report it).

Repeated in edge-id order until no edge is too short. Deterministic.
"""

from __future__ import annotations

import logging
from typing import Optional

import networkx as nx

from roadgen.geometry.chains import find_chains, junction_nodes
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN, node_trims
from roadgen.graph.model import RoadGraph

log = logging.getLogger(__name__)

MAX_DEGREE = 5  # roads a collapsed node may join (Teng & Bidarra's vertex-degree rule)
LENGTH_EPS = 1e-9
MIN_ROAD_LENGTH = 5.0  # [m] least road an edge between two junctions keeps after both trims


def short_edges(graph: RoadGraph, design: DesignParams = DesignParams(),
                junction_margin: float = DEFAULT_MARGIN) -> list[int]:
    """Edges the builder would reject as too short for their junction trims, by id."""
    chains = find_chains(graph, design.max_merge_deflection_deg)
    trims = {}
    for n in junction_nodes(graph, chains):
        try:
            trims[n] = node_trims(graph, n, junction_margin)
        except ValueError:
            trims[n] = {}  # arms too close: not a length problem
    out = set()
    for c in chains:
        first, last = c.edges[0].edge_id, c.edges[-1].edge_id
        t0 = trims.get(c.start, {}).get(first, 0.0)
        t1 = trims.get(c.end, {}).get(last, 0.0)
        if first == last:
            if t0 + t1 >= graph.edge_length(first) - LENGTH_EPS:
                out.add(first)
            continue
        if t0 >= graph.edge_length(first) - LENGTH_EPS:
            out.add(first)
        if t1 >= graph.edge_length(last) - LENGTH_EPS:
            out.add(last)
    return sorted(out)


def stub_roads(graph: RoadGraph, design: DesignParams = DesignParams(),
                junction_margin: float = DEFAULT_MARGIN) -> list[int]:
    """Single-edge roads between two junctions that keep less than MIN_ROAD_LENGTH
    after both trims, by edge id. Uses the builder's own alignment, which also
    keeps bends too tight for their inner lane as junctions."""
    try:
        na = network_alignment(graph, design, junction_margin)
    except ValueError:
        return []  # too-short edges first (short_edges)
    junctions = set(na.junctions)
    return sorted(ca.chain.edge_ids[0] for ca in na.chains
                  if len(ca.chain.edges) == 1 and ca.chain.start in junctions
                  and ca.chain.end in junctions and ca.length < MIN_ROAD_LENGTH)


def repair_short_edges(graph: RoadGraph, design: DesignParams = DesignParams(),
                       junction_margin: float = DEFAULT_MARGIN, max_degree: int = MAX_DEGREE) -> int:
    """Fix too-short edges in place; return how many were collapsed or removed."""
    fixed = 0
    tried: set[int] = set()
    while True:
        todo = [e for e in short_edges(graph, design, junction_margin) if e not in tried]
        if not todo:  # only once every edge fits: the alignment needs that
            todo = [e for e in stub_roads(graph, design, junction_margin) if e not in tried]
        if not todo:
            break
        eid = todo[0]
        if _collapse(graph, eid, junction_margin, max_degree) or _remove(graph, eid):
            fixed += 1
        else:
            tried.add(eid)
            log.warning("repair: edge %d is too short for its junctions and cannot be fixed", eid)
    if fixed:
        log.info("repair: fixed %d edge(s) too short for their junctions", fixed)
    return fixed


def _collapse(graph: RoadGraph, eid: int, margin: float, max_degree: int) -> bool:
    e = graph.edges[eid]
    keep, gone = sorted((e.from_node, e.to_node))
    others = [x for x in graph.incident_edges(gone) if x.id != eid]
    kept_far = {x.to_node if x.from_node == keep else x.from_node
                for x in graph.incident_edges(keep) if x.id != eid}
    gone_far = {x.to_node if x.from_node == gone else x.from_node for x in others}
    if kept_far & gone_far or keep in gone_far:
        return False  # two roads would join the same pair of nodes
    if len(kept_far) + len(gone_far) > max_degree:
        return False
    a, b = graph.nodes[keep], graph.nodes[gone]
    old = (a.x, a.y)
    snapshot = {x.id: (x.from_node, x.to_node) for x in others}
    a.x, a.y = (a.x + b.x) / 2, (a.y + b.y) / 2
    del graph.edges[eid]
    for x in others:
        if x.from_node == gone:
            x.from_node = keep
        else:
            x.to_node = keep
    try:
        node_trims(graph, keep, margin)  # raises if two arms are now too close
        ok = all(graph.edge_length(x.id) > LENGTH_EPS for x in graph.incident_edges(keep))
    except ValueError:
        ok = False
    if not ok:  # undo
        a.x, a.y = old
        for x in others:
            x.from_node, x.to_node = snapshot[x.id]
        graph.edges[eid] = e
        graph.edges = dict(sorted(graph.edges.items()))
        return False
    del graph.nodes[gone]
    _drop_bridge_ref(graph, eid)
    log.debug("repair: collapsed edge %d (node %d into %d)", eid, gone, keep)
    return True


def _remove(graph: RoadGraph, eid: int) -> bool:
    g = graph.to_networkx()
    e = graph.edges[eid]
    g.remove_edge(e.from_node, e.to_node)
    if not nx.has_path(g, e.from_node, e.to_node):
        return False  # the edge is a bridge of the network: removing it would split it
    del graph.edges[eid]
    for n in (e.from_node, e.to_node):
        if graph.degree(n) == 0:
            del graph.nodes[n]
    _drop_bridge_ref(graph, eid)
    log.debug("repair: removed edge %d", eid)
    return True


def _drop_bridge_ref(graph: RoadGraph, eid: int) -> None:
    bridges: Optional[list] = graph.metadata.get("bridges")
    if bridges and eid in bridges:
        graph.metadata["bridges"] = [b for b in bridges if b != eid]
