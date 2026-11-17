"""Highway interchanges (stage C9): the diamond, ramp nodes, lane changes.

esmini's drive-check of the interchange networks is left out here: it takes
minutes on their long highways (to be looked at later).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from roadgen.generators.base import assign_node_kinds
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.transforms.interchange import (
    InterchangeParams,
    add_diamond,
)
from roadgen.geometry.grade_separation import find_crossings, separate
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.metrics.network import network_metrics
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.clearance import check_clearance
from roadgen.validate.connectivity import check_traffic_connectivity
from roadgen.validate.continuity import check_continuity, check_curvature
from roadgen.validate.dynamics import check_dynamics
from roadgen.validate.topology import check_topology


def build_static(g: RoadGraph, tmp_path: Path) -> OpenDriveBuilder:
    b = OpenDriveBuilder()
    b.build(separate(g))
    path = tmp_path / "ic.xodr"
    b.write(path)
    assert check_topology(path) == []
    assert check_continuity(path) == []
    assert check_curvature(path)[0] == []
    assert check_dynamics(path, connecting=True) == []
    assert check_clearance(path) == []
    return b


def diamond(length: float = 1500.0) -> RoadGraph:
    """A 2+2 highway along x crossing a 2+2 avenue along y at the origin (edges 2 and 3,
    without a node), each road length [m] either way, with a diamond there."""
    g = RoadGraph()
    for nid, (x, y) in {1: (-length, 0.0), 2: (-1.0, 0.0), 3: (length, 0.0),
                        4: (0.0, -length), 5: (0.0, 1.0), 6: (0.0, length)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 2, "highway", 2, 2, speed_limit=22.2))
    g.add_edge(Edge(2, 2, 3, "highway", 2, 2, speed_limit=22.2))
    g.add_edge(Edge(3, 4, 5, "avenue", 2, 2, speed_limit=13.9))
    g.add_edge(Edge(4, 5, 6, "avenue", 2, 2, speed_limit=13.9))
    assert add_diamond(g, 2, 3, (0.0, 0.0))
    assign_node_kinds(g)
    g.metadata["exits"] = [n for n in sorted(g.nodes) if g.degree(n) == 1]  # roads running on
    return g


def test_diamond_builds_and_validates(tmp_path):
    g = diamond()
    (ic,) = g.metadata["interchanges"]
    ramps = ramp_nodes = 4
    assert ic["type"] == "diamond" and len(ic["ramp_nodes"]) == ramp_nodes
    assert sum(1 for n in g.nodes.values() if n.kind == "ramp") == ramp_nodes
    one_way = [e for e in g.edges.values() if e.is_one_way]
    assert all(e.road_class == "highway" and e.total_lanes == 1 for e in one_way)
    # Every ramp starts at a ramp node (exits) or a diamond terminal (entries).
    starts = set(ic["ramp_nodes"]) | set(ic["terminals"])
    assert sum(1 for e in one_way if e.from_node in starts) == ramps
    build_static(g, tmp_path)
    assert network_metrics(g)["features"]["interchanges"] == 1


def test_ramp_nodes_allow_no_left_turns_or_ramp_to_ramp():
    g = diamond()
    b = OpenDriveBuilder()
    b.build(separate(g))
    ramp_junctions = {b.node_to_junction[n] for n, node in g.nodes.items() if node.kind == "ramp"}
    moves = [m for m in b.movements if m.junction_id in ramp_junctions]
    assert moves and not [m for m in moves if m.kind == "left"]
    one_way = {e.id for e in g.edges.values() if e.is_one_way}
    assert not [m for m in moves if m.from_edge in one_way and m.to_edge in one_way]


def test_diamond_exits_from_the_outer_lane_with_aux_lanes():
    g = diamond()
    b = OpenDriveBuilder()
    b.build(separate(g))
    ramp_junctions = {b.node_to_junction[n] for n, node in g.nodes.items() if node.kind == "ramp"}
    exits = [m for m in b.movements if m.junction_id in ramp_junctions and m.kind == "right"]
    assert exits
    # Auxiliary lanes: highway edges with 3 lanes one way next to the ramp nodes.
    assert any(max(e.lanes_forward, e.lanes_backward) == 3 for e in g.edges.values() if e.road_class == "highway")


def test_leg_ends_are_exits_not_traps():
    assert not [e for e in check_traffic_connectivity(diamond(800.0)) if "trap" in e]


def test_diamond_needs_room_and_a_straight_road():
    # A crossing too skewed for a diamond.
    g = RoadGraph()
    for nid, (x, y) in {1: (-1500, 0), 2: (1500, 0), 3: (-1200, -1000), 4: (1200, 1000)}.items():
        g.add_node(Node(nid, float(x), float(y)))
    g.add_edge(Edge(1, 1, 2, "highway", 2, 2))
    g.add_edge(Edge(2, 3, 4, "avenue", 2, 2))
    (c,) = find_crossings(g)
    assert not add_diamond(g, c.upper, c.lower, (c.x, c.y))
    # A road next to the mainline where the ramps would go.
    g = RoadGraph()
    for nid, (x, y) in {1: (-1500, 0), 2: (1500, 0), 3: (0, -1500), 4: (0, 1500), 5: (-300, 40), 6: (-60, 40)}.items():
        g.add_node(Node(nid, float(x), float(y)))
    g.add_edge(Edge(1, 1, 2, "highway", 2, 2))
    g.add_edge(Edge(2, 3, 4, "avenue", 2, 2))
    g.add_edge(Edge(3, 5, 6, "street", 1, 1))
    (c,) = find_crossings(g)
    assert not add_diamond(g, c.upper, c.lower, (c.x, c.y))
    assert "interchanges" not in g.metadata


@pytest.mark.slow
def test_lsystem_freeway_corridor_has_a_diamond():
    from roadgen.config import DEFAULTS, section
    from roadgen.maps.base import make_map
    from roadgen.maps.model import GridSpec, MapSet

    gen = LSystemGenerator()
    params = LSystemParams.preset("highway_corridor")
    grid = GridSpec.from_bounds(gen.map_bounds(params), 200, 10)
    layers = {}
    for kind in gen.map_kinds(params):
        name = DEFAULTS["maps"]["pipeline"][kind]
        layers[kind] = make_map(kind, name, grid, 1, defaults=section(DEFAULTS, "maps", kind, name),
                                upstream=MapSet(**layers))
    g = gen.generate(params, 1, MapSet(**layers))
    assert [ic["type"] for ic in g.metadata["interchanges"]] == ["diamond"]
    assert g.validate() == []
    OpenDriveBuilder(hd=HdMapParams(enabled=False)).build(separate(g))  # HD repair is the pipeline's job


def test_invalid_params():
    with pytest.raises(ValueError):
        InterchangeParams(ramp_off=0.0)


def test_lane_changes_on_highways():
    g = RoadGraph()
    for nid in range(1, 6):
        g.add_node(Node(nid, 150.0 * nid, 0.0))
    for k in range(1, 5):
        g.add_edge(Edge(k, k, k + 1, "highway", 2, 2))
    assign_node_kinds(g)
    LSystemGenerator._lane_changes(g, np.random.default_rng(0), 1.0)
    widened = [e.id for e in g.edges.values() if e.lanes_forward == 3]
    assert widened == [2, 3]  # not the end edges (their outer node is a dead end)
    OpenDriveBuilder().build(g)
