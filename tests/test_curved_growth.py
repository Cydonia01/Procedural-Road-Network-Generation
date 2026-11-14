"""Curved streets from the lsystem generator: tortuosity bows and the contour pattern."""

from __future__ import annotations

import math

import numpy as np

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
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


def test_tortuosity_bows_streets_in_both_goals():
    for preset in ("city", "organic_town"):
        plain = grow(preset, extent=(900.0, 700.0))
        bowed = grow(preset, extent=(900.0, 700.0), curve_tortuosity=1.05)
        assert bowed == grow(preset, extent=(900.0, 700.0), curve_tortuosity=1.05)  # deterministic
        bends = sum(bowed.degree(n) == 2 for n in bowed.nodes) - sum(plain.degree(n) == 2 for n in plain.nodes)
        assert bends > 10, preset


def test_bowed_network_builds_smooth_and_drivable(tmp_path):
    from lxml import etree

    b = OpenDriveBuilder()
    b.build(grow("city", seed=2, extent=(900.0, 700.0), curve_tortuosity=1.03))
    b.write(tmp_path / "c.xodr")
    tree = etree.parse(str(tmp_path / "c.xodr"))
    assert check_continuity_tree(tree) == []
    assert check_curvature_tree(tree) == ([], [])
    assert check_dynamics_tree(tree) == []


def test_hill_town_turns_streets_along_the_contours():
    p = LSystemParams.preset("hill_town", extent=(1200.0, 900.0), highways=False)
    grid = GridSpec((-200.0, -200.0), (1600.0, 1300.0), 10.0)
    x, y = grid.cell_centers()
    # A 10 % slope rising to the north-east: contours run diagonally, 45 deg off the grid.
    ramp = MapSet(elevation=MapLayer("elevation", grid, (0.1 * (x + y) / math.sqrt(2)).astype(np.float32)))
    curved = LSystemGenerator().generate(p, 3, maps=ramp)
    straight = LSystemGenerator().generate(LSystemParams(**{**p.__dict__, "pattern_weights": (1, 0, 0, 0, 0)}),
                                           3, maps=ramp)
    assert street_headings(curved).mean() > street_headings(straight).mean() + 10.0


def test_hill_town_ignores_the_style_map():
    from roadgen.generators.base import get_generator

    gen = get_generator("lsystem")
    assert "style" not in gen.map_kinds(LSystemParams.preset("hill_town"))
    assert "style" in gen.map_kinds(LSystemParams.preset("mixed_city"))


