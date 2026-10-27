from __future__ import annotations

import pytest

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import get_generator, load_params
from roadgen.generators.repair import MIN_ROAD_LENGTH, repair_short_edges, short_edges, stub_roads
from roadgen.graph.io import graph_to_dict
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.base import make_map
from roadgen.maps.model import MAP_KINDS, GridSpec, MapSet
from roadgen.opendrive.builder import OpenDriveBuilder


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
    OpenDriveBuilder().build(g)


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


def _sweep_graph(seed: int):
    gen = get_generator("lsystem")
    params = load_params(gen.params_type, preset="city", defaults=section(DEFAULTS, "generators", "lsystem"))
    grid = GridSpec.from_bounds(gen.map_bounds(params), DEFAULTS["maps"]["grid"]["margin"],
                                DEFAULTS["maps"]["grid"]["cell_size"])
    layers = {}
    for kind in MAP_KINDS:
        name = DEFAULTS["maps"]["pipeline"][kind]
        layers[kind] = make_map(kind, name, grid, seed, defaults=section(DEFAULTS, "maps", kind, name),
                                upstream=MapSet(**layers))
    return gen.generate(params, seed, maps=MapSet(**layers))


def test_sweep_seed_60_now_builds():
    g = _sweep_graph(60)  # 42 m between two junctions needing 21 m each (A0 sweep)
    assert short_edges(g) == []
    OpenDriveBuilder().build(g)


def test_buildable_networks_are_unchanged(monkeypatch):
    # The repair only touches edges the builder would reject: a seed that built
    # before (seed 3) gives the same graph with the repair switched off.
    repaired = graph_to_dict(_sweep_graph(3))
    monkeypatch.setattr("roadgen.generators.lsystem.repair_short_edges", lambda graph, **kw: 0)
    assert graph_to_dict(_sweep_graph(3)) == repaired


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
    OpenDriveBuilder().build(g)
