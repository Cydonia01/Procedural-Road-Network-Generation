"""Connectivity knob and cul-de-sac bulbs (stage C4)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.transforms.culdesac import (
    LOOP_NODES,
    add_culdesacs,
    bulb_points,
    cut_dead_ends,
)
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.metrics.network import arm_headings, network_metrics
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.connectivity import check_traffic_connectivity, exits
from roadgen.validate.continuity import check_continuity
from roadgen.validate.topology import check_topology
from tests.shared import grown

PLAIN = HdMapParams(enabled=False)  # these tests build driving lanes only (HD maps on by default since B10)


def street(*points: tuple[float, float]) -> RoadGraph:
    """A two-way street through points: dead ends at both ends."""
    g = RoadGraph(metadata={"name": "street"})
    for k, (x, y) in enumerate(points, 1):
        g.add_node(Node(k, float(x), float(y)))
    for k in range(1, len(points)):
        g.add_edge(Edge(k, k, k + 1, "street", 1, 1, speed_limit=11.1))
    return g


def bulbs(g: RoadGraph, prob: float = 1.0, radius: float = 15.0, **kw) -> int:
    return add_culdesacs(g, np.random.default_rng(0), lambda n: prob, radius, 8.3, **kw)


# --- the transform ------------------------------------------------------------------------


def test_bulb_points_lie_on_a_circle_ahead():
    pts = bulb_points((0.0, 0.0), 0.0, 15.0)
    assert len(pts) == LOOP_NODES - 1
    assert all(math.dist(p, (15.0, 0.0)) == pytest.approx(15.0) for p in pts)
    assert pts[0][1] < 0 < pts[-1][1]  # first to the right (counter-clockwise loop)


def test_bulbs_turn_dead_ends_into_y_junctions():
    g = street((0, 0), (200, 0))
    assert bulbs(g) == 2
    assert check_traffic_connectivity(g) == []  # no traps left
    for bulb in g.metadata["culdesacs"]:
        end = bulb["node"]
        assert g.degree(end) == 3
        gaps = sorted(math.degrees(b[0] - a[0]) % 360 for a, b in
                      zip(arm_headings(g, end), arm_headings(g, end)[1:] + arm_headings(g, end)[:1]))
        assert gaps == pytest.approx([120.0, 120.0, 120.0])
        loop = [e for e in g.edges.values() if {e.from_node, e.to_node} & set(bulb["loop"])]
        assert len(loop) == LOOP_NODES and all(e.is_one_way and e.lanes_forward == 1 for e in loop)
        assert all(e.speed_limit == 8.3 for e in loop)
    m = network_metrics(g)
    assert m["features"]["culdesacs"] == 2 and m["counts"]["dead_ends"] == 0


def test_bulbs_build_into_valid_opendrive(tmp_path):
    g = street((0, 0), (120, 0), (240, 40))
    bulbs(g)
    b = OpenDriveBuilder(hd=PLAIN)
    b.build(g)
    b.write(tmp_path / "c.xodr")
    assert check_topology(tmp_path / "c.xodr") == []
    assert check_continuity(tmp_path / "c.xodr") == []


def test_blocked_bulbs_retreat_or_stay():
    # A crossing road 20 m beyond the east end: the east bulb moves back along the street.
    g = street((0, 0), (200, 0))
    g.add_node(Node(10, 220, -100))
    g.add_node(Node(11, 220, 100))
    g.add_edge(Edge(10, 10, 11, "street", 1, 1))
    assert bulbs(g) == 4  # the crossing road's two dead ends get bulbs too
    east = next(b for b in g.metadata["culdesacs"] if b["node"] == 2)
    assert g.nodes[2].x < 200 - 1e-6  # moved back
    # Too short to move back: no bulb at a 40 m stub ending next to a road.
    g = street((0, 0), (40, 0))
    g.add_node(Node(10, 55, -100))
    g.add_node(Node(11, 55, 100))
    g.add_edge(Edge(10, 10, 11, "street", 1, 1))
    bulbs(g)
    assert all(b["node"] != 2 for b in g.metadata.get("culdesacs", []))
    assert east["loop"]


def test_prob_classes_and_allowed():
    g = street((0, 0), (200, 0))
    assert bulbs(g, prob=0.0) == 0 and "culdesacs" not in g.metadata
    g = street((0, 0), (200, 0))
    assert bulbs(g, allowed=lambda footprint: False) == 0
    g = street((0, 0), (200, 0))
    g.edges[1].road_class = "avenue"
    assert bulbs(g) == 0  # only streets by default


# --- lsystem ------------------------------------------------------------------------------


def suburb(seed: int = 1, **kw) -> RoadGraph:
    p = LSystemParams(global_goal="field", extent=(1500.0, 1200.0), snap_radius=20.0,
                      min_segment_length=30.0, min_angle_deg=40.0, pattern_weights=(0, 0, 0, 0, 1),
                      suburb_culdesac_prob=0.0, **{"dead_ends": False, **kw})
    return LSystemGenerator().generate(p, seed)


def test_cnr_falls_as_the_detour_ratio_rises():
    # one seed: each of seeds 1-3 alone keeps the order (2026-11-14)
    cnr = [network_metrics(suburb(1, detour_ratio=k))["cnr"] for k in (1.2, 2.0, 4.0, 0.0)]  # 0 = no connectors
    assert cnr[0] >= cnr[1] >= cnr[2] > cnr[3]


@pytest.mark.slow
def test_connectors_keep_the_network_valid():
    g = suburb(3, detour_ratio=1.5)
    assert g.validate() == []
    OpenDriveBuilder(hd=PLAIN).build(g)


def test_culdesac_prob_in_lsystem():
    plain, bulbed = suburb(2), suburb(2, culdesac_prob=1.0)
    m0, m1 = network_metrics(plain), network_metrics(bulbed)
    assert m1["features"]["culdesacs"] > 0
    assert m1["counts"]["dead_ends"] == m0["counts"]["dead_ends"] - m1["features"]["culdesacs"]
    traps = [e for e in check_traffic_connectivity(bulbed) if "trap" in e]
    assert len(traps) == m1["counts"]["dead_ends"] - len(exits(bulbed))  # roads leaving the area are no traps


@pytest.mark.slow
def test_suburb_preset_has_culdesacs_and_builds():
    g = grown("suburb", 1)
    m = network_metrics(g)
    assert m["features"]["culdesacs"] >= 5 and m["counts"]["dead_ends"] > 0
    OpenDriveBuilder(hd=PLAIN).build(g)


def cross(leg_class: str = "street") -> RoadGraph:
    """Four legs from node 1: three end at exits (2, 3, 4), the fourth runs 1 - 5 - 6
    and ends at 6 with no exit."""
    g = RoadGraph(metadata={"exits": [2, 3, 4]})
    for n, (x, y) in {1: (0, 0), 2: (200, 0), 3: (0, 200), 4: (-200, 0), 5: (0, -100), 6: (0, -200)}.items():
        g.add_node(Node(n, x, y))
    for eid, (a, b) in enumerate([(1, 2), (1, 3), (1, 4)], 1):
        g.add_edge(Edge(eid, a, b, "avenue", 1, 1))
    g.add_edge(Edge(4, 1, 5, leg_class, 1, 1))
    g.add_edge(Edge(5, 5, 6, leg_class, 1, 1))
    return g


def test_cut_dead_end_goes_back_to_its_junction():
    g = cross()
    assert cut_dead_ends(g, keep=g.metadata["exits"]) == 1
    assert set(g.nodes) == {1, 2, 3, 4} and set(g.edges) == {1, 2, 3}
    assert not check_traffic_connectivity(g)


def test_cut_keeps_other_classes():
    g = cross(leg_class="highway")
    assert cut_dead_ends(g, keep=g.metadata["exits"]) == 0


def test_cut_steps_the_bigger_road_down_at_a_two_road_node():
    # T: avenue 2 - 1 - 4 and a street 1 - 3 - 7 to an exit. Cutting the avenue's end 4
    # leaves 1 with an avenue and a street: the avenue becomes a street up to its next
    # junction (slower and narrower, so its curves stay within its speed).
    g = cross()
    del g.edges[4], g.edges[5], g.nodes[5], g.nodes[6]
    g.edges[2] = Edge(2, 1, 3, "street", 1, 1, speed_limit=8.3)
    g.add_node(Node(7, 0, 400))
    g.add_edge(Edge(6, 3, 7, "street", 1, 1, speed_limit=8.3))
    g.metadata["exits"] = [2, 7]
    assert cut_dead_ends(g, ("street", "avenue"), keep=g.metadata["exits"]) == 1  # 1 - 4
    assert g.edges[1].road_class == "street" and g.edges[1].speed_limit == 8.3
    assert not check_traffic_connectivity(g)


def test_cut_leaves_a_one_way_ramp_alone():
    g = cross()
    del g.edges[4], g.edges[5], g.nodes[5], g.nodes[6]
    g.edges[2] = Edge(2, 1, 3, "avenue", 1, 0)  # one-way out of 1
    g.metadata["exits"] = [2, 3]
    assert cut_dead_ends(g, ("street", "avenue"), keep=g.metadata["exits"]) == 0


def test_exits_are_no_traps():
    g = street((0, 0), (100, 0), (200, 0))
    assert len([e for e in check_traffic_connectivity(g) if "trap" in e]) == 2
    g.metadata["exits"] = [1]
    assert len([e for e in check_traffic_connectivity(g) if "trap" in e]) == 1
    g.metadata["exits"] = [1, 3]
    assert not check_traffic_connectivity(g)  # in at one end, out at the other, back via the world


def test_cut_lets_a_one_way_ring_run_on():
    # A one-way ring 3 -> 1 -> 4 through entry 1, whose arm 1 - 5 - 6 ends at 6.
    g = cross()
    del g.edges[1], g.edges[2], g.edges[3]
    g.add_edge(Edge(1, 3, 1, "avenue", 1, 0))
    g.add_edge(Edge(2, 1, 4, "avenue", 1, 0))
    g.add_edge(Edge(3, 4, 3, "avenue", 1, 0))
    del g.nodes[2]
    g.metadata = {"exits": [], "roundabouts": [{"entries": [1], "ring": [1, 3, 4]}]}
    assert cut_dead_ends(g, keep=[]) == 1
    assert set(g.edges) == {1, 2, 3} and g.metadata["roundabouts"][0]["entries"] == []
    assert not check_traffic_connectivity(g)
    g.edges[2] = Edge(2, 4, 1, "avenue", 1, 0)  # both into 1: no way on, so no cut
    g.add_node(Node(5, 0, -100)); g.add_node(Node(6, 0, -200))
    g.add_edge(Edge(4, 1, 5, "street", 1, 1)); g.add_edge(Edge(5, 5, 6, "street", 1, 1))
    assert cut_dead_ends(g, keep=[]) == 0
