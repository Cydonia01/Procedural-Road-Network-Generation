"""Stage A4: fitted roads seen by the maps (align, drape) and the scene (buildings)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from cli import main
from roadgen.geometry.alignment import network_alignment
from roadgen.graph.io import graph_to_dict, load_json, save_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.align import align
from roadgen.maps.model import LAND, WATER, GridSpec, MapLayer, save_npz
from roadgen.scene.buildings import (
    BuildingParams,
    footprints,
    road_half_extent,
    road_lines,
)

GRID = GridSpec((-100.0, -100.0), (700.0, 700.0), 5.0)
X, Y = GRID.cell_centers()


def l_shape() -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (400.0, 0.0), 3: (400.0, 400.0)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1))
    g.add_edge(Edge(2, 2, 3, "street", 1, 1))
    return g


def curve_points(g: RoadGraph) -> np.ndarray:
    (ca,) = network_alignment(g).chains
    return ca.alignment.sample(1.0).xy()


def test_lake_inside_a_corner_caps_its_radius():
    g = l_shape()
    lake = np.hypot(X - 362.0, Y - 38.0) < 12.0  # on the design curve, clear of both legs
    terrain = MapLayer("terrain", GRID, np.where(lake, WATER, LAND).astype(np.uint8))
    assert np.any(terrain.sample(*curve_points(g).T) == WATER)
    out = align(g, terrain=terrain)
    assert g.nodes[2].radius_cap is None  # input untouched
    cap = out.nodes[2].radius_cap
    design_radius = network_alignment(g).chains[0].corners[0].radius
    assert cap is not None and cap < design_radius
    assert not np.any(terrain.sample(*curve_points(out).T) == WATER)
    assert network_alignment(out).chains[0].corners[0].radius == pytest.approx(cap, abs=1e-3)
    assert out.nodes[1].radius_cap is None and out.nodes[3].radius_cap is None


def test_steep_bump_inside_a_corner_caps_its_radius():
    g = l_shape()
    bump = np.clip(30.0 - np.hypot(X - 360.0, Y - 40.0), 0.0, None)  # 1:1 cone, flat elsewhere
    elevation = MapLayer("elevation", GRID, bump.astype(np.float32))
    out = align(g, elevation=elevation)
    assert out.nodes[2].radius_cap is not None
    # Grades are measured over 25 m: the capped curve may clip the cone's rim, but
    # climbs far less of it than the design curve.
    assert elevation.sample(*curve_points(out).T).max() < 0.5 * elevation.sample(*curve_points(g).T).max()


def test_no_maps_no_caps_and_json_unchanged():
    g = l_shape()
    out = align(g)
    assert graph_to_dict(out) == graph_to_dict(g)
    assert all("radius_cap" not in n for n in graph_to_dict(out)["nodes"])


def test_align_cli(tmp_path):
    g = l_shape()
    save_json(g, tmp_path / "g.json")
    lake = np.hypot(X - 362.0, Y - 38.0) < 12.0
    save_npz(MapLayer("terrain", GRID, np.where(lake, WATER, LAND).astype(np.uint8)), tmp_path / "t.npz")
    assert main(["align", str(tmp_path / "g.json"), "--map-file", f"terrain={tmp_path / 't.npz'}"]) == 0
    assert load_json(tmp_path / "g.aligned.json").nodes[2].radius_cap is not None


def test_buildings_follow_the_curves():
    # A 400 m square of bends: its block is cut at the corners, and no building
    # footprint overlaps a fitted road.
    g = RoadGraph()
    corners = {1: (0.0, 0.0), 2: (400.0, 0.0), 3: (400.0, 400.0), 4: (0.0, 400.0)}
    for nid, (x, y) in corners.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate([(1, 2), (2, 3), (3, 4), (4, 1)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    lines = road_lines(g)
    assert any(len(line.coords) > 2 for _, line in lines)  # curved, not straight edges
    params = BuildingParams(seed=1, setback=2.0)
    feet = footprints(g, params)
    assert feet
    for poly in feet:
        for e, line in lines:
            assert not poly.intersects(line.buffer(road_half_extent(e)))
    square = Polygon(corners.values())
    assert sum(p.area for p in feet) < square.area
    # The road passes inside the square's corner, so the corner itself is not built on.
    assert not any(p.contains(LineString([(5.0, 5.0), (6.0, 6.0)])) for p in feet)
    assert math.isfinite(sum(p.area for p in feet))
