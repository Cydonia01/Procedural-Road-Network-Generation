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
   of them become duplicates, and its arms, and those of the nodes next to
   it (whose arms turn with the moved node), stay at least MIN_ARM_ANGLE_DEG
   apart;
2. otherwise remove the edge, if its two nodes stay connected;
3. otherwise leave it (the builder will report it).

Repeated in edge-id order until no edge is too short. Deterministic.

With a cross-section (stage B) the trims are those of roads with their side
lanes and curb returns; the pipeline runs this pass again with it before the
build (cli.fit_graph), so generators stay unaware of cross-sections. A left-turn
pocket widens its arm and so the trims of the arms beside it: before an edge is
repaired, the pockets at its two junctions are left out, one at a time, until it
fits (graph metadata "no_pockets", [node, edge] pairs; real designs leave them out
on short blocks too). An edge that is too short only because of the side lanes
(it fits with the driving lanes alone) is never collapsed or removed (prompt 13):
road ends at its junctions lose side lanes instead, one at a time until it fits
(its own ends first, then the other arms there): first only their bike lanes end
before the junction, the curb and sidewalk going on round the corner (graph metadata
"no_bike_lanes", cross_section.bike_ends); if that is not enough the ends become bare,
their sidewalks, shoulders, parking and bike lanes ending before the junction (graph
metadata "no_side_lanes", cross_section.bare_ends). If even that is not enough and one end is a two-road bend
(which the side lanes turned into a junction: its curve no longer clears the
sidewalks around), the bend is straightened: its two edges, the same road, become
one, and the bend node goes (metadata "straightened_bends"); the road stays.
Otherwise it is left for the builder to report. Only edges too short with the
driving lanes alone are collapsed or removed.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import networkx as nx
import numpy as np

from roadgen.geometry.alignment import ShortEdgeError, network_alignment
from roadgen.geometry.chains import find_chains, junction_nodes
from roadgen.geometry.cross_section import (
    BARE_DROP,
    CrossSectionParams,
    edge_cross_section,
    loop_edges,
    pocket_ends,
)
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN, node_trims
from roadgen.graph.model import Edge, RoadGraph

log = logging.getLogger(__name__)

MAX_DEGREE = 5  # roads a collapsed node may join (Teng & Bidarra's vertex-degree rule)
LENGTH_EPS = 1e-9
MIN_ROAD_LENGTH = 5.0  # [m] least road an edge between two junctions keeps after both trims


def short_edges(graph: RoadGraph, design: DesignParams = DesignParams(),
                junction_margin: float = DEFAULT_MARGIN,
                cross_section: Optional[CrossSectionParams] = None) -> list[int]:
    """Edges the builder would reject as too short for their junction trims, by id."""
    chains = find_chains(graph, design.max_merge_deflection_deg)
    trims = {}
    for n in junction_nodes(graph, chains):
        try:
            trims[n] = node_trims(graph, n, junction_margin, cross_section)
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
                junction_margin: float = DEFAULT_MARGIN,
                cross_section: Optional[CrossSectionParams] = None, dead_ends: bool = False) -> list[int]:
    """Single-edge roads between two junctions that keep less than MIN_ROAD_LENGTH
    after both trims, by edge id. Uses the builder's own alignment, which also
    keeps bends too tight for their inner lane as junctions: an edge too short
    for such a junction is returned alone. dead_ends: also single-edge roads from
    a junction to a dead end (the wider trims of cross-sections can leave them
    under a metre)."""
    return _stub_scan(graph, design, junction_margin, cross_section, dead_ends)[0]


def _stub_scan(graph: RoadGraph, design: DesignParams, junction_margin: float,
               cross_section: Optional[CrossSectionParams], dead_ends: bool) -> tuple[list[int], frozenset[int]]:
    """(stub_roads, the alignment's junction nodes; empty if it could not be built)."""
    try:
        na = network_alignment(graph, design, junction_margin, cross_section=cross_section)
    except ShortEdgeError as err:
        return [err.edge_id], frozenset()
    except ValueError:
        return [], frozenset()  # too-short edges first (short_edges)
    junctions = set(na.junctions)
    ends_ok = junctions | ({n for n in graph.nodes if graph.degree(n) == 1} if dead_ends else set())
    return sorted(ca.chain.edge_ids[0] for ca in na.chains
                  if len(ca.chain.edges) == 1 and ca.length < MIN_ROAD_LENGTH
                  and {ca.chain.start, ca.chain.end} <= ends_ok
                  and (ca.chain.start in junctions or ca.chain.end in junctions)), frozenset(junctions)


def _too_short_in_alignment(graph: RoadGraph, design: DesignParams, junction_margin: float,
                            cross_section: Optional[CrossSectionParams]) -> Optional[int]:
    """The edge the builder's alignment finds too short for a junction (a bend it keeps as
    one included), or None."""
    try:
        network_alignment(graph, design, junction_margin, cross_section=cross_section)
    except ShortEdgeError as err:
        return err.edge_id
    except ValueError:
        return None
    return None


def _stub_here(graph: RoadGraph, eid: int, junctions: frozenset[int], junction_margin: float,
               cross_section: Optional[CrossSectionParams]) -> bool:
    """Whether a stub found by _stub_scan still is one, without a new alignment: a
    single-edge road is straight, so what it keeps is its length less its trims at the
    junctions (B9: the trials of the HD repair aligned the whole network each time)."""
    e = graph.edges[eid]
    try:
        trims = sum(node_trims(graph, n, junction_margin, cross_section).get(eid, 0.0)
                    for n in (e.from_node, e.to_node) if n in junctions)
    except ValueError:
        return True
    return graph.edge_length(eid) - trims < MIN_ROAD_LENGTH


def repair_short_edges(graph: RoadGraph, design: DesignParams = DesignParams(),
                       junction_margin: float = DEFAULT_MARGIN, max_degree: int = MAX_DEGREE,
                       cross_section: Optional[CrossSectionParams] = None,
                       keep_stubs: frozenset[int] = frozenset(), dead_ends: bool = False) -> int:
    """Fix too-short edges in place; return how many were collapsed or removed.
    keep_stubs: stub roads to leave alone (they build, only too short to change
    slope between their junctions); dead_ends: dead-end roads count as stubs too."""
    fixed = dropped = bared = dissolved = 0
    tried: set[int] = set()
    while True:
        todo = [e for e in short_edges(graph, design, junction_margin, cross_section) if e not in tried]
        check = lambda eid, cs=cross_section: eid in short_edges(graph, design, junction_margin, cs)  # noqa: E731
        stubs = not todo
        if not todo:  # only once every edge fits: the alignment needs that
            found, junctions = _stub_scan(graph, design, junction_margin, cross_section, dead_ends)
            if found and not junctions:
                # The alignment found an edge too short for a bend it keeps as a junction:
                # not a stub, so keep_stubs does not apply, and whether the side lanes made
                # it so is checked like a too-short edge (B9: a stub without them became too
                # short with them).
                stubs = False
                todo = [e for e in found if e not in tried]
                check = lambda eid, cs=cross_section: _too_short_in_alignment(  # noqa: E731
                    graph, design, junction_margin, cs) == eid
            else:
                todo = [e for e in found if e not in tried and e not in keep_stubs]
                # The trials re-check one edge from its trims; the next scan confirms.
                check = lambda eid, cs=cross_section, j=junctions: _stub_here(  # noqa: E731
                    graph, eid, j, junction_margin, cs)
        if not todo:
            break
        # Pockets left out and bare road ends change only trims, which check sees: work
        # through all edges of one scan. A collapse or removal moves the network: rescan.
        for k, eid in enumerate(todo):
            # The first is the scan's own; later ones may have been fixed meanwhile by an
            # earlier edge's pockets or bare ends. (Each pass changes something or adds
            # to tried, so the loop ends.)
            if k and (eid not in graph.edges or not check(eid)):
                continue
            if cross_section is not None:
                n = _drop_pockets(graph, eid, lambda eid=eid: check(eid), cross_section)
                if n:
                    dropped += n
                    continue
                # Too short only with the side lanes: HD made it so. A stub here is one: the
                # stubs the network had without them are keep_stubs (fit_cross_sections).
                if stubs or not check(eid, None):
                    n = _bare(graph, eid, lambda eid=eid: check(eid), cross_section)
                    if not n:  # pockets and side lanes may both be needed (two highways at an acute angle)
                        n, out = _bare_without_pockets(graph, eid, lambda eid=eid: check(eid), cross_section)
                        dropped += out
                    if n:
                        bared += n
                        continue
                        # A two-road bend the side lanes made a junction (its curve no longer clears
                    # the sidewalks around): the road stays, its bend vertex goes (B9).
                    if _dissolve_bend(graph, eid, junction_margin, cross_section):
                        dissolved += 1
                        break
                    tried.add(eid)  # never removed for its side lanes (prompt 13): the builder reports it
                    log.warning("repair: edge %d is too short for its junctions even with bare road ends", eid)
                    continue
            if _collapse(graph, eid, junction_margin, max_degree, cross_section) or _remove(graph, eid):
                fixed += 1
            else:
                tried.add(eid)
                log.warning("repair: edge %d is too short for its junctions and cannot be fixed", eid)
            break
    if dropped:
        log.info("repair: left out %d turn pocket(s) on blocks too short for them", dropped)
    if bared:
        bikes = len(graph.metadata.get("no_bike_lanes", []))
        log.info("repair: %d road end(s) bare (side lanes end before the junction), %d with only the bike "
                 "lanes ending, on blocks too short for them", bared - bikes, bikes)
    if dissolved:
        log.info("repair: %d two-road bend(s) straightened (their two edges made one)", dissolved)
    if fixed:
        log.info("repair: fixed %d edge(s) too short for their junctions", fixed)
    return fixed


def fit_cross_sections(graph: RoadGraph, design: DesignParams, junction_margin: float,
                       cross_section: CrossSectionParams) -> RoadGraph:
    """The HD-map pass before the build: repair what the roads' side lanes (and turn
    pockets) make too short, in place. It never joins more roads at a node than the
    generator already does (at least MAX_DEGREE) and leaves the stub roads the network
    already has (they build); records the repair in the graph metadata ("fit")."""
    max_degree = max([MAX_DEGREE] + [graph.degree(n) for n in graph.nodes])
    known = frozenset(stub_roads(graph, design, junction_margin, dead_ends=True))
    n = repair_short_edges(graph, design, junction_margin, max_degree, cross_section, known, dead_ends=True)
    if n or any(graph.metadata.get(k) for k in ("no_pockets", "no_bike_lanes", "no_side_lanes",
                                                "straightened_bends")):
        graph.metadata["fit"] = {"repaired_edges": n, "profile": cross_section.profile,
                                 "dropped_pockets": len(graph.metadata.get("no_pockets", [])),
                                 "bare_ends": len(graph.metadata.get("no_side_lanes", [])),
                                 "bike_lane_ends": len(graph.metadata.get("no_bike_lanes", [])),
                                 "straightened_bends": len(graph.metadata.get("straightened_bends", []))}
    return graph


def _drop_pockets(graph: RoadGraph, eid: int, too_short: Callable[[], bool],
                  cross_section: CrossSectionParams) -> int:
    """Leave out the left-turn pockets at the junctions of a too-short edge (see
    _leave_out); return how many stay out."""
    if cross_section.turn_lanes is None:
        return 0
    e = graph.edges[eid]
    pockets = [[n, p] for n in sorted({e.from_node, e.to_node})
               for p in sorted(pocket_ends(graph, n, cross_section.turn_lanes))]
    return _leave_out(graph, "no_pockets", pockets, too_short)


def _bare(graph: RoadGraph, eid: int, too_short: Callable[[], bool], cross_section: CrossSectionParams) -> int:
    """Make road ends bare at the junctions of an edge its side lanes make too short (see
    _leave_out): the edge's own ends first, then the other arms of those junctions (an
    arm at an acute angle widens the edge's trim); only ends with side lanes to leave
    out. Return how many stay bare."""
    e = graph.edges[eid]
    loops = loop_edges(graph)
    nodes = sorted({e.from_node, e.to_node})

    def has_side_lanes(x: int) -> bool:
        xs = edge_cross_section(graph.edges[x], cross_section, x in loops)
        return any(s.kind in BARE_DROP for s in xs.right + xs.left)

    ends = [[n, eid] for n in nodes]
    ends += [[n, x.id] for n in nodes for x in sorted(graph.incident_edges(n), key=lambda x: x.id) if x.id != eid]
    ends = [pair for pair in ends if has_side_lanes(pair[1]) and pair not in graph.metadata.get("no_side_lanes", [])]
    # First only the bike lanes (sidewalks go on round the corner), then whole bare ends.
    bikes = [pair for pair in ends if pair not in graph.metadata.get("no_bike_lanes", []) and any(
        s.kind == "biking" for s in edge_cross_section(graph.edges[pair[1]], cross_section, pair[1] in loops).right)]
    n = _leave_out(graph, "no_bike_lanes", bikes, too_short) if bikes else 0
    return n or _leave_out(graph, "no_side_lanes", ends, too_short)


def _bare_without_pockets(graph: RoadGraph, eid: int, too_short: Callable[[], bool],
                          cross_section: CrossSectionParams) -> tuple[int, int]:
    """_bare with every pocket at the edge's junctions left out, then the pockets it fits
    without put back; (bare ends, pockets left out), or (0, 0) with nothing changed."""
    if cross_section.turn_lanes is None:
        return 0, 0
    e = graph.edges[eid]
    pockets = [[n, p] for n in sorted({e.from_node, e.to_node})
               for p in sorted(pocket_ends(graph, n, cross_section.turn_lanes))]
    if not pockets:
        return 0, 0
    before = list(graph.metadata.get("no_pockets", []))
    graph.metadata["no_pockets"] = before + pockets
    n = _bare(graph, eid, too_short, cross_section)
    if not n:
        if before:
            graph.metadata["no_pockets"] = before
        else:
            graph.metadata.pop("no_pockets", None)
        return 0, 0
    out = list(pockets)
    for pair in pockets:  # those it fits without go back
        graph.metadata["no_pockets"] = before + [x for x in out if x != pair]
        if not too_short():
            out.remove(pair)
    graph.metadata["no_pockets"] = before + out
    if not graph.metadata["no_pockets"]:
        graph.metadata.pop("no_pockets")
    return n, len(out)


def _leave_out(graph: RoadGraph, key: str, candidates: list[list[int]], too_short: Callable[[], bool]) -> int:
    """Add [node, edge] candidates to graph.metadata[key] one at a time, in order, until
    the edge fits (too_short false), then take back those it fits without; return how
    many stay (0: it does not fit with all of them, and none is added)."""
    before = list(graph.metadata.get(key, []))
    out: list[list[int]] = []
    for pair in candidates:
        out.append(pair)
        graph.metadata[key] = before + out
        if not too_short():
            for extra in out[:-1]:  # those it fits without go back
                graph.metadata[key] = before + [x for x in out if x != extra]
                if not too_short():
                    out.remove(extra)
            graph.metadata[key] = before + out
            return len(out)
    if before:
        graph.metadata[key] = before
    else:
        graph.metadata.pop(key, None)
    return 0


def _dissolve_bend(graph: RoadGraph, eid: int, margin: float, cross_section: Optional[CrossSectionParams]) -> bool:
    """Join an edge with its neighbour at an end of degree 2 (a bend, not a junction) into
    one edge between the two outer nodes; the node goes. Only where both edges are the same
    road (class, lanes each way, lane width, speed, profile; no bridges or tunnels), the
    two outer nodes differ and are not already joined, and both stay buildable. The node
    is recorded in the graph metadata ("straightened_bends"). Return whether one went."""
    e = graph.edges[eid]
    for node in sorted((e.from_node, e.to_node)):
        if graph.degree(node) != 2:
            continue
        other = next(x for x in graph.incident_edges(node) if x.id != eid)
        a = e.from_node if e.to_node == node else e.to_node
        b = other.to_node if other.from_node == node else other.from_node
        if a == b or any({x.from_node, x.to_node} == {a, b} for x in graph.edges.values()):
            continue
        first = _oriented(e, a)  # (forward, backward) running a -> node
        second = _oriented(other, node)  # node -> b
        same = (e.road_class == other.road_class and first == second and e.lane_width == other.lane_width
                and e.speed_limit == other.speed_limit and e.cross_section == other.cross_section)
        if not same or e.bridges or e.tunnels or other.bridges or other.tunnels:
            continue
        saved = {x.id: x for x in (e, other)}
        heights = _joined_profile(graph, e, a, node, other, b)
        keep = min(e.id, other.id)
        del graph.edges[e.id], graph.edges[other.id]
        graph.edges[keep] = Edge(keep, a, b, e.road_class, first[0], first[1], e.lane_width, e.speed_limit,
                                 heights, [], [], e.cross_section)
        graph.edges = dict(sorted(graph.edges.items()))
        try:
            node_trims(graph, a, margin, cross_section)
            node_trims(graph, b, margin, cross_section)
        except ValueError:
            del graph.edges[keep]
            graph.edges.update(saved)
            graph.edges = dict(sorted(graph.edges.items()))
            continue
        del graph.nodes[node]
        graph.metadata.setdefault("straightened_bends", []).append(node)
        log.debug("repair: straightened bend node %d (edges %d and %d into %d)", node, e.id, other.id, keep)
        return True
    return False


def _oriented(edge: Edge, start: int) -> tuple[int, int]:
    """(lanes away from start, lanes towards it) of an edge."""
    return (edge.lanes_forward, edge.lanes_backward) if edge.from_node == start else \
        (edge.lanes_backward, edge.lanes_forward)


def _joined_profile(graph: RoadGraph, e: Edge, a: int, node: int, other: Edge, b: int) -> list[float]:
    """Heights strictly between a and b of the joined edge: both edges' profiles and the
    bend node's height along the old polyline, resampled at as many even points."""
    if not e.profile and not other.profile:
        return []
    def along(edge: Edge, start: int, end: int) -> list[tuple[float, float]]:
        length = graph.edge_length(edge.id)
        n = len(edge.profile)
        hs = edge.profile if edge.from_node == start else edge.profile[::-1]
        if not n:
            return [(0.0, graph.nodes[start].z), (length, graph.nodes[end].z)]
        return ([(0.0, graph.nodes[start].z)] + [(length * (k + 1) / (n + 1), h) for k, h in enumerate(hs)]
                + [(length, graph.nodes[end].z)])
    first, second = along(e, a, node), along(other, node, b)
    l1 = first[-1][0]
    pts = first + [(l1 + d, h) for d, h in second[1:]]
    total = pts[-1][0]
    n = len(e.profile) + len(other.profile) + 1
    xs, hs = [d for d, _ in pts], [h for _, h in pts]
    return [float(np.interp(total * (k + 1) / (n + 1), xs, hs)) for k in range(n)]


def _collapse(graph: RoadGraph, eid: int, margin: float, max_degree: int,
              cross_section: Optional[CrossSectionParams] = None) -> bool:
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
    moved = [x for x in graph.incident_edges(keep) if x.id != eid] + others
    old_lengths = {x.id: graph.edge_length(x.id) for x in moved}
    a.x, a.y = (a.x + b.x) / 2, (a.y + b.y) / 2
    del graph.edges[eid]
    for x in others:
        if x.from_node == gone:
            x.from_node = keep
        else:
            x.to_node = keep
    try:
        node_trims(graph, keep, margin, cross_section)  # raises if two arms are now too close
        # Moving the node also turns the arms at its neighbours: they must still be
        # buildable, two-road bends too, which become junctions when too tight (B9: a
        # collapse left a neighbour's two roads 1.7 deg apart).
        for far in kept_far | gone_far:
            node_trims(graph, far, margin, cross_section)
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
    # Bridges and tunnels (from drape, before a later repair) stay at the same share of
    # their edge when the edge's length changes.
    for x in moved:
        f = graph.edge_length(x.id) / old_lengths[x.id] if old_lengths[x.id] > LENGTH_EPS else 1.0
        if f != 1.0:
            x.bridges = [(s0 * f, s1 * f) for s0, s1 in x.bridges]
            x.tunnels = [(s0 * f, s1 * f) for s0, s1 in x.tunnels]
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
    for key in ("bridges", "tunnels"):
        ids: Optional[list] = graph.metadata.get(key)
        if ids and eid in ids:
            graph.metadata[key] = [b for b in ids if b != eid]
