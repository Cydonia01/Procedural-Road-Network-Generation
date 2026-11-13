"""Heights end to end: drape (maps -> RoadGraph), elevation profiles and
bridges in the .xodr, the continuity check, and the 3D ground."""

from __future__ import annotations

import copy
import json
import logging
import re

import numpy as np
import pytest
import shapely
from lxml import etree
from scipy.spatial import cKDTree

from cli import main
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.vertical import evaluate, junction_planes, road_elevation
from roadgen.graph.io import graph_from_dict, graph_to_dict, load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.drape import DrapeParams, drape
from roadgen.maps.model import LAND, WATER, GridSpec, MapLayer, load_npz
from roadgen.maps.terrain import TERRAIN_COLORS
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.scene.buildings import (
    FOUNDATION,
    BuildingParams,
    building_mesh,
    footprints,
)
from roadgen.scene.ground import (
    GroundParams,
    ground_heights,
    ground_mesh,
    poke_through,
    water_area,
)
from roadgen.scene.osgt import write_osgt
from roadgen.scene.structures import structure_mesh
from roadgen.scene.surface import read_surfaces
from roadgen.validate.continuity import RoadEval, check_continuity
from roadgen.validate.topology import check_topology
from tests.grids import ON_MAPS, grid_cli

PLAIN = HdMapParams(enabled=False)  # these tests build driving lanes only (HD maps on by default since B10)

GRID = GridSpec((-100.0, -100.0), (1200.0, 700.0), 10.0)
X, Y = GRID.cell_centers()
RIVER = (X > 480.0) & (X < 540.0)  # 60 m of water across the map


def elevation(z) -> MapLayer:
    return MapLayer("elevation", GRID, np.broadcast_to(z, GRID.shape).astype(np.float32))


def terrain(water) -> MapLayer:
    return MapLayer("terrain", GRID, np.where(water, WATER, LAND).astype(np.uint8))


def network() -> RoadGraph:
    """A 3 x 2 grid, 400 m by 250 m blocks, crossing the river."""
    g = RoadGraph()
    k = 1
    for j in range(2):
        for i in range(3):
            g.add_node(Node(k, 100.0 + 400.0 * i, 100.0 + 250.0 * j))
            k += 1
    pairs = [(1, 2), (2, 3), (4, 5), (5, 6), (1, 4), (2, 5), (3, 6)]
    for eid, (a, b) in enumerate(pairs, 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    return g


SLOPE = elevation(0.05 * X)  # a 5 % ramp towards +x
VALLEY = elevation(np.where(RIVER, 0.0, 2.0 + 0.04 * np.abs(X - 510.0)))


def build(g: RoadGraph, tmp_path, name: str = "net") -> tuple[OpenDriveBuilder, etree._ElementTree]:
    b = OpenDriveBuilder(hd=PLAIN)
    b.build(g)
    path = tmp_path / f"{name}.xodr"
    b.write(path)
    return b, etree.parse(str(path))


# --- graph model ---------------------------------------------------------------------------------


def test_flat_graph_json_is_unchanged():
    d = graph_to_dict(network())
    assert all(set(n) == {"id", "x", "y", "kind"} for n in d["nodes"])
    assert all("profile" not in e and "bridges" not in e for e in d["edges"])
    assert not network().has_elevation


def test_heights_roundtrip_and_edge_profile():
    g = drape(network(), VALLEY, terrain(RIVER))
    back = graph_from_dict(json.loads(json.dumps(graph_to_dict(g))))
    assert back == g and back.has_elevation
    d, z = g.edge_profile(2)
    assert d[0] == 0.0 and d[-1] == pytest.approx(g.edge_length(2))
    assert z[0] == g.nodes[2].z and z[-1] == g.nodes[3].z and len(z) == len(g.edges[2].profile) + 2


def test_validate_rejects_bridges_off_the_edge():
    g = network()
    g.edges[1].bridges = [(350.0, 450.0)]
    assert g.validate() == ["edge 1: bridge outside the edge"]


# --- drape ---------------------------------------------------------------------------------------


def test_drape_follows_a_ramp_and_leaves_the_input_alone():
    g = network()
    before = copy.deepcopy(g)
    out = drape(g, SLOPE)
    assert g == before
    na = network_alignment(g)
    for n in out.nodes.values():
        ca = na.of_edge(g.incident_edges(n.id)[0].id)
        x = ca.alignment.pose(ca.node_station(n.id)).x if n.id in ca.chain.inner else n.x
        assert n.z == pytest.approx(0.05 * x, abs=0.05)  # averaging keeps a plane
    d, z = out.edge_profile(1)
    # Sampled under the fitted road: node 1 is a bend, so edge 1 starts on a curve.
    x, y = network_alignment(g).edge_points(g, 1, d)
    assert y[1] > 100.0 + 1.0  # the curve cuts inside the corner (towards node 4)
    assert np.allclose(z[1:-1], 0.05 * x[1:-1], atol=0.1)  # smoothing along a curve
    assert len(d) - 1 == 20  # 400 m at <= 20 m spacing
    assert drape(g, SLOPE) == out  # deterministic


def test_drape_warns_about_steep_roads(caplog):
    g = network()
    caplog.set_level(logging.WARNING, logger="roadgen.maps.drape")
    drape(g, SLOPE)  # 5 %
    assert not caplog.records
    drape(g, elevation(0.2 * X))  # 20 %
    (rec,) = caplog.records
    assert "steeper than 15 %" in rec.getMessage()
    caplog.clear()
    drape(g, elevation(0.2 * X), params=DrapeParams(max_grade=0.0))  # no limit, no warning
    assert not caplog.records


def test_drape_bridges_water_straight_and_clear():
    p = DrapeParams()
    out = drape(network(), VALLEY, terrain(RIVER), p)
    s0, s1 = out.edges[1].bridges[0]  # edge 1 runs from x = 100 to 500
    assert out.edges[1].bridges == [(s0, s1)] and 375.0 <= s0 <= 385.0 and s1 == 400.0
    assert out.edges[2].bridges and out.edges[2].bridges[0][0] == 0.0
    assert not out.edges[5].bridges  # x = 100, dry
    d, z = out.edge_profile(2)
    over = d <= out.edges[2].bridges[0][1]
    assert np.all(z[over] >= p.bridge_clearance - 1e-6)  # water surface is at 0
    node = out.nodes[2]  # x = 500: in the river
    assert node.z >= p.bridge_clearance


# --- profile fit -----------------------------------------------------------------------------------


def test_road_elevation_is_smooth_flat_ended_and_monotone():
    d = np.linspace(0.0, 200.0, 11)
    z = 10.0 + 5.0 * np.tanh((d - 100.0) / 30.0)
    recs = road_elevation(d, z, 15.0, 185.0, 4.0, 16.0)
    length = 170.0
    assert evaluate(recs, 0.0) == pytest.approx((4.0, 0.0))
    assert evaluate(recs, length) == pytest.approx((16.0, 0.0), abs=1e-6)
    for r in recs[1:]:  # height and slope continuous across pieces
        left = evaluate([recs[recs.index(r) - 1]], r.s)
        assert left == pytest.approx(evaluate([r], r.s), abs=1e-6)
    s = np.linspace(0.0, length, 400)
    heights = [evaluate(recs, v)[0] for v in s]
    assert np.all(np.diff(heights) >= -1e-9)  # no overshoot on rising data
    assert road_elevation(d, np.full_like(d, 3.0), 0.0, 200.0, 3.0, 3.0) == [(0.0, 3.0, 0.0, 0.0, 0.0)]


# --- OpenDRIVE ---------------------------------------------------------------------------------------


def test_xodr_has_profiles_junction_planes_and_bridges(tmp_path):
    g = drape(network(), VALLEY, terrain(RIVER))
    b, tree = build(g, tmp_path)
    planes = junction_planes(g, b.alignment, b.design)
    roads = tree.getroot().findall("road")
    assert all(r.find("elevationProfile/elevation") is not None for r in roads)
    for r in roads:
        if r.get("junction") != "-1":
            # Connecting roads lie on their junction's plane, through the node's height.
            node = next(n for n, j in b.node_to_junction.items() if j == int(r.get("junction")))
            plane = planes[node]
            assert plane.z0 == pytest.approx(g.nodes[node].z) and plane.grade <= 0.04 + 1e-9
            ev = RoadEval(r)
            for s in np.linspace(0.0, ev.length, 5):
                x, y, _ = ev.reference(float(s))
                assert ev.elevation(float(s))[0] == pytest.approx(float(plane.height(x, y)), abs=1e-3)
    bridges = tree.getroot().findall("road/objects/bridge")
    assert bridges and {br.get("type") for br in bridges} == {"concrete"}
    for br in bridges:
        road = RoadEval(br.getparent().getparent())
        assert 0 <= float(br.get("s")) and float(br.get("s")) + float(br.get("length")) <= road.length + 1e-3
    path = tmp_path / "net.xodr"
    assert check_topology(path) == []
    assert check_continuity(path) == []


def test_flat_graph_writes_no_heights(tmp_path):
    _, tree = build(network(), tmp_path)
    assert tree.getroot().find("road/elevationProfile/elevation") is None
    assert tree.getroot().find("road/objects") is None


def test_continuity_catches_height_and_grade_jumps(tmp_path):
    g = drape(network(), SLOPE)
    build(g, tmp_path)
    path = tmp_path / "net.xodr"
    tree = etree.parse(str(path))
    rec = tree.getroot().find("road[@id='1']/elevationProfile/elevation")
    rec.set("a", str(float(rec.get("a")) + 0.5))
    rec.set("b", "0.2")
    tree.write(str(path))
    errors = check_continuity(path)
    assert any("height mismatch" in e for e in errors)
    assert any("grade mismatch" in e for e in errors)


def _rm_available() -> bool:
    try:
        from roadgen.sim.drive_check import _load_rm

        _load_rm()
        return True
    except (OSError, FileNotFoundError):
        return False


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_esmini_reads_the_heights(tmp_path):
    import ctypes

    from roadgen.sim.drive_check import _load_rm, _PositionData

    g = drape(network(), VALLEY, terrain(RIVER))
    _, tree = build(g, tmp_path)
    lib = _load_rm()
    assert lib.RM_Init(str(tmp_path / "net.xodr").encode()) == 0
    try:
        h = lib.RM_CreatePosition()
        for r in map(RoadEval, tree.getroot().findall("road")):
            for s in np.linspace(0.0, r.length, 5):
                lib.RM_SetLanePosition(h, int(r.id), -1, 0.0, float(s), True)
                data = _PositionData()
                lib.RM_GetPositionData(h, ctypes.byref(data))
                assert data.z == pytest.approx(r.lane_height(-1, float(s)), abs=1e-3)
    finally:
        lib.RM_Close()


# --- 3D scene ------------------------------------------------------------------------------------------


def _surfaces(g: RoadGraph, tmp_path):
    _, tree = build(g, tmp_path)
    return read_surfaces(tree.getroot())


def test_ground_meets_road_edges_with_side_slopes_and_keeps_water_low(tmp_path):
    t = terrain(RIVER)
    g = drape(network(), VALLEY, t)
    surfaces = _surfaces(g, tmp_path)
    p = GroundParams(cell_size=10.0)
    x, y, z = ground_heights(VALLEY, surfaces, p, t)
    x0, y0, plain = ground_heights(VALLEY, None, p)
    assert np.array_equal(x, x0) and np.array_equal(y, y0)
    # Within the slope's reach the ground lies within side_slope x distance of the nearest edge.
    d, j = cKDTree(surfaces.edges[:, :2]).query(np.column_stack([x.ravel(), y.ravel()]))
    near = (d < p.slope_reach).reshape(z.shape)
    ze = surfaces.edges[j, 2].reshape(z.shape) - p.below_road
    wet = t.sample(x, y) == WATER
    dry = near & ~wet & np.isinf(_bridge_distance(surfaces, x, y))
    assert np.all(np.abs(z[dry] - ze[dry]) <= p.side_slope * d.reshape(z.shape)[dry] + 1e-6)
    far = ~near & np.isinf(_bridge_distance(surfaces, x, y))
    assert np.array_equal(z[far], plain[far])
    assert np.all(z[wet] <= plain[wet] + 1e-9)


def _bridge_distance(surfaces, x, y):
    d = np.full(x.shape, np.inf)
    for sp in surfaces.spans:
        dd, _ = cKDTree(sp.centre[:, :2]).query(np.column_stack([x.ravel(), y.ravel()]),
                                                  distance_upper_bound=sp.half_width + 60.0)
        d = np.minimum(d, dd.reshape(x.shape))
    return d


def test_ground_mesh_and_osgt(tmp_path):
    mesh = ground_mesh(VALLEY, params=GroundParams(cell_size=50.0))  # no water, no roads: a plain grid
    rows, cols = 15, 25  # 700 / 50 + 1, 1200 / 50 + 1
    assert len(mesh.vertices) == len(mesh.normals) == len(mesh.colors) == rows * cols
    assert len(mesh.triangles) == 2 * (rows - 1) * (cols - 1)
    assert np.all(mesh.normals[:, 2] > 0)
    write_osgt(tmp_path / "s.osgt", [mesh.indexed()])
    text = (tmp_path / "s.osgt").read_text()
    assert "osg::DrawElementsUInt" in text and "Drawables 1 {" in text
    ids = re.findall(r"UniqueID (\d+)", text)
    assert len(ids) == len(set(ids))


def test_ground_is_cut_around_the_roads(tmp_path):
    t = terrain(RIVER)
    g = drape(network(), VALLEY, t)
    surfaces = _surfaces(g, tmp_path)
    mesh = ground_mesh(VALLEY, t, surfaces, GroundParams(cell_size=20.0))
    v = mesh.vertices[mesh.triangles]
    a, b = v[:, 1, :2] - v[:, 0, :2], v[:, 2, :2] - v[:, 0, :2]
    cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    assert np.all(cross > 0)  # all counter-clockwise from above
    p = GroundParams()
    # No triangle reaches further in under a road than under_road (beyond float noise).
    footprint = shapely.union_all(surfaces.parts)
    tris = shapely.polygons(v[:, :, :2])
    assert not shapely.intersects(footprint.buffer(-p.under_road - 0.01), tris).any()
    # The vertices round each road lie under_road inside its outer edge, at the edge's height
    # less below_road.
    d, j = cKDTree(surfaces.edges[:, :2]).query(mesh.vertices[:, :2])
    rim = shapely.dwithin(footprint.boundary, shapely.points(mesh.vertices[:, :2]), p.under_road + 0.01)
    assert rim.sum() > 100
    assert np.all(d[rim] <= p.under_road + 1.0)  # within a sample spacing of the edge
    assert np.allclose(mesh.vertices[rim, 2], surfaces.edges[j[rim], 2] - p.below_road, atol=0.05)
    # The ground covers the map's area apart from the roads (and bridges, under which it stays).
    area = cross.sum() / 2
    inner = footprint.buffer(-p.under_road, quad_segs=2).area
    assert area == pytest.approx(1200.0 * 700.0 - inner, rel=1e-3)


def test_shoreline_is_a_smooth_line_between_water_and_land(tmp_path):
    # A river at 30 degrees: on the 10 m raster its banks are stair-stepped.
    lake = np.abs((Y - 250.0) - 0.577 * (X - 500.0)) < 40.0
    t = terrain(lake)
    w = water_area(t, 1.0)
    assert w is not None and w.area == pytest.approx(lake.sum() * 100.0, rel=0.1)
    edge = shapely.LineString([(-100.0, 250.0 - 0.577 * 600.0 + 40.0), (1100.0, 250.0 + 0.577 * 600.0 + 40.0)])
    # The bank stays within a third of a raster cell of the straight line (the raw steps reach 5 m).
    bank = [p for p in shapely.get_coordinates(w.boundary)
            if 0 < p[0] < 1000 and 0 < p[1] < 500 and edge.distance(shapely.Point(p)) < 8.0]
    assert bank and max(edge.distance(shapely.Point(p)) for p in bank) < 3.5
    mesh = ground_mesh(elevation(1.0), t, params=GroundParams(cell_size=20.0))
    water, land = np.array(TERRAIN_COLORS[WATER]), np.array(TERRAIN_COLORS[LAND])
    is_water = np.all(np.isclose(mesh.colors, water), axis=1)
    assert np.all(is_water | np.all(np.isclose(mesh.colors, land), axis=1))  # no blend at the shore
    # Each triangle is all water or all land, and water triangles lie in the water area.
    tri_water = is_water[mesh.triangles]
    assert np.all(tri_water.all(axis=1) | ~tri_water.any(axis=1))
    centres = mesh.vertices[mesh.triangles].mean(axis=1)
    inside = shapely.contains_xy(w.buffer(1e-6), centres[:, 0], centres[:, 1])
    assert np.array_equal(inside, tri_water.all(axis=1))


def test_buildings_stand_on_the_ground_and_not_on_water():
    g = network()
    params = BuildingParams(corner_radius=0.0)
    flat_tris, _, flat_n = building_mesh(g, params)
    assert flat_n >= len(footprints(g, params)) and min(p[2] for t in flat_tris for p in t) == 0.0
    tris, _, n = building_mesh(g, params, SLOPE)
    assert n == flat_n
    assert min(p[2] for t in tris for p in t) == pytest.approx(0.05 * 100.0 - FOUNDATION, abs=1.0)
    _, _, dry = building_mesh(g, params, SLOPE, terrain(RIVER))
    assert dry < n  # the blocks reaching into the river are gone


# --- CLI -----------------------------------------------------------------------------------------------


def test_cli_pipeline_drapes_and_writes_the_scene(tmp_path, caplog):
    prefix = tmp_path / "city"
    caplog.set_level(logging.INFO, logger="roadgen.cli")
    assert main(["pipeline", "lsystem", "--preset", "city", "--set", "extent=[1500, 900]", "--allow-traps", "--maps",
                 "--seed", "3", "--ground", "--buildings", "-o", str(prefix)]) == 0
    # Quality gate (F14): the ground covers no road surface on a whole generated city.
    (msg,) = [r.getMessage() for r in caplog.records if "road-surface samples under the ground" in r.getMessage()]
    covered, samples = (int(v) for v in re.match(r"ground: (\d+) of (\d+)", msg).groups())
    assert samples > 10000 and covered == 0, msg
    g = load_json(prefix.with_suffix(".json"))
    assert g.has_elevation and "drape" in g.metadata
    assert (tmp_path / "city_scene.osgt").is_file() and not (tmp_path / "city_buildings.osgt").exists()
    text = (tmp_path / "city_scene.osgt").read_text()
    assert 'Name "ground"' in text and 'Name "buildings"' in text

    flat = tmp_path / "flat"
    assert main(["pipeline", "lsystem", "--preset", "city", "--set", "extent=[1500, 900]", "--allow-traps", "--maps",
                 "--seed", "3", "--flat", "-o", str(flat)]) == 0
    assert not load_json(flat.with_suffix(".json")).has_elevation


def test_cli_drape_and_ground_commands(tmp_path, caplog):
    prefix = tmp_path / "net"
    assert main(["pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, "--maps", "--flat",
                 "-o", str(prefix)]) == 0
    files = [f"--map-file={k}={tmp_path / f'net_{k}.npz'}" for k in ("terrain", "elevation")]
    assert main(["drape", str(prefix.with_suffix(".json")), *files]) == 0
    draped = load_json(tmp_path / "net.draped.json")
    assert draped.has_elevation
    elev = load_npz(tmp_path / "net_elevation.npz")
    n = draped.nodes[1]
    assert abs(n.z - float(elev.sample(n.x, n.y))) < 10.0
    out = tmp_path / "scene.osgt"
    assert main(["ground", str(out), *files, "--graph", str(tmp_path / "net.draped.json"),
                 "--buildings"]) == 0
    assert 'Name "buildings"' in out.read_text()
    # With the built .xodr the ground is cut around its roads.
    xodr = tmp_path / "net.draped.xodr"
    assert main(["build-xodr", str(tmp_path / "net.draped.json"), str(xodr)]) == 0
    cut = tmp_path / "cut.osgt"
    caplog.set_level(logging.INFO, logger="roadgen.scene.ground")
    assert main(["ground", str(cut), *files, "--xodr", str(xodr)]) == 0
    (msg,) = [r.getMessage() for r in caplog.records if "cells cut" in r.getMessage()]
    assert int(re.search(r"(\d+) of \d+ cells cut", msg).group(1)) > 0
    with pytest.raises(SystemExit, match="--graph"):
        main(["ground", str(cut), *files, "--buildings"])
    with pytest.raises(SystemExit, match="elevation"):
        main(["drape", str(prefix.with_suffix(".json")), files[0]])


def test_cli_ground_needs_an_elevation_map(tmp_path):
    with pytest.raises(SystemExit, match="--ground needs an elevation map"):
        main(["pipeline", "lsystem", *grid_cli(2, 2), *ON_MAPS, "--ground",
              "-o", str(tmp_path / "n")])


# --- ground under the roads (stage R4) ------------------------------------------------------------


def test_grid_surface_is_exact_on_a_plane():
    from roadgen.scene.ground import grid_surface

    xs, ys = np.linspace(0, 100, 6), np.linspace(0, 60, 4)
    x, y = np.meshgrid(xs, ys)
    z = 1.0 + 0.02 * x - 0.03 * y
    px, py = np.array([3.0, 55.5, 99.0, 12.0]), np.array([1.0, 30.2, 59.0, 47.0])
    assert np.allclose(grid_surface(xs, ys, z, px, py), 1.0 + 0.02 * px - 0.03 * py)
    # The mesh lookup agrees with it on an uncut mesh.
    mesh = ground_mesh(SLOPE, params=GroundParams(cell_size=50.0))
    qx, qy = np.array([10.0, 333.3, 1050.0]), np.array([20.0, 400.0, 555.0])
    assert np.allclose(mesh.height(qx, qy), 0.05 * qx, atol=0.3)


@pytest.mark.parametrize("cell", [10.0, 20.0, 40.0])
def test_ground_never_covers_a_road(cell, tmp_path):
    t = terrain(RIVER)
    g = drape(network(), VALLEY, t)
    surfaces = _surfaces(g, tmp_path)
    mesh = ground_mesh(VALLEY, t, surfaces, GroundParams(cell_size=cell))
    samples, covered, _ = poke_through(mesh, surfaces)
    assert samples > 1000 and covered == 0
    assert np.isnan(mesh.height(surfaces.samples[:, 0], surfaces.samples[:, 1])).all()  # cut out


def test_land_under_a_bridge_stays_below_the_deck_and_the_bridge_has_a_deck(tmp_path):
    # A land strip in the middle of the river, as high as the banks.
    island = (X > 484.0) & (X < 496.0)  # under edge 1, which ends at x = 500
    t = terrain(RIVER & ~island)
    valley = elevation(np.where(RIVER & ~island, 0.0, 2.0 + 0.04 * np.abs(X - 510.0)))
    g = drape(network(), valley, t)
    surfaces = _surfaces(g, tmp_path)
    assert [sp.kind for sp in surfaces.spans].count("bridge") >= 1
    p = GroundParams(cell_size=5.0)
    x, y, z = ground_heights(valley, surfaces, p, t)
    for sp in (sp for sp in surfaces.spans if sp.kind == "bridge"):
        d, k = cKDTree(sp.centre[:, :2]).query(np.column_stack([x.ravel(), y.ravel()]))
        under = d <= sp.half_width
        assert under.any()
        assert np.all(z.ravel()[under] <= sp.centre[k[under], 2] - p.under_bridge + 1e-6)
    mesh, counts = structure_mesh(surfaces, lambda px, py: valley.sample(px, py))
    assert counts["decks"] == sum(sp.kind == "bridge" for sp in surfaces.spans) and mesh.tris
    deck = next(sp for sp in surfaces.spans if sp.kind == "bridge")
    lowest = min(pt[2] for t_ in mesh.tris for pt in t_ if abs(pt[1] - deck.centre[0, 1]) < 20.0)
    assert lowest < deck.centre[:, 2].min()  # the deck hangs below the road


def test_tunnel_portals_frame_the_road():
    from roadgen.scene.surface import RoadSurfaces, Span

    s = np.linspace(0.0, 100.0, 21)
    centre = np.column_stack([s, np.zeros_like(s), np.full_like(s, 10.0)])
    left, right = centre + [0.0, 5.0, 0.0], centre - [0.0, 5.0, 0.0]
    surfaces = RoadSurfaces(spans=[Span("tunnel", "1", centre, left, right, np.zeros_like(s))])
    mesh, counts = structure_mesh(surfaces, lambda px, py: np.zeros_like(px))
    assert counts == {"decks": 0, "piers": 0, "portals": 2}
    pts = np.array([pt for t_ in mesh.tris for pt in t_])
    # Nothing over the road below the clearance: the opening is free.
    over = (np.abs(pts[:, 1]) < 5.0 - 1e-6) & (pts[:, 2] > 10.0) & (pts[:, 2] < 15.0 - 1e-6)
    assert not over.any()
    assert pts[:, 2].max() == pytest.approx(16.0)
