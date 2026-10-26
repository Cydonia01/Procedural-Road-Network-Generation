"""Network metrics (stage C0): junction legs, skew, offset T pairs, CNR, orientation, blocks."""

from __future__ import annotations

import json
import math

import pytest

from cli import main
from roadgen.graph.io import save_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.metrics.network import (
    junction_skew,
    network_metrics,
    offset_t_pairs,
    orientation,
)


def graph_of(points: dict[int, tuple[float, float]], edges: list[tuple[int, int]], **meta) -> RoadGraph:
    g = RoadGraph(metadata={"name": "test", **meta})
    for nid, (x, y) in points.items():
        g.add_node(Node(nid, x, y))
    for k, (a, b) in enumerate(edges, 1):
        g.add_edge(Edge(k, a, b, "street", 1, 1))
    return g


def lattice(n: int, spacing: float = 100.0) -> RoadGraph:
    pts = {i * n + j + 1: (i * spacing, j * spacing) for i in range(n) for j in range(n)}
    edges = [(i * n + j + 1, i * n + j + 2) for i in range(n) for j in range(n - 1)]
    edges += [(i * n + j + 1, (i + 1) * n + j + 1) for i in range(n - 1) for j in range(n)]
    return graph_of(pts, edges)


def star(legs: int, length: float = 100.0, angles=None) -> RoadGraph:
    angles = angles or [360.0 * k / legs for k in range(legs)]
    pts = {1: (0.0, 0.0)}
    for k, a in enumerate(angles, 2):
        pts[k] = (length * math.cos(math.radians(a)), length * math.sin(math.radians(a)))
    return graph_of(pts, [(1, k) for k in range(2, len(angles) + 2)])


def test_lattice_counts_cnr_and_orientation():
    m = network_metrics(lattice(3))
    assert m["counts"]["junctions"] == 5 and m["counts"]["dead_ends"] == 0
    assert m["counts"]["two_road_nodes"] == 4  # the corners
    assert m["legs"] == {"1": 0, "3": 4, "4": 1, "5": 0, "6": 0, "7+": 0}
    assert m["cnr"] == 1.0
    assert m["skew_deg"]["max"] == 0.0 and m["skewed_share"] == 0.0
    assert m["orientation_entropy"] == pytest.approx(math.log(4), abs=1e-4)
    assert m["orientation_order"] == pytest.approx(1.0)
    assert m["block_area_m2"]["n"] == 4 and m["block_area_m2"]["max"] == pytest.approx(10000.0)
    assert m["counts"]["area_km2"] == pytest.approx(0.04)
    assert m["intersections_per_km2"] == pytest.approx(125.0)


def test_star_legs_and_skew():
    m = network_metrics(star(5))
    assert m["legs"]["5"] == 1 and m["legs"]["1"] == 5
    assert m["cnr"] == pytest.approx(1 / 6, abs=1e-4)
    assert m["skew_deg"]["max"] == pytest.approx(18.0)
    assert m["min_arm_angle_deg"]["max"] == pytest.approx(72.0)


@pytest.mark.parametrize("angles, skew", [
    ([0, 90, 180], 0.0),  # T
    ([0, 120, 240], 30.0),  # Y with equal angles
    ([0, 60, 180, 240], 30.0),  # cross skewed by 30 deg
    ([0, 90, 180, 270], 0.0),  # right-angled cross
])
def test_junction_skew(angles, skew):
    assert junction_skew(star(len(angles), angles=angles), 1) == pytest.approx(skew)


def test_offset_t_pairs():
    # Through road along x with T junctions at x = 100 and x = 130.
    pts = {1: (0, 0), 2: (100, 0), 3: (130, 0), 4: (300, 0), 5: (100, 100), 6: (130, -100)}
    staggered = graph_of(pts, [(1, 2), (2, 3), (3, 4), (2, 5), (3, 6)])
    assert offset_t_pairs(staggered, 50.0) == 1
    assert offset_t_pairs(staggered, 20.0) == 0  # the joining edge is 30 m
    same_side = graph_of({**pts, 6: (130, 100)}, [(1, 2), (2, 3), (3, 4), (2, 5), (3, 6)])
    assert offset_t_pairs(same_side, 50.0) == 0


def test_orientation_spread_lowers_order():
    entropy_grid, phi_grid = orientation(lattice(3), 36)
    entropy_star, phi_star = orientation(star(9), 36)  # 9 bearings, 18 bins
    assert entropy_star > entropy_grid and phi_star < phi_grid


def test_tortuosity_and_features():
    pts = {1: (0, 0), 2: (100, 30), 3: (200, 0)}  # one road with a bow node
    g = graph_of(pts, [(1, 2), (2, 3)], roundabouts=[{"ring": [1]}], bridges=[1, 2])
    m = network_metrics(g)
    assert m["tortuosity"]["n"] == 1
    assert m["tortuosity"]["max"] == pytest.approx(2 * math.hypot(100, 30) / 200, abs=1e-4)
    assert m["curved_length_share"] == 1.0
    assert m["features"]["roundabouts"] == 1 and m["features"]["bridges"] == 2


def test_cli_metrics_graph(tmp_path):
    path = tmp_path / "grid.json"
    save_json(lattice(3), path)
    out = tmp_path / "report.json"
    assert main(["metrics", "--graph", str(path), "--json", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["graphs"][0]["graph"] == "grid.json"
    assert report["graphs"][0]["legs"]["4"] == 1
