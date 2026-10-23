"""Stage A9: curved streets from the lsystem generator."""

from __future__ import annotations

import math

import numpy as np
import pytest

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams, _max_turn
from roadgen.graph.io import graph_to_dict
from roadgen.maps.model import GridSpec, MapLayer, MapSet
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.continuity import check_continuity_tree, check_curvature_tree
from roadgen.validate.dynamics import check_dynamics_tree


def grow(preset: str, seed: int = 1, maps=None, **overrides):
    return LSystemGenerator().generate(LSystemParams.preset(preset, **overrides), seed, maps=maps)


def street_headings(g) -> np.ndarray:
    """Deviation [deg] of every street edge from the nearer grid axis."""
    out = []
    for e in g.edges.values():
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        h = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 90.0
        out.append(min(h, 90.0 - h))
    return np.array(out)


def test_none_mode_ignores_the_curve_parameters():
    plain = grow("grid_organic")
    tuned = grow("grid_organic", curve_drift_deg=20.0, curve_tortuosity=1.2)
    assert graph_to_dict(plain)["edges"] == graph_to_dict(tuned)["edges"]


def test_drift_bows_streets_and_keeps_turns_drivable():
    g = grow("organic_curved")
    assert g == grow("organic_curved")  # deterministic
    bends = [n for n in g.nodes if g.degree(n) == 2]
    assert len(bends) > 10
    # Each step's turn is limited (snapping onto existing nodes can add a little).
    p = LSystemParams.preset("organic_curved")
    limit = math.degrees(max(_max_turn(p, 0), _max_turn(p, 1)))
    turns = []
    for n in bends:
        a, b = (e for e in g.incident_edges(n))
        pa = g.nodes[a.to_node if a.from_node == n else a.from_node]
        pb = g.nodes[b.to_node if b.from_node == n else b.from_node]
        node = g.nodes[n]
        turns.append(180.0 - math.degrees(abs(math.atan2(
            (pa.x - node.x) * (pb.y - node.y) - (pa.y - node.y) * (pb.x - node.x),
            (pa.x - node.x) * (pb.x - node.x) + (pa.y - node.y) * (pb.y - node.y)))))
    assert np.median(turns) < 40.0 and np.mean(np.array(turns) <= limit + 1e-6) > 0.95
    assert street_headings(g).mean() > street_headings(grow("grid_organic")).mean()


def test_drift_network_builds_smooth_and_drivable(tmp_path):
    from lxml import etree

    b = OpenDriveBuilder()
    b.build(grow("organic_curved", seed=2))
    b.write(tmp_path / "c.xodr")
    tree = etree.parse(str(tmp_path / "c.xodr"))
    assert check_continuity_tree(tree) == []
    assert check_curvature_tree(tree) == ([], [])
    assert check_dynamics_tree(tree) == []


def test_contour_mode_turns_streets_along_the_contours():
    p = LSystemParams.preset("hill_town", extent=(1200.0, 900.0), highways=False)
    grid = GridSpec((-200.0, -200.0), (1600.0, 1300.0), 10.0)
    x, _ = grid.cell_centers()
    ramp = MapSet(elevation=MapLayer("elevation", grid, (0.1 * x).astype(np.float32)))  # contours run along y
    curved = LSystemGenerator().generate(p, 3, maps=ramp)
    straight = LSystemGenerator().generate(LSystemParams(**{**p.__dict__, "curve_mode": "none"}), 3, maps=ramp)
    assert street_headings(curved).mean() > street_headings(straight).mean() + 2.0


def test_curve_params_are_validated():
    with pytest.raises(ValueError):
        LSystemParams(curve_mode="spiral")
    with pytest.raises(ValueError):
        LSystemParams(curve_tortuosity=0.9)
