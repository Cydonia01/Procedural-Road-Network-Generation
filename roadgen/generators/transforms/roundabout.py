"""Roundabouts (stage C7): a junction becomes a one-way ring.

A node with 3 to 5 roads, at least MIN_ARM_GAP_DEG apart, none of them a
highway, is replaced by a ring of radius R around it, driven
counter-clockwise (right-hand traffic). Each road is cut back to an entry
node on the ring, and the ring between two entries gets a bend node at
least every RING_STEP_DEG (a short gap has none: its entries' turns curve it), so the
builder fits it as arcs. Entries are
3-way junctions of a road and the ring's two directions, so every entry
turns right onto the ring and every exit right off it; a U-turn is a lap.
Inscribed radii after the FHWA roundabout guide (Exhibit 1-9 / 3-12): a
single-lane ring (centre line) radius_street when every road is a street, a
two-lane ring radius_avenue when a road has 4 or more lanes or is an
avenue.

The ring must also be smooth: fitted by the builder's own alignment (which
the new pieces are checked with), none of its bends may become a junction
because its inner lane would turn tighter than design.min_lane_radius;
otherwise the radius is tried RADIUS_GROWTH larger once. Nothing changes
unless the whole roundabout fits: every road keeps
MIN_ROAD_LENGTH after the entry and its far end's trims, every ring arc
keeps it between its two entries' trims, and the ring (widened by its lanes
and a clearance) touches no other road and passes an allowed callback (map
legality, the generator's area). Roundabouts are listed in
graph.metadata["roundabouts"] (for the metrics and, later, yield signs).
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Optional

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from roadgen.generators.repair import MIN_ROAD_LENGTH
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.junction_space import DEFAULT_MARGIN, node_trims
from roadgen.graph.model import Edge, Node, RoadGraph

log = logging.getLogger(__name__)

MIN_ARM_GAP_DEG = 50.0  # roads closer than this can't each get an entry
RING_STEP_DEG = 60.0  # most arc between two ring nodes
CLEARANCE = 3.0  # [m] between the ring's outer edge and other roads
RADIUS_GROWTH = 1.25  # a ring too tight to fit smoothly is tried this much larger once


def _arms(graph: RoadGraph, node_id: int) -> list[tuple[float, Edge, int]]:
    n = graph.nodes[node_id]
    out = []
    for e in graph.incident_edges(node_id):
        far = e.to_node if e.from_node == node_id else e.from_node
        o = graph.nodes[far]
        out.append((math.atan2(o.y - n.y, o.x - n.x) % (2 * math.pi), e, far))
    return sorted(out, key=lambda a: a[0])


def _plan(graph: RoadGraph, node_id: int, radius_street: float, radius_avenue: float, speed: float,
          margin: float) -> Optional[dict]:
    """The roundabout replacing node_id (positions, edges), or None if it doesn't fit."""
    for growth in (1.0, RADIUS_GROWTH):
        plan = _plan_at(graph, node_id, radius_street * growth, radius_avenue * growth, speed, margin)
        if plan is not None:
            return plan
    return None


def _plan_at(graph: RoadGraph, node_id: int, radius_street: float, radius_avenue: float, speed: float,
             margin: float) -> Optional[dict]:
    arms = _arms(graph, node_id)
    if not 3 <= len(arms) <= 5 or any(e.road_class == "highway" or e.profile or e.bridges for _, e, _ in arms):
        return None
    heads = [h for h, _, _ in arms]
    gaps = [(heads[(i + 1) % len(heads)] - h) % (2 * math.pi) for i, h in enumerate(heads)]
    if min(gaps) < math.radians(MIN_ARM_GAP_DEG):
        return None
    big = any(e.road_class == "avenue" or e.total_lanes >= 4 for _, e, _ in arms)
    radius, lanes = (radius_avenue, 2) if big else (radius_street, 1)
    width = max(e.lane_width for _, e, _ in arms)
    c = graph.nodes[node_id]
    # Ring nodes counter-clockwise: each entry, then bend nodes up to the next entry.
    ring: list[tuple[float, float, Optional[int]]] = []  # (x, y, arm index or None)
    for i, (h, _, _) in enumerate(arms):
        steps = math.ceil(gaps[i] / math.radians(RING_STEP_DEG) - 1e-9)
        for k in range(steps):
            a = h + gaps[i] * k / steps
            ring.append((c.x + radius * math.cos(a), c.y + radius * math.sin(a), i if k == 0 else None))
    # A small graph of the new pieces, for the junction trims at each entry.
    local = RoadGraph()
    ids = {}
    nid = max(graph.nodes) + 1
    for j, (x, y, _) in enumerate(ring):
        local.add_node(Node(nid, x, y))
        ids[j] = nid
        nid += 1
    eid = max(graph.edges) + 1
    ring_edges = []
    for j in range(len(ring)):
        a, b = ids[j], ids[(j + 1) % len(ring)]
        local.add_edge(Edge(eid, a, b, "avenue" if big else "street", lanes, 0, width, speed))
        ring_edges.append(eid)
        eid += 1
    entries = {}
    for j, (_, _, i) in enumerate(ring):
        if i is None:
            continue
        _, e, far = arms[i]
        f = graph.nodes[far]
        local.add_node(Node(far, f.x, f.y))
        arm = Edge(e.id, ids[j], far, e.road_class, e.lanes_forward, e.lanes_backward, e.lane_width,
                   e.speed_limit) if e.to_node == far else Edge(e.id, far, ids[j], e.road_class,
                                                                e.lanes_forward, e.lanes_backward,
                                                                e.lane_width, e.speed_limit)
        local.add_edge(arm)
        entries[i] = ids[j]
    try:
        entry_trims = {i: node_trims(local, n, margin) for i, n in entries.items()}
    except ValueError:
        return None
    # Each arm keeps a road after the entry's trim and its far end's.
    for i, (_, e, far) in enumerate(arms):
        far_trim = node_trims(graph, far, margin).get(e.id, 0.0) if graph.degree(far) >= 3 else 0.0
        if local.edge_length(e.id) - entry_trims[i][e.id] - far_trim < MIN_ROAD_LENGTH:
            return None
    # Each ring arc keeps a road between its two entries' trims.
    order = sorted(entries)
    for k, i in enumerate(order):
        nxt = order[(k + 1) % len(order)]
        out_edge = next(x.id for x in local.incident_edges(entries[i]) if x.from_node == entries[i]
                        and x.id in ring_edges)
        in_edge = next(x.id for x in local.incident_edges(entries[nxt]) if x.to_node == entries[nxt]
                       and x.id in ring_edges)
        if radius * gaps[i] - entry_trims[i][out_edge] - entry_trims[nxt][in_edge] < MIN_ROAD_LENGTH:
            return None
    # Smooth: no ring bend may stay a junction in the builder's alignment.
    try:
        na = network_alignment(local, junction_margin=margin)
    except ValueError:
        return None
    if set(na.junctions) - set(entries.values()):
        return None
    return {"centre": (c.x, c.y), "radius": radius, "lanes": lanes, "local": local, "ids": ids,
            "ring_edges": ring_edges, "entries": entries, "arms": arms,
            "footprint": Point(c.x, c.y).buffer(radius + lanes * width + CLEARANCE)}


def add_roundabouts(graph: RoadGraph, rng: np.random.Generator, prob: Callable[[Node], float],
                    radius_street: float, radius_avenue: float, speed: float,
                    allowed: Optional[Callable[[Polygon], bool]] = None,
                    margin: float = DEFAULT_MARGIN) -> int:
    """Turn junctions into roundabouts, each with probability prob(node); returns how many.

    One random draw per candidate (3 to 5 roads) in node-id order, so the
    outcome depends only on rng. Ring edges are one-way at speed [m/s].
    """
    candidates = [n for n in sorted(graph.nodes) if 3 <= graph.degree(n) <= 5]
    draws = rng.random(len(candidates))
    done = list(graph.metadata.get("roundabouts") or [])
    footprints: list[Polygon] = []
    # Roads as they are now: an arm only ever gets shorter along its line, and new
    # rings are kept apart by their footprints.
    edge_ids = list(graph.edges)
    tree = STRtree([LineString([(graph.nodes[graph.edges[e].from_node].x, graph.nodes[graph.edges[e].from_node].y),
                                (graph.nodes[graph.edges[e].to_node].x, graph.nodes[graph.edges[e].to_node].y)])
                    for e in edge_ids])
    for node_id, r in zip(candidates, draws):
        if node_id not in graph.nodes or r >= prob(graph.nodes[node_id]):
            continue
        plan = _plan(graph, node_id, radius_street, radius_avenue, speed, margin)
        if plan is None:
            continue
        arm_ids = {e.id for _, e, _ in plan["arms"]}
        fp = plan["footprint"]
        if any(edge_ids[k] not in arm_ids for k in tree.query(fp, predicate="intersects")):
            continue
        if any(fp.intersects(f) for f in footprints) or (allowed is not None and not allowed(fp)):
            continue
        _apply(graph, node_id, plan)
        footprints.append(fp)
        done.append({"node": node_id, "centre": [round(v, 3) for v in plan["centre"]],
                     "radius": plan["radius"], "lanes": plan["lanes"],
                     "entries": sorted(plan["entries"].values()), "ring": list(plan["ids"].values())})
    if done:
        graph.metadata["roundabouts"] = done
    log.debug("roundabouts: %d of %d candidates", len(footprints), len(candidates))
    return len(footprints)


def _apply(graph: RoadGraph, node_id: int, plan: dict) -> None:
    local: RoadGraph = plan["local"]
    for nid in plan["ids"].values():
        graph.add_node(local.nodes[nid])
    for eid in plan["ring_edges"]:
        graph.add_edge(local.edges[eid])
    for i, (_, e, _) in enumerate(plan["arms"]):
        entry = plan["entries"][i]
        if e.from_node == node_id:
            e.from_node = entry
        else:
            e.to_node = entry
    del graph.nodes[node_id]
