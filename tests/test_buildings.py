from __future__ import annotations

import re

import pytest
from lxml import etree
from shapely.geometry import LineString

from roadgen.generators.manhattan import ManhattanGenerator, ManhattanParams
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.scenario.xosc_builder import ScenarioBuilder, ScenarioParams
from roadgen.scene.buildings import (
    BuildingParams,
    blocks,
    building_mesh,
    export_buildings,
    footprints,
    road_half_extent,
)


def grid(a: int = 4, s: int = 5) -> RoadGraph:
    return ManhattanGenerator().generate(ManhattanParams(a, s), 0)


def test_one_block_per_grid_cell():
    assert len(blocks(grid(4, 5))) == 3 * 4
    assert len(footprints(grid(4, 5), BuildingParams())) == 3 * 4


def test_footprints_keep_clear_of_every_road():
    g, p = grid(), BuildingParams(setback=3.0)
    for poly in footprints(g, p):
        for e in g.edges.values():
            a, b = g.nodes[e.from_node], g.nodes[e.to_node]
            clearance = LineString([(a.x, a.y), (b.x, b.y)]).distance(poly)
            assert clearance >= road_half_extent(e) + p.setback - 1e-6


def test_dead_end_inside_a_block_is_kept_clear():
    g = RoadGraph()
    for i, (x, y) in enumerate([(0, 0), (300, 0), (300, 200), (0, 200), (150, 0), (150, 120)], 1):
        g.add_node(Node(i, float(x), float(y)))
    for k, (a, b) in enumerate([(1, 5), (5, 2), (2, 3), (3, 4), (4, 1), (5, 6)], 1):
        g.add_edge(Edge(k, a, b, "street", 1, 1))  # edge 6 is a dead-end spur into the block
    spur = LineString([(150, 0), (150, 120)])
    polys = footprints(g, BuildingParams())
    assert polys
    # Round caps are polygonal approximations, so allow 1% around the tip.
    assert all(spur.distance(poly) >= (3.5 + 3.0) * 0.99 for poly in polys)


def test_heights_and_determinism():
    g, p = grid(), BuildingParams(seed=5, min_height=20, max_height=30)
    tris, normals, n = building_mesh(g, p)
    assert n == 12 and len(tris) == len(normals)
    roofs = {round(t[0][2], 6) for t, nrm in zip(tris, normals) if nrm == (0.0, 0.0, 1.0)}
    assert len(roofs) == n and all(20 <= h <= 30 for h in roofs)
    assert building_mesh(g, p) == (tris, normals, n)
    assert building_mesh(g, BuildingParams(seed=6))[0] != tris


def test_walls_face_outward():
    g = grid(2, 2)
    tris, normals, _ = building_mesh(g, BuildingParams(corner_radius=0))
    (poly,) = footprints(g, BuildingParams(corner_radius=0))
    cx, cy = poly.centroid.x, poly.centroid.y
    for tri, (nx, ny, nz) in zip(tris, normals):
        if nz == 0.0:
            mx = sum(p[0] for p in tri) / 3 - cx
            my = sum(p[1] for p in tri) / 3 - cy
            assert mx * nx + my * ny > 0


def test_osgt_structure(tmp_path):
    path = tmp_path / "b.osgt"
    n = export_buildings(grid(), path, BuildingParams())
    text = path.read_text()
    assert n == 12
    assert text.startswith("#Ascii Scene \n#Version 161 \n#Generator roadgen 0.1 ")
    count = int(re.search(r"Count (\d+)", text).group(1))
    sizes = [int(x) for x in re.findall(r"vector (\d+) \{", text)]
    assert sizes == [count, count, 1]  # positions, normals, one overall color
    assert "Mode TRIANGLES" in text and "Binding BIND_OVERALL" in text
    assert count % 3 == 0


def test_obj_export(tmp_path):
    path = tmp_path / "b.obj"
    export_buildings(grid(2, 2), path, BuildingParams(y_up=True))
    lines = path.read_text().splitlines()
    assert lines[0] == "mtllib b.mtl" and (tmp_path / "b.mtl").is_file()
    verts = [tuple(map(float, l.split()[1:])) for l in lines if l.startswith("v ")]
    faces = [l for l in lines if l.startswith("f ")]
    assert len(verts) == 3 * len(faces)
    assert max(v[1] for v in verts) >= BuildingParams().min_height  # height is OBJ's y


def test_unsupported_format(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        export_buildings(grid(2, 2), tmp_path / "b.fbx")


def test_scenario_references_scenegraph(tmp_path):
    g = grid(3, 3)
    b = OpenDriveBuilder()
    b.build(g)
    b.write(tmp_path / "net" / "g.xodr")
    export_buildings(g, tmp_path / "net" / "g_buildings.osgt")
    xosc = ScenarioBuilder.from_files(tmp_path / "net" / "g.xodr").write(
        ScenarioParams(3, sim_duration=2.0), tmp_path / "scen" / "g.xosc",
        scenegraph=tmp_path / "net" / "g_buildings.osgt",
    )
    root = etree.parse(str(xosc)).getroot()
    assert root.find("RoadNetwork/SceneGraphFile").get("filepath") == "../net/g_buildings.osgt"
    assert root.find("RoadNetwork/LogicFile").get("filepath") == "../net/g.xodr"


def _esmini_available() -> bool:
    from roadgen.sim.esmini import esmini_binary

    try:
        esmini_binary("esmini")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found")
def test_runner_keeps_roads_visible_with_scenegraph(tmp_path):
    from roadgen.sim.runner import run_esmini

    g = grid(2, 2)
    b = OpenDriveBuilder()
    b.build(g)
    b.write(tmp_path / "g.xodr")
    export_buildings(g, tmp_path / "g_buildings.osgt")
    xosc = ScenarioBuilder.from_files(tmp_path / "g.xodr").write(
        ScenarioParams(2, sim_duration=1.0), tmp_path / "g.xosc", scenegraph=tmp_path / "g_buildings.osgt"
    )
    result = run_esmini(xosc, record=False, csv=False, timeout=60)
    assert result.ok, result.errors
    assert "--enforce_generate_model" in result.command
