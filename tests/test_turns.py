"""Stage A7: curvature-continuous junction turns and turn speeds."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from roadgen.geometry import design
from roadgen.geometry.planar import Pose
from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.junctions import TurnPath, turn_path, turn_speed
from roadgen.validate.continuity import check_curvature
from roadgen.validate.dynamics import check_dynamics

FIXTURES = Path(__file__).parent / "fixtures"


def test_right_turn_is_three_clothoids_with_zero_end_curvature():
    path = turn_path(Pose(0.0, 0.0, 0.0), Pose(12.0, -12.0, -math.pi / 2))
    assert path.kind == "clothoid" and len(path.geometries) == 3
    assert 0 < path.max_curvature < 1.0
    first, last = path.geometries[0][0], path.geometries[-1][0]
    assert first.curvstart == 0.0 and abs(last.curvend) < 1e-9


def test_straight_ahead_is_a_line():
    path = turn_path(Pose(0.0, 0.0, 0.0), Pose(20.0, 0.0, 0.0))
    assert path.kind == "line" and path.max_curvature == 0.0


def test_turn_speed_follows_the_tightest_radius():
    tight = TurnPath([], 20.0, 1 / 10.0, "clothoid")  # 10 m radius
    v = turn_speed(tight, 13.9)
    assert v == pytest.approx(design.advisory_speed(10.0, 0.0))
    assert v * v / 10.0 <= 3.0
    gentle = TurnPath([], 20.0, 1 / 500.0, "clothoid")
    assert turn_speed(gentle, 13.9) == 13.9


@pytest.mark.parametrize("name", ["single_4way", "acute_T", "4way_avenue_x_street"])
def test_junctions_are_curvature_continuous_and_drivable(tmp_path, name):
    b = OpenDriveBuilder()
    b.build(load_json(FIXTURES / f"{name}.json"))
    path = tmp_path / f"{name}.xodr"
    b.write(path)
    assert check_curvature(path) == ([], [])
    assert check_dynamics(path) == []
    assert all(m.path in ("line", "clothoid") for m in b.movements)
