from __future__ import annotations

import pytest

from roadgen.config import DEFAULTS
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.repair import MIN_ROAD_LENGTH, repair_short_edges, short_edges, stub_roads
from roadgen.graph.io import graph_to_dict
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from tests.shared import grown, grown_maps

PLAIN = HdMapParams(enabled=False)  # these tests build driving lanes only (HD maps on by default since B10)


def two_junctions(gap: float) -> RoadGraph:
    """Two 3-way junctions joined by an edge of length gap."""
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (gap, 0.0), 3: (-100.0, 0.0), 4: (0.0, 100.0), 5: (gap, -100.0), 6: (gap + 100, 0.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate([(1, 2), (3, 1), (1, 4), (2, 5), (2, 6)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    return g


def test_short_edge_between_junctions_is_collapsed():
    g = two_junctions(8.0)
    assert short_edges(g) == [1]
    assert repair_short_edges(g) == 1
    assert 1 not in g.edges and 2 not in g.nodes
    assert g.nodes[1].x == pytest.approx(4.0)
    assert g.degree(1) == 4 and short_edges(g) == []
    OpenDriveBuilder(hd=PLAIN).build(g)


def test_long_enough_edges_are_untouched():
    g = two_junctions(60.0)
    before = graph_to_dict(g)
    assert repair_short_edges(g) == 0
    assert graph_to_dict(g) == before


def test_collapse_refused_when_it_would_duplicate_a_road_then_edge_removed():
    # Nodes 1 and 2 both connect to node 7: merging them would give two 1-7 roads.
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (8.0, 0.0), 3: (-100.0, 0.0), 4: (0.0, 100.0), 6: (108.0, 0.0), 7: (4.0, -100.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate([(1, 2), (3, 1), (1, 4), (2, 6), (1, 7), (2, 7)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    assert short_edges(g) == [1]
    assert repair_short_edges(g) == 1
    assert 1 not in g.edges and set(g.nodes) == {1, 2, 3, 4, 6, 7}  # removed, not collapsed
    assert short_edges(g) == []


@pytest.mark.slow
def test_sweep_seed_60_now_builds():
    g = grown("city", 60, maps=True)  # 42 m between two junctions needing 21 m each (A0 sweep)
    assert short_edges(g) == []
    OpenDriveBuilder(hd=PLAIN).build(g)


@pytest.mark.slow
def test_buildable_networks_are_unchanged(monkeypatch):
    # The repair only touches edges the builder would reject: a seed that built
    # before (seed 3) gives the same graph with the repair switched off.
    repaired = graph_to_dict(grown("city", 3, maps=True))
    monkeypatch.setattr("roadgen.generators.lsystem.repair_short_edges", lambda graph, **kw: 0)
    unrepaired = LSystemGenerator().generate(LSystemParams.preset("city"), 3, grown_maps("city", 3))
    assert graph_to_dict(unrepaired) == repaired


def test_stub_road_between_junctions_is_merged():
    # Two T junctions 20 m apart with 2+2-lane roads: each trims 9 m, leaving a 2 m stub
    # road that cannot change from one junction's slope and banking to the other's.
    g = RoadGraph()
    for nid, (x, y) in {1: (0, 0), 2: (20, 0), 3: (-100, 0), 4: (-30, 100), 5: (50, 100), 6: (120, 0)}.items():
        g.add_node(Node(nid, float(x), float(y)))
    for eid, (a, b) in enumerate([(1, 2), (3, 1), (1, 4), (2, 5), (2, 6)], 1):
        g.add_edge(Edge(eid, a, b, "avenue", 2, 2))
    assert MIN_ROAD_LENGTH > 2.0
    assert stub_roads(g) == [1]
    assert repair_short_edges(g) == 1
    assert stub_roads(g) == [] and 1 not in g.edges
    OpenDriveBuilder(hd=PLAIN).build(g)


def test_collapse_keeps_the_neighbours_buildable():
    """Collapsing an edge moves the surviving node, which turns the roads at its
    neighbours: a collapse that would leave a neighbour's roads under 10 deg apart is
    refused (B9: an HD sweep seed failed so at a two-road bend)."""
    from roadgen.generators.repair import _collapse

    g = RoadGraph()
    # Node 1 is a sharp two-road bend: roads to 2 (0 deg) and to 3 (about 17 deg).
    # Collapsing the short edge 3-4 moves node 3 to (100, 10): the bend closes to 6 deg.
    for nid, (x, y) in {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (100.0, 30.0), 4: (100.0, -10.0),
                        5: (200.0, 60.0), 6: (200.0, -60.0), 7: (100.0, 120.0)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((1, 2), (1, 3), (3, 4), (3, 5), (4, 6), (3, 7)), 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    assert not _collapse(g, 3, DEFAULTS["opendrive"]["junction_margin"], 5)
    assert (g.nodes[3].x, g.nodes[3].y) == (100.0, 30.0) and 3 in g.edges and 4 in g.nodes  # undone


def test_dissolve_bend_joins_the_same_road():
    """B9: a two-road bend between two junctions becomes one edge between them (the road
    stays, the bend node goes); lanes keep their direction, heights are resampled."""
    from roadgen.generators.repair import _dissolve_bend

    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-50.0, 0.0), 3: (0.0, 50.0), 4: (10.0, 10.0), 5: (20.0, 0.0),
                        6: (70.0, 0.0), 7: (20.0, -50.0)}.items():
        g.add_node(Node(nid, x, y))
    g.nodes[4].z = 2.0
    g.add_edge(Edge(1, 1, 4, "avenue", 2, 1, profile=[1.0]))  # junction 1 -> bend 4
    g.add_edge(Edge(2, 5, 4, "avenue", 1, 2))  # junction 5 -> bend 4: the same road, drawn the other way
    for eid, (a, b) in enumerate(((2, 1), (3, 1), (5, 6), (5, 7)), 3):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    assert _dissolve_bend(g, 1, DEFAULTS["opendrive"]["junction_margin"], None)
    assert 4 not in g.nodes and 2 not in g.edges and g.metadata["straightened_bends"] == [4]
    e = g.edges[1]
    assert (e.from_node, e.to_node, e.lanes_forward, e.lanes_backward) == (1, 5, 2, 1)
    assert len(e.profile) == 2 and max(e.profile) <= 2.0 and min(e.profile) >= 0.0


def test_dissolve_bend_needs_the_same_road():
    from roadgen.generators.repair import _dissolve_bend

    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 4: (10.0, 10.0), 5: (20.0, 0.0)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 4, "avenue", 2, 2))
    g.add_edge(Edge(2, 4, 5, "street", 1, 1))  # another class: not one road
    assert not _dissolve_bend(g, 1, DEFAULTS["opendrive"]["junction_margin"], None)
    assert set(g.edges) == {1, 2} and 4 in g.nodes
