"""Blender export: the .glb writer, the road mesh from the .xodr, and `export-gltf`."""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from lxml import etree
from shapely.geometry import Polygon

from cli import main
from kadmos.graph.io import load_json
from kadmos.opendrive.builder import OpenDriveBuilder
from kadmos.opendrive.hdmap import HdMapParams
from kadmos.scene.export import GltfParams, scene_nodes
from kadmos.scene.gltf import Material, Node, Primitive, accessor_array, linear, read_glb, write_glb
from kadmos.scene.osgt import Mesh
from kadmos.scene.junctions import SceneParams, junction_fill_mesh
from kadmos.scene.marks import dashes, lane_mark_strips, mark_lines, mark_primitives, object_mark_strips
from kadmos.scene.roads import LaneStrip, frame, junction_surface, lane_strips, road_primitives
from kadmos.scene.signals import _place, shape, sign_name, signal_primitives
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
    base = doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert base == pytest.approx([*linear([0.1, 0.2, 0.3]), 1.0])  # written linear
    assert linear([0.0, 0.5, 1.0]) == pytest.approx([0.0, 0.214, 1.0], abs=1e-3)  # sRGB -> linear
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
    nodes, materials = scene_nodes(root, [fill, building], GltfParams(marks=False, signals=False))
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
    assert [n["name"] for n in doc["nodes"]] == ["roads", "marks", "signals"]
    assert {"lane_driving", "lane_median"} <= {m["name"] for m in doc["materials"]}


def test_junction_lanes_merge_into_one_flat_surface(built):
    _, root = built
    strips = [st for road in root.findall("road") if road.get("junction") != "-1"
              for st in lane_strips(RoadEval(road), 2.0)]
    pos, nrm, uv, idx = junction_surface(strips)
    tris = pos[idx.reshape(-1, 3)]
    assert np.all(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])[:, 2] > 0)
    np.testing.assert_allclose(nrm, np.tile(nrm[0], (len(nrm), 1)))  # one plane
    np.testing.assert_allclose(uv, pos[:, :2])
    # it covers what the strips cover (overlaps counted once)
    area = shapely.union_all([Polygon(np.vstack([st.inner[:, :2], st.outer[::-1, :2]])).buffer(0)
                              for st in strips]).area
    covered = 0.5 * np.abs(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])[:, 2]).sum()
    assert covered == pytest.approx(area, rel=0.01)
    # lanes not on one plane stay strips
    tilted = [LaneStrip(st.road, st.lane_type, st.s, st.t_inner, st.t_outer,
                        st.inner + [0, 0, 1.0 * k], st.outer + [0, 0, 1.0 * k]) for k, st in enumerate(strips[:2])]
    assert junction_surface(tilted) is None


def test_mark_lines_and_dashes():
    def mark(xml: str):
        return mark_lines(etree.fromstring(xml))

    assert mark('<roadMark type="none"/>') == []
    (solid,) = mark('<roadMark type="solid" width="0.12"/>')
    assert (solid.width, solid.length, solid.space) == (0.12, 0.0, 0.0)
    (broken,) = mark('<roadMark type="broken"/>')
    assert (broken.length, broken.space) == (3.0, 9.0)
    left, right = mark('<roadMark type="solid broken" width="0.45"/>')
    assert left.t_offset < 0 < right.t_offset and (left.space, right.space) == (0.0, 9.0)
    lines = mark('<roadMark type="broken"><type><line length="6" space="12" tOffset="0.1" width="0.2"/></type>'
                 '</roadMark>')
    assert [(ln.length, ln.space, ln.t_offset, ln.width) for ln in lines] == [(6.0, 12.0, 0.1, 0.2)]
    assert dashes(0.0, 10.0, 0.0, 0.0) == [(0.0, 10.0)]
    assert dashes(0.0, 20.0, 3.0, 9.0) == [(0.0, 3.0), (12.0, 15.0)]
    assert dashes(0.0, 13.0, 3.0, 9.0, 1.0) == [(1.0, 4.0)]


def test_marks_lie_on_the_road_with_their_width(built):
    _, root = built
    road = next(r for r in root.findall("road") if r.get("junction") == "-1")
    ev = RoadEval(road)
    strips = lane_mark_strips(ev, 2.0, 0.02)
    assert strips and {st.lane_type for st in strips} == {"white"}
    for st in strips:
        width = np.hypot(*(st.outer[:, :2] - st.inner[:, :2]).T)
        assert np.all(width > 0.05) and np.all(width < 0.5)
        np.testing.assert_allclose(st.inner[:, 2], frame(ev, st.s).at(st.t_inner)[:, 2] + 0.02, atol=1e-6)
    # stop lines and arrows: marking objects
    objects = [st for r in root.findall("road") for st in object_mark_strips(RoadEval(r), 2.0, 0.02)]
    assert len(objects) >= 2 + 2 * 4  # 2 stop lines, arrows of at least 4 segments each
    prims = mark_primitives(root, 2.0, 0.02)
    assert [p.material for p in prims] == ["mark_white"]


def test_sign_shapes_and_names():
    for name in ("octagon", "circle", "triangle_up", "triangle_down", "diamond", "square"):
        pts = shape(name)
        assert np.all(np.abs(pts) <= 0.5 + 1e-9), name
        x, y = pts.T
        assert 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) > 0, name  # counter-clockwise
    sign = etree.fromstring('<signal type="274" subtype="" value="80" country="DE"/>')
    assert sign_name(sign) == "sign_DE_274_80"
    assert sign_name(etree.fromstring('<signal type="209" subtype="30" country="DE"/>')) == "sign_DE_209_30"


def test_signs_face_their_traffic_and_lights_get_lamps(built):
    _, root = built
    prims, materials = signal_primitives(root)
    by = {p.material: p for p in prims}
    assert {"pole", "sign_back", "sign_rim_white", "sign_rim_red", "sign_DE_206", "sign_DE_301"} <= set(by)
    assert {m.name for m in materials} == set(by)
    road = next(r for r in root.findall("road") if r.find("signals/signal[@type='206']") is not None)
    ev = RoadEval(road)
    for sig in road.findall("signals/signal"):
        _, n = _place(ev, sig)
        d = frame(ev, np.array([float(sig.get("s"))])).hdg[0]
        travel = np.array([np.cos(d), np.sin(d), 0.0]) * (1 if sig.get("orientation") == "+" else -1)
        assert np.dot(n, travel) == pytest.approx(-1.0)  # facing the drivers it is for
    # a light (none in this network): post, housing, three lamps
    light = etree.SubElement(road.find("signals"), "signal", s="5", t="-6", zOffset="3", width="0.3",
                             height="0.8", type="1000001", dynamic="yes", orientation="+")
    try:
        prims, _ = signal_primitives(root)
    finally:
        light.getparent().remove(light)
    assert {"light_housing", "light_red", "light_amber", "light_green"} <= {p.material for p in prims}


def test_fill_grows_under_the_lanes():
    square = [[(0.0, 0.0, 1.0), (10.0, 0.0, 1.0), (10.0, 10.0, 1.0), (0.0, 10.0, 1.0)]]

    def area(mesh):
        t = np.array(mesh.tris)
        return 0.5 * np.abs(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])[:, 2]).sum()

    assert area(junction_fill_mesh(square, SceneParams(fill_grow=0.0))) == pytest.approx(100.0)
    assert area(junction_fill_mesh(square, SceneParams(fill_grow=0.05))) == pytest.approx(10.1 ** 2)


def test_scene_has_marks_and_signals(built):
    _, root = built
    nodes, materials = scene_nodes(root, [], GltfParams())
    assert [n.name for n in nodes] == ["roads", "marks", "signals"]
    nodes, _ = scene_nodes(root, [], GltfParams(marks=False, signals=False))
    assert [n.name for n in nodes] == ["roads"]
