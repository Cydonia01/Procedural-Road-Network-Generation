"""Staggered crossroads (stage C5): a 4-way crossing becomes two offset T junctions.

At a node where two roads cross (two pairs of opposite arms, each within
STRAIGHT_TOL of a straight line), one arm of the crossing road is moved
along the through road by offset: the through road is split there and the
arm reattached to the new node. Muktadir et al. (JunctionArt) list offset
legs among the junction shapes AV datasets lack; staggered crossroads are
common in older European towns.

The through road is the pair with more lanes (lower edge id on a tie); the
moved arm is the crossing pair's lower edge id, slid towards the through arm
whose edge is longer. A stagger is undone where the moved arm would cross a
road, meet another arm closer than min_angle_deg, or where the road between
the two T junctions keeps less than MIN_ROAD_LENGTH after their trims.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from roadgen.generators.repair import MIN_ROAD_LENGTH
from roadgen.geometry.junction_space import DEFAULT_MARGIN, node_trims
from roadgen.graph.model import Edge, Node, RoadGraph

log = logging.getLogger(__name__)

STRAIGHT_TOL = math.radians(20.0)  # opposite arms this close to a straight line form a road


def _arms(graph: RoadGraph, node_id: int) -> list[tuple[float, Edge, int]]:
    """(outward heading, edge, far node) of every road at a node, by heading."""
    n = graph.nodes[node_id]
    out = []
    for e in graph.incident_edges(node_id):
        far = e.to_node if e.from_node == node_id else e.from_node
        o = graph.nodes[far]
        out.append((math.atan2(o.y - n.y, o.x - n.x) % (2 * math.pi), e, far))
    return sorted(out, key=lambda a: a[0])


def _crosses(graph: RoadGraph, a: tuple[float, float], b: tuple[float, float], skip: set[int]) -> bool:
    for e in graph.edges.values():
        if e.from_node in skip or e.to_node in skip:
            continue
        p, q = graph.nodes[e.from_node], graph.nodes[e.to_node]
        d1, d2 = _side(a, b, (p.x, p.y)), _side(a, b, (q.x, q.y))
        d3, d4 = _side((p.x, p.y), (q.x, q.y), a), _side((p.x, p.y), (q.x, q.y), b)
        if d1 * d2 < 0 and d3 * d4 < 0:
            return True
    return False


def _side(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def stagger_crossings(graph: RoadGraph, prob: float, offset: float, rng: np.random.Generator,
                      min_angle_deg: float = 30.0, margin: float = DEFAULT_MARGIN) -> int:
    """Stagger 4-way crossings, each with probability prob; returns how many.

    One random draw per 4-way node in node-id order, so the outcome depends
    only on rng. offset [m] is the distance between the two T junctions.
    """
    candidates = [n for n in sorted(graph.nodes) if graph.degree(n) == 4]
    draws = rng.random(len(candidates))
    next_node, next_edge = max(graph.nodes) + 1, max(graph.edges) + 1
    done = 0
    for node_id, r in zip(candidates, draws):
        if r >= prob or graph.degree(node_id) != 4:
            continue
        arms = _arms(graph, node_id)
        pairs = [(arms[0], arms[2]), (arms[1], arms[3])]
        if any(abs(abs(a[0] - b[0]) - math.pi) > STRAIGHT_TOL for a, b in pairs):
            continue  # not a crossing of two roads
        through, crossing = sorted(pairs, key=lambda pr: (-sum(a[1].total_lanes for a in pr),
                                                           min(a[1].id for a in pr)))
        moved = min(crossing, key=lambda a: a[1].id)
        towards = max(through, key=lambda a: (graph.edge_length(a[1].id), -a[1].id))
        t_edge, t_far = towards[1], towards[2]
        if graph.edge_length(t_edge.id) < offset + MIN_ROAD_LENGTH + 2 * margin or t_edge.profile \
                or t_edge.bridges or moved[1].profile:
            continue
        n = graph.nodes[node_id]
        new = Node(next_node, n.x + offset * math.cos(towards[0]), n.y + offset * math.sin(towards[0]))
        m_far = graph.nodes[moved[2]]
        if _crosses(graph, (new.x, new.y), (m_far.x, m_far.y), {node_id, t_far, moved[2]}):
            continue
        # Split the through edge at the new node, reattach the moved arm there.
        graph.add_node(new)
        old_t = (t_edge.from_node, t_edge.to_node)
        forward = t_edge.from_node == node_id  # the edge runs node -> far: both pieces keep that
        t_edge.from_node, t_edge.to_node = (node_id, new.id) if forward else (new.id, node_id)
        far_side = Edge(next_edge, *((new.id, t_far) if forward else (t_far, new.id)), t_edge.road_class,
                        t_edge.lanes_forward, t_edge.lanes_backward, t_edge.lane_width, t_edge.speed_limit)
        graph.add_edge(far_side)
        e = moved[1]
        old_m = (e.from_node, e.to_node)
        if e.from_node == node_id:
            e.from_node = new.id
        else:
            e.to_node = new.id
        if _valid(graph, node_id, new.id, min_angle_deg, margin):
            next_node, next_edge, done = next_node + 1, next_edge + 1, done + 1
            log.debug("stagger: node %d -> T junctions %d, %d", node_id, node_id, new.id)
            continue
        # Undo.
        e.from_node, e.to_node = old_m
        del graph.edges[far_side.id]
        t_edge.from_node, t_edge.to_node = old_t
        del graph.nodes[new.id]
    return done


def _valid(graph: RoadGraph, a: int, b: int, min_angle_deg: float, margin: float) -> bool:
    """Both T junctions keep their arms min_angle_deg apart and MIN_ROAD_LENGTH between them."""
    for node_id in (a, b):
        heads = [h for h, _, _ in _arms(graph, node_id)]
        gaps = [(heads[(i + 1) % len(heads)] - h) % (2 * math.pi) for i, h in enumerate(heads)]
        if min(gaps) < math.radians(min_angle_deg):
            return False
    link = next(e for e in graph.incident_edges(a) if b in (e.from_node, e.to_node))
    try:
        trims = node_trims(graph, a, margin)[link.id] + node_trims(graph, b, margin)[link.id]
    except ValueError:
        return False
    return graph.edge_length(link.id) - trims >= MIN_ROAD_LENGTH
