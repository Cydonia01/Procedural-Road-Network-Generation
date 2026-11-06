"""Road hierarchy from strokes (stage R1): strokes, ranking, lanes constant along a road."""

from __future__ import annotations

import math

import pytest

from roadgen.generators.hierarchy import (
    ARTERIAL,
    COLLECTOR,
    LOCAL,
    HierarchyParams,
    Level,
    assign_hierarchy,
    strokes,
)
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.model import Edge, Node, RoadGraph
from tests.grids import SPACING, small_grid

LEVELS = {ARTERIAL: Level("avenue", (2, 2), 13.9), COLLECTOR: Level("avenue", (1, 1), 12.5),
          LOCAL: Level("street", (1, 1), 11.1)}


def graph(nodes: dict[int, tuple[float, float]], edges: list[tuple[int, int]]) -> RoadGraph:
    g = RoadGraph()
    for n, (x, y) in nodes.items():
        g.add_node(Node(n, x, y))
    for k, (a, b) in enumerate(edges, 1):
        g.add_edge(Edge(k, a, b, "street", 1, 1))
    return g


def test_t_junction_through_road_is_one_stroke():
    # 1 - 2 - 3 straight, 2 - 4 the stem.
    g = graph({1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (100, 100)}, [(1, 2), (2, 3), (2, 4)])
    assert strokes(g, g.edges, math.radians(35)) == [[1, 2], [3]]


def test_bend_at_a_two_road_node_stays_one_stroke():
    g = graph({1: (0, 0), 2: (100, 0), 3: (100, 100)}, [(1, 2), (2, 3)])  # 90 deg bend
    assert strokes(g, g.edges, math.radians(35)) == [[1, 2]]


def test_crossing_pairs_straightest_arms():
    # Slightly skewed crossing: 1-2-3 nearly straight, 4-2-5 nearly straight.
    g = graph({1: (0, 0), 2: (100, 0), 3: (200, 10), 4: (100, 100), 5: (110, -100)},
              [(1, 2), (2, 3), (4, 2), (2, 5)])
    assert strokes(g, g.edges, math.radians(35)) == [[1, 2], [3, 4]]


def test_main_road_of_a_grid_becomes_arterial():
    # A long road through the middle of a ladder carries the most shortest paths.
    nodes = {k: (100.0 * k, 0.0) for k in range(9)}
    nodes |= {10 + k: (100.0 * k, 80.0) for k in range(2, 7, 2)}
    edges = [(k, k + 1) for k in range(8)] + [(k, 10 + k) for k in range(2, 7, 2)]
    g = graph(nodes, edges)
    levels = assign_hierarchy(g, HierarchyParams(LEVELS, arterial_share=0.5, collector_share=0.0), 0)
    assert all(levels[k] == ARTERIAL for k in range(1, 9))  # the long road
    assert all(g.edges[k].road_class == "avenue" and g.edges[k].total_lanes == 4 for k in range(1, 9))
    assert all(levels[k] == LOCAL and g.edges[k].road_class == "street" for k in range(9, 12))


def test_fixed_edges_keep_their_class():
    g = graph({1: (0, 0), 2: (100, 0), 3: (200, 0)}, [(1, 2), (2, 3)])
    g.edges[1].road_class, g.edges[1].lanes_forward = "highway", 2
    levels = assign_hierarchy(g, HierarchyParams(LEVELS, arterial_share=1.0, collector_share=0.0), 0, fixed={1})
    assert 1 not in levels and g.edges[1].road_class == "highway" and g.edges[1].lanes_forward == 2


def test_asymmetric_lanes_follow_the_stroke():
    # Edges of one road drawn in alternating directions: forward lanes must still line up.
    g = graph({1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (300, 0)}, [(1, 2), (3, 2), (3, 4)])
    levels = {**LEVELS, ARTERIAL: Level("avenue", (2, 1), 13.9)}
    assign_hierarchy(g, HierarchyParams(levels, arterial_share=1.0, collector_share=0.0), 0)
    e1, e2, e3 = g.edges[1], g.edges[2], g.edges[3]
    assert (e1.lanes_forward, e1.lanes_backward) == (2, 1)
    assert (e2.lanes_forward, e2.lanes_backward) == (1, 2)  # drawn 3 -> 2, against the road
    assert (e3.lanes_forward, e3.lanes_backward) == (2, 1)


def test_one_way_edges_keep_direction():
    g = graph({1: (0, 0), 2: (100, 0)}, [(1, 2)])
    g.edges[1].lanes_forward, g.edges[1].lanes_backward = 0, 2
    assign_hierarchy(g, HierarchyParams(LEVELS, arterial_share=1.0, collector_share=0.0), 0)
    assert (g.edges[1].lanes_forward, g.edges[1].lanes_backward) == (0, 4)


def _two_road_changes(g: RoadGraph) -> int:
    changes = 0
    for n in g.nodes:
        es = g.incident_edges(n)
        if len(es) == 2:
            a, b = es
            changes += a.road_class != b.road_class or a.total_lanes != b.total_lanes
    return changes


@pytest.mark.parametrize("preset, seed", [("organic_town", 1), ("paris", 0), ("grid_organic", 2)])
def test_no_lane_change_at_two_road_nodes(preset, seed):
    g = LSystemGenerator().generate(LSystemParams.preset(preset), seed)
    assert _two_road_changes(g) == 0
    classes = {e.road_class for e in g.edges.values()}
    assert classes == {"avenue", "street"}


def test_shares_of_the_length():
    p = LSystemParams.preset("organic_town")
    g = LSystemGenerator().generate(p, 1)
    total = sum(g.edge_length(e) for e in g.edges)
    arterial = sum(g.edge_length(k) for k, e in g.edges.items() if e.road_class == "avenue" and e.total_lanes == 4)
    # Whole strokes: the arterial share overshoots by at most one stroke.
    assert p.arterial_share * total * 0.8 <= arterial <= (p.arterial_share + 0.15) * total


def test_alternating_lines_stay_one_way_under_strokes():
    g = small_grid(4, 5, oneway_pattern=("alternating", "alternating"))
    for e in g.edges.values():
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        assert e.lanes_backward == 0
        if abs(b.x - a.x) < 1e-6:  # along y: line i runs north iff i is even
            assert (b.y > a.y) == (round(a.x / SPACING[0]) % 2 == 0)
        else:
            assert (b.x > a.x) == (round(a.y / SPACING[1]) % 2 == 0)


def test_axis_rule_is_still_available():
    g = small_grid(3, 3, hierarchy="axis")
    for e in g.edges.values():
        vertical = abs(g.nodes[e.to_node].x - g.nodes[e.from_node].x) < 1e-6
        assert e.road_class == ("avenue" if vertical else "street")


def test_deterministic():
    p = LSystemParams.preset("paris")
    a, b = LSystemGenerator().generate(p, 3), LSystemGenerator().generate(p, 3)
    assert a == b
