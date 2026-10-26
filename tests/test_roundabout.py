"""Roundabouts and U-turns (stage C7)."""

from __future__ import annotations

import numpy as np
import pytest

from roadgen.generators.base import assign_node_kinds
from roadgen.generators.junction import JunctionGenerator, JunctionLayoutParams
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.transforms.culdesac import add_culdesacs
from roadgen.generators.transforms.roundabout import add_roundabouts
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.metrics.network import network_metrics
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.junctions import JunctionParams, RoadEnd, u_turn_lane
from roadgen.geometry.planar import Pose
from roadgen.validate.connectivity import check_traffic_connectivity
from tests.test_junction_variety import build_and_check, star


def roundabout(angles, lanes=(1, 1), **kw) -> tuple[RoadGraph, int]:
    g = star(angles, lanes=(lanes,), length=250.0)
    for e in g.edges.values():
        e.road_class = "avenue" if sum(lanes) >= 4 else "street"
    n = add_roundabouts(g, np.random.default_rng(0), lambda node: 1.0, 25.0, 32.0, 8.3, **kw)
    assign_node_kinds(g)
    return g, n


@pytest.mark.parametrize("angles, lanes", [
    ([0, 120, 240], (1, 1)),
    ([0, 90, 200, 300], (1, 1)),
    ([0, 90, 200, 300], (2, 2)),
    ([0, 72, 144, 216, 288], (1, 1)),
    ([0, 55, 180, 235], (2, 2)),
])
def test_roundabouts_build_validate_and_drive(angles, lanes, tmp_path):
    g, n = roundabout(angles, lanes)
    assert n == 1 and 1 not in g.nodes
    (rb,) = g.metadata["roundabouts"]
    assert len(rb["entries"]) == len(angles) and rb["lanes"] == (2 if sum(lanes) >= 4 else 1)
    assert rb["radius"] >= (32.0 if sum(lanes) >= 4 else 25.0)
    ring = [e for e in g.edges.values() if e.from_node in rb["ring"] and e.to_node in rb["ring"]]
    assert all(e.is_one_way for e in ring)
    for e in ring:  # counter-clockwise around the centre
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        assert a.x * b.y - a.y * b.x > 0
    add_culdesacs(g, np.random.default_rng(0), lambda node: 1.0, 15.0, 8.3, road_classes=("street", "avenue"))
    assign_node_kinds(g)
    assert check_traffic_connectivity(g) == []
    build_and_check(g, tmp_path)
    assert network_metrics(g)["features"]["roundabouts"] == 1


def test_roundabouts_need_room_and_spread_arms():
    assert roundabout([0, 40, 180])[1] == 0  # arms closer than 50 deg
    assert roundabout([0, 90, 180, 270, 300, 330])[1] == 0  # 6 roads
    g = star([0, 90, 180, 270], length=250.0)
    g.edges[1].road_class = "highway"
    assert add_roundabouts(g, np.random.default_rng(0), lambda node: 1.0, 25.0, 32.0, 8.3) == 0
    g = star([0, 90, 180, 270], length=250.0)  # a road passing right next to the junction
    g.add_node(Node(10, -100.0, 20.0))
    g.add_node(Node(11, 100.0, 20.0))
    g.add_edge(Edge(10, 10, 11, "street", 1, 1))
    assert add_roundabouts(g, np.random.default_rng(0), lambda node: 1.0, 25.0, 32.0, 8.3) == 0
    assert roundabout([0, 120, 240], allowed=lambda fp: False)[1] == 0


def test_roundabouts_in_lsystem_and_junction_generator():
    g = LSystemGenerator().generate(LSystemParams.preset("suburb"), 1)
    assert network_metrics(g)["features"]["roundabouts"] > 0
    OpenDriveBuilder().build(g)
    rings = sum(bool(JunctionGenerator().generate(JunctionLayoutParams.preset("roundabout"), s)
                     .metadata.get("roundabouts")) for s in range(20))
    assert rings >= 18
    plain = LSystemGenerator().generate(LSystemParams.preset("suburb", roundabout_prob=0.0), 1)
    assert "roundabouts" not in plain.metadata


# --- U-turns ------------------------------------------------------------------------------


def test_u_turn_lane_needs_a_wide_road():
    wide = RoadEnd(1, 1, "end", Pose(0, 0, 0), 3, 3, 3.5, 13.9)
    assert u_turn_lane(wide, 5.0) == (-1, 3)  # 0.5 + 2.5 lane widths = 10.5 m apart
    assert u_turn_lane(RoadEnd(1, 1, "end", Pose(0, 0, 0), 2, 2, 3.5, 13.9), 5.0) is None
    assert u_turn_lane(RoadEnd(1, 1, "end", Pose(0, 0, 0), 2, 0, 3.5, 13.9), 5.0) is None  # one-way


def test_u_turns_on_wide_roads_only(tmp_path):
    on = JunctionParams(u_turns=True)
    b = build_and_check(star([0, 90, 180, 270], lanes=((3, 3),)), tmp_path, on)
    uturns = [m for m in b.movements if m.kind == "uturn"]
    assert len(uturns) == 4 and all(m.from_road == m.to_road for m in uturns)
    assert not any(m.kind == "uturn" for m in _moves(star([0, 90, 180, 270]), on))  # 2 + 2 lanes
    assert not any(m.kind == "uturn" for m in _moves(star([0, 90, 180, 270], lanes=((3, 3),)), JunctionParams()))


def _moves(g: RoadGraph, params: JunctionParams):
    b = OpenDriveBuilder(junctions=params)
    b.build(g)
    return b.movements


def test_invalid_u_turn_radius():
    with pytest.raises(ValueError):
        JunctionParams(u_turn_radius=0.0)
