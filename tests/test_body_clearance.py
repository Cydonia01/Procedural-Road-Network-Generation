"""Width-aware growth and the road-body overlap check (stage R2)."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import LineString

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.clearance import check_body_overlaps
from tests.test_grade_separation import crossing_graph, separate

PLAIN = HdMapParams(enabled=False)


def write(g: RoadGraph, path) -> str:
    b = OpenDriveBuilder(hd=PLAIN)
    b.build(g)
    b.write(path)
    return str(path)


def test_parallel_roads_overlap(tmp_path):
    g = RoadGraph()
    for n, (x, y) in {1: (0, 0), 2: (200, 0), 3: (0, 4), 4: (200, 4)}.items():
        g.add_node(Node(n, x, y))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1))
    g.add_edge(Edge(2, 3, 4, "street", 1, 1))  # 4 m beside it: bodies 7 m wide
    errors = check_body_overlaps(write(g, tmp_path / "par.xodr"))
    assert len(errors) == 1 and "overlap" in errors[0]


def test_junction_and_overpass_are_not_overlaps(tmp_path):
    g = crossing_graph()
    assert check_body_overlaps(write(separate(g), tmp_path / "sep.xodr")) == []  # overpass
    t = RoadGraph()
    for n, (x, y) in {1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (100, 100)}.items():
        t.add_node(Node(n, x, y))
    for k, (a, b) in enumerate([(1, 2), (2, 3), (2, 4)], 1):
        t.add_edge(Edge(k, a, b, "street", 1, 1))
    assert check_body_overlaps(write(t, tmp_path / "t.xodr")) == []


def _bodies_apart(g: RoadGraph, half: float) -> bool:
    lines = {k: LineString([(g.nodes[e.from_node].x, g.nodes[e.from_node].y),
                            (g.nodes[e.to_node].x, g.nodes[e.to_node].y)]) for k, e in g.edges.items()}
    for i, a in g.edges.items():
        for j, b in g.edges.items():
            if j <= i or {a.from_node, a.to_node} & {b.from_node, b.to_node}:
                continue
            if lines[i].distance(lines[j]) < 2 * half - 1e-6:
                return False
    return True


@pytest.mark.parametrize("preset, seed", [("organic_town", 1), ("grid_organic", 3), ("suburb", 2)])
def test_grown_roads_keep_their_bodies_apart(preset, seed):
    # Cul-de-sac bulbs and roundabouts are added after growth, with their own checks.
    p = LSystemParams.preset(preset, dead_ends=False, suburb_culdesac_prob=0.0, roundabout_prob=0.0)
    g = LSystemGenerator().generate(p, seed)
    half = sum(p.minor_lanes) * p.lane_width / 2 + p.side_width
    assert _bodies_apart(g, half)


def test_body_check_off_keeps_old_growth():
    on = LSystemGenerator().generate(LSystemParams.preset("grid"), 0)
    off = LSystemGenerator().generate(LSystemParams.preset("grid", body_check=False), 0)
    assert len(on.edges) == len(off.edges)  # a plain lattice is far from any limit


def test_min_angle_grows_with_width():
    import numpy as np

    from roadgen.generators.lsystem import HIGHWAY, STREET_X, _Growth

    p = LSystemParams(min_angle_deg=10.0, min_segment_length=30.0)
    g = _Growth(p, np.random.default_rng(0))
    wide = g.min_angle(HIGHWAY, STREET_X)
    assert wide > 10.0
    assert wide == pytest.approx(math.degrees(math.asin((g.half_width[HIGHWAY] + g.half_width[STREET_X]) / 30.0)))
    assert _Growth(LSystemParams(min_angle_deg=10.0, body_check=False), np.random.default_rng(0)) \
        .min_angle(HIGHWAY, STREET_X) == 10.0
