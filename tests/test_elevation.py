"""Heights end to end: drape (maps -> RoadGraph), elevation profiles and
bridges in the .xodr, the continuity check, and the 3D ground."""

from __future__ import annotations

import copy
import json
import re

import numpy as np
import pytest
from lxml import etree

from cli import main
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.vertical import evaluate, junction_planes, road_elevation
from roadgen.graph.io import graph_from_dict, graph_to_dict, load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.drape import DrapeParams, drape
from roadgen.maps.model import LAND, WATER, GridSpec, MapLayer, load_npz
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.scene.buildings import (
    FOUNDATION,
    BuildingParams,
    building_mesh,
    footprints,
)
from roadgen.scene.ground import GroundParams, ground_heights, ground_mesh
from roadgen.scene.osgt import write_osgt
from roadgen.validate.continuity import RoadEval, check_continuity
from roadgen.validate.topology import check_topology

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
    b = OpenDriveBuilder()
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


def test_ground_is_levelled_under_roads_and_keeps_water_low():
    t = terrain(RIVER)
    g = drape(network(), VALLEY, t)
    p = GroundParams(cell_size=10.0)
    x, y, z = ground_heights(VALLEY, g, p, t)
    x0, y0, plain = ground_heights(VALLEY, None, p)
    assert np.array_equal(x, x0) and np.array_equal(y, y0)
    # Edge 1, dry and past its corner at node 1 (which takes at most 125 m of it).
    on_road = (np.abs(y - 100.0) < 3.0) & (x > 250.0) & (x < 400.0)
    road_z = np.interp(x[on_road], *(100.0 + np.array(g.edge_profile(1)[0]), g.edge_profile(1)[1]))
    assert np.allclose(z[on_road], road_z - p.below_road, atol=0.25)
    far = (np.abs(y - 100.0) > 60.0) & (np.abs(y - 350.0) > 60.0)
    far &= (np.abs(x - 100.0) > 60.0) & (np.abs(x - 500.0) > 60.0) & (np.abs(x - 900.0) > 60.0)
    assert np.array_equal(z[far], plain[far])
    wet = t.sample(x, y) == WATER
    assert np.all(z[wet] <= plain[wet] + 1e-9)


def test_ground_mesh_and_osgt(tmp_path):
    mesh = ground_mesh(VALLEY, terrain(RIVER), params=GroundParams(cell_size=50.0))
    rows, cols = 15, 25  # 700 / 50 + 1, 1200 / 50 + 1
    assert len(mesh.vertices) == len(mesh.normals) == len(mesh.colors) == rows * cols
    assert len(mesh.indices) == 6 * (rows - 1) * (cols - 1)
    assert all(n[2] > 0 for n in mesh.normals)
    write_osgt(tmp_path / "s.osgt", [mesh])
    text = (tmp_path / "s.osgt").read_text()
    assert "osg::DrawElementsUInt" in text and "Drawables 1 {" in text
    ids = re.findall(r"UniqueID (\d+)", text)
    assert len(ids) == len(set(ids))


def test_buildings_stand_on_the_ground_and_not_on_water():
    g = network()
    params = BuildingParams(corner_radius=0.0)
    flat_tris, _, flat_n = building_mesh(g, params)
    assert flat_n == len(footprints(g, params)) and min(p[2] for t in flat_tris for p in t) == 0.0
    tris, _, n = building_mesh(g, params, SLOPE)
    assert n == flat_n
    assert min(p[2] for t in tris for p in t) == pytest.approx(0.05 * 100.0 - FOUNDATION, abs=1.0)
    _, _, dry = building_mesh(g, params, SLOPE, terrain(RIVER))
    assert dry < n  # the blocks reaching into the river are gone


# --- CLI -----------------------------------------------------------------------------------------------


def test_cli_pipeline_drapes_and_writes_the_scene(tmp_path):
    prefix = tmp_path / "city"
    assert main(["pipeline", "lsystem", "--preset", "grid_organic", "--allow-traps", "--maps",
                 "--seed", "3", "--ground", "--buildings", "-o", str(prefix)]) == 0
    g = load_json(prefix.with_suffix(".json"))
    assert g.has_elevation and "drape" in g.metadata
    assert (tmp_path / "city_scene.osgt").is_file() and not (tmp_path / "city_buildings.osgt").exists()
    text = (tmp_path / "city_scene.osgt").read_text()
    assert 'Name "ground"' in text and 'Name "buildings"' in text

    flat = tmp_path / "flat"
    assert main(["pipeline", "lsystem", "--preset", "grid_organic", "--allow-traps", "--maps",
                 "--seed", "3", "--flat", "-o", str(flat)]) == 0
    assert not load_json(flat.with_suffix(".json")).has_elevation


def test_cli_drape_and_ground_commands(tmp_path):
    prefix = tmp_path / "net"
    assert main(["pipeline", "manhattan", "--avenues", "3", "--streets", "3", "--maps", "--flat",
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
    with pytest.raises(SystemExit, match="elevation"):
        main(["drape", str(prefix.with_suffix(".json")), files[0]])


def test_cli_ground_needs_an_elevation_map(tmp_path):
    with pytest.raises(SystemExit, match="--ground needs an elevation map"):
        main(["pipeline", "manhattan", "--avenues", "2", "--streets", "2", "--ground",
              "-o", str(tmp_path / "n")])
