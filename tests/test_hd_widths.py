"""What the HD widths (medians, turn pockets; no side lanes since polish N1) change
outside the .xodr: the ground cut, bridges, overpasses and buildings."""

from __future__ import annotations

import numpy as np
from lxml import etree
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from cli import main
from roadgen.geometry.cross_section import CrossSectionParams
from roadgen.geometry.grade_separation import find_crossings, separate
from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.drape import drape
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.plot import lane_polygons
from roadgen.scene.buildings import BuildingParams, footprints
from roadgen.scene.ground import GroundParams, ground_mesh
from roadgen.scene.surface import read_surfaces
from tests.grids import GRID_CLI
from tests.test_elevation import GRID, elevation, terrain
from tests.test_grade_separation import crossing_graph

CS = CrossSectionParams()


def _polys(root: etree._Element, roads: set[str] | None = None) -> list[Polygon]:
    return [Polygon(p).buffer(0) for r in root.findall("road") if roads is None or r.get("id") in roads
            for _, p in lane_polygons(r, step=0.25)]


def _street(cross_section=None, y: float = 100.0, road_class: str = "street", lanes: int = 1) -> RoadGraph:
    """One two-way road along y from x = 100 to 500, 1 m up."""
    g = RoadGraph()
    g.add_node(Node(1, 100.0, y, z=1.0))
    g.add_node(Node(2, 500.0, y, z=1.0))
    g.add_edge(Edge(1, 1, 2, road_class, lanes, lanes, cross_section=cross_section))
    return g


def test_ground_meets_the_road_edge(tmp_path):
    g = _street()
    b = OpenDriveBuilder(hd=HdMapParams(enabled=True))
    b.build(g)
    b.write(tmp_path / "s.xodr")
    mesh = ground_mesh(elevation(5.0), surfaces=read_surfaces(tmp_path / "s.xodr"),
                       params=GroundParams(cell_size=10.0))
    # The cut reaches the driving lane's outer edge (3.5 m off the centre line), less under_road.
    p = GroundParams()
    assert np.isnan(mesh.height(np.array([300.0]), np.array([100.0 + 3.4 - p.under_road]))).all()
    assert np.isfinite(mesh.height(np.array([300.0]), np.array([100.0 + 3.6]))).all()
    # There the ground meets the road: its 1 m, less below_road.
    edge = mesh.vertices[np.abs(mesh.vertices[:, 1] - (103.5 - p.under_road)) < 0.05]
    edge = edge[(edge[:, 0] > 200.0) & (edge[:, 0] < 400.0)]
    assert len(edge) and np.allclose(edge[:, 2], 1.0 - p.below_road, atol=0.01)


def test_bridges_span_the_whole_width():
    g = _street(y=97.0)  # the centre line in the map cells 90-100 m; the pond in the cells 100-110 m
    water = np.zeros(GRID.shape, bool)
    xs = np.arange(GRID.shape[1]) * GRID.cell_size + GRID.origin[0] + GRID.cell_size / 2
    ys = np.arange(GRID.shape[0]) * GRID.cell_size + GRID.origin[1] + GRID.cell_size / 2
    xx, yy = np.meshgrid(xs, ys)
    water[(xx > 280.0) & (xx < 320.0) & (yy > 101.0) & (yy < 109.0)] = True  # a pond by the road
    flat, t = elevation(0.0), terrain(water)
    assert drape(g, flat, t).edges[1].bridges == []  # the centre line stays dry
    assert drape(g, flat, t, cross_section=CS).edges[1].bridges  # the lane (to y = 100.5) does not


def test_overpass_spans_the_lower_road():
    g = crossing_graph()
    (c,) = find_crossings(g)
    plain = separate(g).edges[c.upper].bridges[0]
    wide = separate(g, cross_section=CS).edges[c.upper].bridges[0]
    assert wide[1] - wide[0] >= plain[1] - plain[0] > 0.0


def test_buildings_keep_ground_between_them_and_the_roads(tmp_path):
    out = tmp_path / "grid"
    assert main(["--no-history", "pipeline", "lsystem", *GRID_CLI, "--allow-traps", "--hd",
                 "-o", str(out)]) == 0
    g = load_json(out.with_suffix(".json"))
    root = etree.parse(str(out.with_suffix(".xodr"))).getroot()
    normal = {r.get("id") for r in root.findall("road") if r.get("junction") == "-1"}
    surface = unary_union(_polys(root, normal))
    built = HdMapParams().active_cross_section  # the build's, turn pockets included
    params = BuildingParams()
    buildings = unary_union(footprints(g, params, built))
    assert buildings.area > 0
    # setback of ground between the lanes and the buildable area (less the lane polygons'
    # sampling on curves)
    assert buildings.distance(surface) >= params.setback - 0.5
    assert not buildings.contains(Point(0.0, 0.0))
