"""Blender export: the .glb writer, the road mesh from the .xodr, and `export-gltf`."""

from __future__ import annotations

import numpy as np
import pytest
from lxml import etree

from cli import main
from kadmos.graph.io import load_json
from kadmos.opendrive.builder import OpenDriveBuilder
from kadmos.opendrive.hdmap import HdMapParams
from kadmos.scene.export import GltfParams, scene_nodes
from kadmos.scene.gltf import Material, Node, Primitive, accessor_array, read_glb, write_glb
from kadmos.scene.osgt import Mesh
from kadmos.scene.roads import lane_strips, road_primitives
from kadmos.validate.continuity import RoadEval


def _triangle(material: str = "m", z: float = 0.0) -> Primitive:
    pos = np.array([[100.0, 200.0, z], [110.0, 200.0, z], [100.0, 210.0, z]])
    return Primitive(material, pos, np.tile([0.0, 0.0, 1.0], (3, 1)), np.arange(3, dtype=np.uint32),
                     uvs=pos[:, :2], colors=np.full((3, 3), 0.5))


def _attr(doc: dict, blob: bytes, mesh: int, name: str) -> np.ndarray:
    return accessor_array(doc, blob, doc["meshes"][mesh]["primitives"][0]["attributes"][name])


def test_glb_round_trip_is_y_up_around_the_origin(tmp_path):
    path = tmp_path / "t.glb"
    origin = write_glb(path, [Node("roads", [_triangle(z=5.0)]), Node("empty")], [Material("m", (0.1, 0.2, 0.3))])
    doc, blob = read_glb(path)
    assert doc["asset"]["version"] == "2.0"
    assert [n["name"] for n in doc["nodes"]] == ["roads"]  # nodes without triangles left out
    assert doc["scenes"][0]["extras"]["kadmos_origin"] == list(origin)
    np.testing.assert_allclose(origin, [105.0, 205.0, 5.0])
    pos = _attr(doc, blob, 0, "POSITION")
    # (x, y, z) - origin -> (x, z, -y)
    np.testing.assert_allclose(pos[1], [5.0, 0.0, 5.0])
    np.testing.assert_allclose(_attr(doc, blob, 0, "NORMAL")[0], [0.0, 1.0, 0.0])  # up is +y
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    np.testing.assert_allclose(acc["min"], pos.min(axis=0))
    assert _attr(doc, blob, 0, "TEXCOORD_0").shape == (3, 2) and _attr(doc, blob, 0, "COLOR_0").shape == (3, 3)
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == pytest.approx([0.1, 0.2, 0.3, 1.0])
    assert all(v["byteOffset"] % 4 == 0 for v in doc["bufferViews"])


def test_glb_rejects_an_unknown_material(tmp_path):
    with pytest.raises(ValueError, match="unknown material"):
        write_glb(tmp_path / "t.glb", [Node("n", [_triangle("missing")])], [Material("m")])


def test_material_texture_is_referenced(tmp_path):
    path = tmp_path / "t.glb"
    write_glb(path, [Node("n", [_triangle()])], [Material("m", texture="textures/asphalt.jpg")])
    doc, _ = read_glb(path)
    assert doc["images"] == [{"uri": "textures/asphalt.jpg"}]
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A 4-way junction of an avenue (with a median) and a street, HD maps on."""
    path = tmp_path_factory.mktemp("gltf") / "net.xodr"
    b = OpenDriveBuilder(hd=HdMapParams(enabled=True))
    b.build(load_json("tests/fixtures/4way_avenue_x_street.json"))
    b.write(path)
    return path, etree.parse(str(path)).getroot()


def test_lane_strips_follow_the_xodr_lanes(built):
    _, root = built
    for road in root.findall("road"):
        ev = RoadEval(road)
        strips = lane_strips(ev, 2.0)
        assert all(st.lane_type != "none" for st in strips)
        for st in strips:
            assert st.s[0] >= -1e-9 and st.s[-1] <= ev.length + 1e-9
            assert np.max(np.diff(st.s)) <= 2.0 + 1e-9
            # the strip is as wide as the lane's width records say, on the road's level
            width = np.hypot(*(st.outer[:, :2] - st.inner[:, :2]).T)
            np.testing.assert_allclose(width, np.abs(st.t_outer - st.t_inner), atol=1e-6)
        assert any(st.lane_type == "driving" for st in strips), road.get("id")


def test_road_mesh_faces_up_with_st_uvs(built):
    _, root = built
    prims = road_primitives(root, 2.0)
    materials = {p.material for p in prims}
    assert "lane_driving" in materials and "lane_median" in materials
    for p in prims:
        tris = p.indices.reshape(-1, 3)
        a, b, c = (p.positions[tris[:, k]] for k in range(3))
        assert np.all(np.cross(b - a, c - a)[:, 2] > 0)  # counter-clockwise from above
        assert np.all(p.normals[:, 2] > 0.9)  # flat network
        np.testing.assert_allclose(np.linalg.norm(p.normals, axis=1), 1.0)
        assert p.uvs is not None and p.uvs.shape == (len(p.positions), 2)


def test_scene_nodes_group_meshes_and_name_materials(built):
    _, root = built
    building = Mesh("buildings", [((0, 0, 0), (1, 0, 0), (0, 1, 0))], [(0, 0, 1)], (0.7, 0.6, 0.5))
    fill = Mesh("junction_fill", [((0, 0, 0), (1, 0, 0), (0, 1, 0))], [(0, 0, 1)], (0.3, 0.3, 0.3))
    nodes, materials = scene_nodes(root, [fill, building], GltfParams())
    by_name = {n.name: n for n in nodes}
    assert set(by_name) == {"roads", "buildings"}
    assert by_name["roads"].primitives[-1].material == "lane_driving"  # the fill joins the roads
    colors = {m.name: m.color for m in materials}
    assert colors["buildings"] == (0.7, 0.6, 0.5)
    assert colors["lane_driving"] == tuple(GltfParams().lane_colors["driving"])


def test_gltf_params_check_their_colors():
    with pytest.raises(ValueError):
        GltfParams(lane_colors={"driving": (0.3, 0.3, 0.3)})  # no "other"
    with pytest.raises(ValueError):
        GltfParams(step=0)


def test_export_gltf_command(built, tmp_path):
    path, _ = built
    out = tmp_path / "net.glb"
    assert main(["--no-history", "export-gltf", str(path), "-o", str(out)]) == 0
    doc, _ = read_glb(out)
    assert [n["name"] for n in doc["nodes"]] == ["roads"]
    assert {"lane_driving", "lane_median"} <= {m["name"] for m in doc["materials"]}
