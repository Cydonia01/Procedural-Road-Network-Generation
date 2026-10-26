"""Junction variety (stage C5): 5-7 legs, skew, K shapes, offset T pairs, lane splits."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.transforms.stagger import stagger_crossings
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.metrics.network import network_metrics, offset_t_pairs
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.junctions import JunctionParams, RoadEnd, plan_movements, straight_pairs
from roadgen.geometry.planar import Pose
from roadgen.validate.continuity import check_continuity, check_curvature
from roadgen.validate.dynamics import check_dynamics
from roadgen.validate.topology import check_topology
from tests.test_junctions import _rm_available


def star(angles_deg, lanes=((2, 2),), length: float = 150.0) -> RoadGraph:
    """A junction at the origin with one road per angle; lanes repeat over the arms."""
    g = RoadGraph(metadata={"name": "star"})
    g.add_node(Node(1, 0.0, 0.0))
    for k, a in enumerate(angles_deg):
        g.add_node(Node(k + 2, length * math.cos(math.radians(a)), length * math.sin(math.radians(a))))
        fwd, bwd = lanes[k % len(lanes)]
        g.add_edge(Edge(k + 1, k + 2, 1, "avenue", fwd, bwd))
    return g


def build_and_check(g: RoadGraph, tmp_path: Path, params: JunctionParams = JunctionParams()) -> OpenDriveBuilder:
    b = OpenDriveBuilder(junctions=params)
    b.build(g)
    path = tmp_path / "net.xodr"
    b.write(path)
    assert check_topology(path) == []
    assert check_continuity(path) == []
    assert check_curvature(path)[0] == []
    assert check_dynamics(path, connecting=True) == []
    if _rm_available():
        from roadgen.sim.drive_check import drive_lanes, drive_movements

        assert [r.describe() for r in drive_movements(path) if not r.ok] == []
        assert [r.describe() for r in drive_lanes(path) if not r.ok] == []
    return b


SHAPES = {
    "five_way": [0, 72, 144, 216, 288],
    "six_way": [0, 60, 120, 180, 240, 300],
    "seven_way": [0, 51.4, 102.9, 154.3, 205.7, 257.1, 308.6],
    "y_30": [270, 75, 105],  # a stem and two arms 30 deg apart
    "k": [90, 270, 20, 340],  # a through road and two diagonals on one side
    "skewed_cross": [0, 60, 180, 240],
}


@pytest.mark.parametrize("name", SHAPES)
def test_shapes_build_and_drive(name, tmp_path):
    g = star(SHAPES[name], lanes=((2, 2), (1, 1)))
    b = build_and_check(g, tmp_path)
    assert len(b.node_to_junction) == 1
    legs = len(SHAPES[name])
    assert network_metrics(g)["legs"][str(legs) if legs < 7 else "7+"] == 1


# --- lane splits ------------------------------------------------------------------------


def test_straight_pairs_policies():
    assert straight_pairs([-1], [1, 2], "none") == [(-1, 1)]
    assert straight_pairs([-1], [1, 2], "outer") == [(-1, 1), (-1, 2)]
    assert straight_pairs([-1, -2], [1, 2, 3], "outer") == [(-1, 1), (-2, 2), (-2, 3)]
    assert straight_pairs([-1, -2], [1, 2, 3], "inner") == [(-1, 1), (-1, 2), (-2, 3)]
    assert straight_pairs([-1, -2], [1], "outer") == [(-1, 1)]  # merges are fill_pair's


def end(edge: int, hdg: float, right: int, left: int) -> RoadEnd:
    """A road arriving at the origin with heading hdg (contact end)."""
    return RoadEnd(edge, edge, "end", Pose(-50 * math.cos(hdg), -50 * math.sin(hdg), hdg), right, left,
                   3.5, 13.9)


def test_lane_split_feeds_every_outgoing_lane():
    # A 1+1 street continues straight as a 2+2 avenue at a T junction.
    ends = [end(1, 0.0, 1, 1), RoadEnd(2, 2, "start", Pose(0, 0, 0.0), 2, 2, 3.5, 13.9),
            end(3, math.pi / 2, 1, 1)]
    for policy, fed in (("outer", {-1, -2}), ("none", {-1})):
        moves = plan_movements(1, ends, 10, JunctionParams(lane_split=policy))
        straight = {m.to_lane for m in moves if m.from_edge == 1 and m.to_edge == 2}
        assert straight == fed


def test_lane_split_builds_and_drives(tmp_path):
    g = star([180, 0, 90], lanes=((1, 1), (2, 2), (1, 1)))
    b = build_and_check(g, tmp_path)
    from_street = [m for m in b.movements if m.from_edge == 1 and m.to_edge == 2]
    assert len(from_street) == 2 and len({m.from_lane for m in from_street}) == 1


def test_invalid_lane_split():
    with pytest.raises(ValueError):
        JunctionParams(lane_split="middle")


# --- staggered crossings ----------------------------------------------------------------


def crossing() -> RoadGraph:
    g = star([0, 90, 180, 270], lanes=((2, 2), (1, 1)))
    return g


def test_stagger_makes_an_offset_t_pair(tmp_path):
    g = crossing()
    assert stagger_crossings(g, 1.0, 35.0, np.random.default_rng(0)) == 1
    assert sorted(g.degree(n) for n in g.nodes) == [1, 1, 1, 1, 3, 3]
    assert offset_t_pairs(g, 50.0) == 1
    assert g.validate() == []
    build_and_check(g, tmp_path)


def test_stagger_keeps_room_between_the_ts():
    g = crossing()
    assert stagger_crossings(g, 1.0, 10.0, np.random.default_rng(0)) == 0  # too close: undone
    assert sorted(g.degree(n) for n in g.nodes) == [1, 1, 1, 1, 4]
    g = star([0, 70, 180, 230])  # not a crossing of two straight roads
    assert stagger_crossings(g, 1.0, 35.0, np.random.default_rng(0)) == 0


def test_stagger_in_lsystem():
    p = LSystemParams.preset("grid", stagger_prob=0.5)
    g = LSystemGenerator().generate(p, 1)
    plain = LSystemGenerator().generate(LSystemParams.preset("grid"), 1)
    assert network_metrics(g)["offset_t_pairs"] > 10 == network_metrics(plain)["offset_t_pairs"] + 10
    OpenDriveBuilder().build(g)
