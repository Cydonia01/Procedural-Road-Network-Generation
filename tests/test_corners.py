"""Stage B3: corner roads close the junction surface; buildings, ground, bridges and
overpasses take the side lanes into account."""

from __future__ import annotations

import json

import numpy as np
import pytest
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


def _junction(tmp_path, *args: str):
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", *args, "--allow-traps", "--hd", "-o", str(out)]) == 0
    return out


def _polys(root: etree._Element, roads: set[str] | None = None) -> list[Polygon]:
    return [Polygon(p).buffer(0) for r in root.findall("road") if roads is None or r.get("id") in roads
            for _, p in lane_polygons(r, step=0.25)]


def _holes(root: etree._Element) -> list[Polygon]:
    """Holes in the union of every lane: islands, and road surface no lane owns."""
    u = unary_union([p.buffer(0.02) for p in _polys(root)])
    return [Polygon(i) for g in getattr(u, "geoms", [u]) for i in g.interiors]


@pytest.mark.parametrize("args", [
    ["build", "tests/fixtures/4way_avenue_x_street.json"],
    ["junction", "--preset", "t"],
    ["junction", "--preset", "skewed_cross"],
    ["junction", "--preset", "five_way"],
], ids=["4-leg", "3-leg", "skewed", "5-leg"])
def test_corner_roads_close_the_junction(tmp_path, args):
    if args[0] == "build":
        xodr = tmp_path / "net.xodr"
        assert main(["--no-history", "build-xodr", args[1], str(xodr), "--hd"]) == 0
    else:
        xodr = _junction(tmp_path, *args).with_suffix(".xodr")
    root = etree.parse(str(xodr)).getroot()
    records = json.loads(xodr.with_suffix(".roadmap.json").read_text())["corner_roads"]
    corners = {str(c["road"]) for c in records if c["closes"]}
    assert corners
    curbs = unary_union(_polys(root, corners))
    # No hole next to a corner road whose right turn is driven (cul-de-sac islands, the
    # slivers between turning lanes inside a junction and the surface by a corner between
    # two one-way roads into the junction are other holes).
    assert [round(h.area, 1) for h in _holes(root) if h.area > 0.5 and h.distance(curbs) < 0.1] == []


def test_corner_roads_lie_outside_the_movements(tmp_path):
    xodr = tmp_path / "net.xodr"
    assert main(["--no-history", "build-xodr", "tests/fixtures/4way_avenue_x_street.json", str(xodr), "--hd"]) == 0
    root = etree.parse(str(xodr)).getroot()
    sidecar = json.loads(xodr.with_suffix(".roadmap.json").read_text())
    corners = {str(c["road"]) for c in sidecar["corner_roads"]}
    moves = {str(m["connecting_road"]) for m in sidecar["movements"]}
    assert len(corners) == 4
    overlap = unary_union(_polys(root, corners)).intersection(unary_union(_polys(root, moves))).area
    assert overlap < 0.5  # they only touch along the right-turn lanes' edges
    for road in root.findall("road"):
        if road.get("id") in corners:
            kinds = {lane.get("type") for lane in road.iter("lane") if lane.get("id") != "0"}
            assert kinds <= {"biking", "curb", "sidewalk", "shoulder"} and "sidewalk" in kinds
            assert all(float(h.get("inner")) == pytest.approx(0.15) for h in road.iter("height"))


def test_corner_roads_need_side_lanes_on_both_arms(tmp_path):
    out = _junction(tmp_path, "junction", "--preset", "t", "--cross-section", "none")
    assert "corner_roads" not in json.loads(out.with_suffix(".roadmap.json").read_text())


def _street(cross_section=None, y: float = 100.0) -> RoadGraph:
    """One 2-lane street along y from x = 100 to 500, 1 m up."""
    g = RoadGraph()
    g.add_node(Node(1, 100.0, y, z=1.0))
    g.add_node(Node(2, 500.0, y, z=1.0))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1, cross_section=cross_section))
    return g


def test_ground_meets_the_sidewalks_at_their_top(tmp_path):
    g = _street()
    hills = elevation(5.0)
    b = OpenDriveBuilder(hd=HdMapParams(enabled=True))
    b.build(g)
    b.write(tmp_path / "s.xodr")
    surfaces = read_surfaces(tmp_path / "s.xodr")
    mesh = ground_mesh(hills, surfaces=surfaces, params=GroundParams(cell_size=10.0))
    # The cut reaches past the driving lanes, parking and curb to the sidewalk's outer edge
    # (3.5 + 2.2 + 0.15 + 2.0 = 7.85 m off the centre line), less under_road.
    p = GroundParams()
    assert np.isnan(mesh.height(np.array([300.0]), np.array([100.0 + 7.6 - p.under_road]))).all()
    assert np.isfinite(mesh.height(np.array([300.0]), np.array([100.0 + 7.9]))).all()
    # There the ground meets the sidewalk's top: the road's 1 m plus 0.15 m, less below_road.
    edge = mesh.vertices[np.abs(mesh.vertices[:, 1] - (107.85 - p.under_road)) < 0.05]
    edge = edge[(edge[:, 0] > 200.0) & (edge[:, 0] < 400.0)]
    assert len(edge) and np.allclose(edge[:, 2], 1.15 - p.below_road, atol=0.01)


def test_bridges_span_the_whole_width():
    g = _street(y=95.0)  # in the map cells 90-100 m; the pond in the cells 100-110 m
    water = np.zeros(GRID.shape, bool)
    xs = np.arange(GRID.shape[1]) * GRID.cell_size + GRID.origin[0] + GRID.cell_size / 2
    ys = np.arange(GRID.shape[0]) * GRID.cell_size + GRID.origin[1] + GRID.cell_size / 2
    xx, yy = np.meshgrid(xs, ys)
    water[(xx > 280.0) & (xx < 320.0) & (yy > 101.0) & (yy < 109.0)] = True  # a pond by the road
    flat, t = elevation(0.0), terrain(water)
    assert drape(g, flat, t).edges[1].bridges == []  # the centre line stays dry
    assert drape(g, flat, t, cross_section=CS).edges[1].bridges  # the sidewalk does not


def test_overpass_spans_the_lower_roads_side_lanes():
    g = crossing_graph()
    (c,) = find_crossings(g)
    plain = separate(g).edges[c.upper].bridges[0]
    wide = separate(g, cross_section=CS).edges[c.upper].bridges[0]
    assert wide[1] - wide[0] > plain[1] - plain[0] + 4.0


def test_buildings_stand_behind_the_sidewalks(tmp_path):
    out = tmp_path / "grid"
    assert main(["--no-history", "pipeline", "lsystem", *GRID_CLI, "--allow-traps", "--hd",
                 "-o", str(out)]) == 0
    g = load_json(out.with_suffix(".json"))
    root = etree.parse(str(out.with_suffix(".xodr"))).getroot()
    normal = {r.get("id") for r in root.findall("road") if r.get("junction") == "-1"}
    surface = unary_union(_polys(root, normal))
    built = HdMapParams().active_cross_section  # the build's, turn pockets included
    buildings = unary_union(footprints(g, BuildingParams(setback=0.0), built))
    assert buildings.area > 0
    assert buildings.intersection(surface).area < 1.0
    plain = unary_union(footprints(g, BuildingParams(setback=0.0)))
    assert plain.intersection(surface).area > 1000.0  # without the cross-section they would stand on it
    assert not buildings.contains(Point(0.0, 0.0))


def test_corner_lanes_never_fold_inside_tight_turns():
    from roadgen.geometry.planar import Pose
    from roadgen.opendrive.junctions import (
        CORNER_REACH,
        _cubic,
        _fit_to_curvature,
        _smoothstep,
        turn_path,
    )

    # a tight right turn: lane centre from 6 m before the corner to 6 m after it
    path = turn_path(Pose(-6.0, 0.0, 0.0), Pose(0.0, -6.0, -np.pi / 2))
    blends = [_smoothstep(1.5, 1.5, path.length), _smoothstep(0.15, 0.15, path.length),
              _smoothstep(3.0, 3.0, path.length)]  # bike, curb, sidewalk
    offset = _smoothstep(-1.75, -1.75, path.length)
    records = _fit_to_curvature(path, blends, offset)
    assert records is not None  # the sidewalk had to narrow

    def sidewalk(s: float) -> float:
        start, coeffs = [r for r in records if r[0] <= s + 1e-9][-1]
        return _cubic(coeffs, s - start)

    for s in np.linspace(0.0, path.length, 200):
        kappa = path.point(float(s))[3]
        reach = 1.75 + 1.5 + 0.15 + sidewalk(float(s))
        if kappa < 0:  # never past the centre of curvature (the fit aims at CORNER_REACH of the way)
            assert reach < 1.0 / -kappa
            assert reach <= CORNER_REACH / -kappa * 1.05  # the 17-station fit may overshoot a little
        assert sidewalk(float(s)) >= -1e-9
    assert sidewalk(0.0) == pytest.approx(3.0) and sidewalk(path.length) == pytest.approx(3.0)
