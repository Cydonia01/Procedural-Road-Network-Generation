"""Cul-de-sac bulbs (stage C4): turn-arounds at the dead ends of streets.

A dead end becomes a teardrop: the street's end node joins a one-way loop
of nodes on a circle of the given radius that touches the node, centred
straight ahead along the street. Traffic drives the loop counter-clockwise
(right-hand traffic, island on the left) and leaves the way it came, so the
dead end is no longer a trap. With LOOP_NODES nodes on the circle, the
loop's two arms leave the end node 120 deg from the street and from each
other: a symmetric Y junction. Teng & Bidarra (2017) place cul-de-sac
patches at dead ends; the teardrop shape is the common US layout.

A bulb is only added where its circle, widened by a lane and a clearance,
touches no other road, and where an allowed callback (map legality, the
generator's area) accepts it. On a wide street the loop grows, so that its
chords keep room for the junction's trims (radius at least the street's half
width plus ROOM). Where the room ahead is taken, the end node
moves back along the street (the bulb takes the end of the street instead),
in steps of the radius, as long as at least min_stem of the street is left;
otherwise the dead end stays.
"""

from __future__ import annotations

import logging
import math
from itertools import pairwise
from typing import Callable, Optional, Sequence

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from roadgen.graph.model import Edge, Node, RoadClass, RoadGraph

log = logging.getLogger(__name__)

LOOP_NODES = 6  # nodes on the circle, the end node included: 60 deg between them
CLEARANCE = 2.0  # [m] between the bulb's outer lane edge and any other road
ROOM = 12.0  # [m] a loop's radius exceeds the street's half width at least by this (junction trims)
RETREATS = 3  # tries moving the end node back along the street, one radius each


def bulb_points(end: tuple[float, float], heading: float, radius: float) -> list[tuple[float, float]]:
    """The loop nodes after the end node, counter-clockwise, on the circle through
    end centred radius ahead of it along heading."""
    cx, cy = end[0] + radius * math.cos(heading), end[1] + radius * math.sin(heading)
    start = heading + math.pi  # the end node's angle as seen from the centre
    return [(cx + radius * math.cos(start + 2 * math.pi * k / LOOP_NODES),
             cy + radius * math.sin(start + 2 * math.pi * k / LOOP_NODES)) for k in range(1, LOOP_NODES)]


def add_culdesacs(
    graph: RoadGraph, rng: np.random.Generator, prob: Callable[[Node], float], radius: float,
    speed: float, allowed: Optional[Callable[[Polygon], bool]] = None,
    road_classes: Sequence[RoadClass] = ("street",), min_stem: float = 30.0,
) -> int:
    """Add bulbs at street dead ends, each with probability prob(node); returns how many.

    One random draw per dead end, in node-id order, so the outcome depends only
    on rng. Loop edges are one-lane, one-way streets at speed [m/s] (lower if
    the street is slower). Bulbs are listed in graph.metadata["culdesacs"] as
    {"node": end node, "loop": [loop node ids]}.
    """
    dead_ends = [n for n in sorted(graph.nodes) if graph.degree(n) == 1]
    if not dead_ends:
        return 0
    draws = rng.random(len(dead_ends))
    lines = {e.id: LineString([(graph.nodes[e.from_node].x, graph.nodes[e.from_node].y),
                               (graph.nodes[e.to_node].x, graph.nodes[e.to_node].y)])
             for e in graph.edges.values()}
    ids = list(lines)
    tree = STRtree([lines[i] for i in ids])
    next_node = max(graph.nodes) + 1
    next_edge = max(graph.edges) + 1
    bulbs = list(graph.metadata.get("culdesacs") or [])
    added = []  # footprints of the bulbs added so far
    for nid, r in zip(dead_ends, draws):
        node = graph.nodes[nid]
        (stem,) = graph.incident_edges(nid)
        if stem.road_class not in road_classes or r >= prob(node):
            continue
        other = graph.nodes[stem.from_node if stem.to_node == nid else stem.to_node]
        r_loop = max(radius, max(stem.lanes_forward, stem.lanes_backward) * stem.lane_width + ROOM)
        heading = math.atan2(node.y - other.y, node.x - other.x)
        length = math.hypot(node.x - other.x, node.y - other.y)
        fit = None
        for k in range(RETREATS + 1):
            back = k * r_loop
            if length - back < min_stem:
                break
            end = (node.x - back * math.cos(heading), node.y - back * math.sin(heading))
            centre = Point(end[0] + r_loop * math.cos(heading), end[1] + r_loop * math.sin(heading))
            footprint = centre.buffer(r_loop + stem.lane_width + CLEARANCE)
            hits = [ids[j] for j in tree.query(footprint) if ids[j] != stem.id
                    and lines[ids[j]].intersects(footprint)]
            if hits or any(footprint.intersects(f) for f in added):
                continue
            if allowed is not None and not allowed(footprint):
                continue
            fit = end, footprint
            break
        if fit is None:
            continue
        (node.x, node.y), footprint = fit
        pts = bulb_points((node.x, node.y), heading, r_loop)
        loop = []
        for x, y in pts:
            graph.add_node(Node(next_node, x, y))
            loop.append(next_node)
            next_node += 1
        ring = [nid, *loop, nid]
        for a, b in pairwise(ring):
            graph.add_edge(Edge(next_edge, a, b, "street", 1, 0, lane_width=stem.lane_width,
                                speed_limit=min(speed, stem.speed_limit)))
            next_edge += 1
        added.append(footprint)
        bulbs.append({"node": nid, "loop": loop})
    if bulbs:
        graph.metadata["culdesacs"] = bulbs
    log.debug("cul-de-sacs: %d of %d dead ends", len(added), len(dead_ends))
    return len(added)
