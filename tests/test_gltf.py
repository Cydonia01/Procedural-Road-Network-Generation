"""Blender export: the .glb writer, the road mesh from the .xodr, and `export-gltf`."""

from __future__ import annotations

import json

import numpy as np
import pytest
import shapely
from lxml import etree
from shapely.geometry import Polygon

from cli import main
from kadmos.graph.io import load_json
from kadmos.maps.terrain import LAND, TERRAIN_COLORS, VEGETATION, WATER
from kadmos.opendrive.builder import OpenDriveBuilder
from kadmos.opendrive.hdmap import HdMapParams
from kadmos.scene.export import GltfParams, box_uvs, building_parts, ground_parts, scene_nodes
from kadmos.scene.gltf import Material, Node, Primitive, accessor_array, linear, read_glb, write_glb
from kadmos.scene.junctions import SceneParams, junction_fill_mesh
from kadmos.scene.marks import dashes, lane_mark_strips, mark_lines, mark_primitives, object_mark_strips
from kadmos.scene.osgt import IndexedMesh, Mesh
from kadmos.scene.roads import LaneStrip, frame, junction_surface, lane_strips, road_primitives
from kadmos.scene.signals import _place, shape, sign_name, signal_primitives
from kadmos.scene.textures import TextureLibrary, load_library
from kadmos.validate.continuity import RoadEval

NONE = TextureLibrary()  # plain colours


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


def test_textures_are_embedded_and_tiled(tmp_path):
    color, normal, sign = tmp_path / "c.jpg", tmp_path / "n.jpg", tmp_path / "s.png"
    for f, data in ((color, b"\xff\xd8jpeg-color"), (normal, b"\xff\xd8jpeg-normal"), (sign, b"\x89PNGsign")):
        f.write_bytes(data)
    path = tmp_path / "t.glb"
    write_glb(path, [Node("n", [_triangle("a"), _triangle("b"), _triangle("s")])],
              [Material("a", texture=color, normal_map=normal, roughness_map=color, tile=(2.0, 4.0)),
               Material("b", texture=color, tile=(2.0, 4.0)), Material("s", texture=sign, alpha_mask=True)])
    doc, blob = read_glb(path)
    assert len(doc["images"]) == 3  # each file embedded once
    img = {i["mimeType"]: i for i in doc["images"] if i["mimeType"] == "image/png"}["image/png"]
    view = doc["bufferViews"][img["bufferView"]]
    assert blob[view["byteOffset"]:view["byteOffset"] + view["byteLength"]] == b"\x89PNGsign"
    a, b, s = doc["materials"]
    tex = a["pbrMetallicRoughness"]["baseColorTexture"]
    assert tex["extensions"]["KHR_texture_transform"]["scale"] == [0.5, 0.25]  # UVs in metres
    assert "normalTexture" in a and "metallicRoughnessTexture" in a["pbrMetallicRoughness"]
    assert b["pbrMetallicRoughness"]["baseColorTexture"]["index"] == tex["index"]  # shared texture
    assert "extensions" not in s["pbrMetallicRoughness"]["baseColorTexture"] and s["alphaMode"] == "MASK"
    assert doc["samplers"][doc["textures"][tex["index"]]["sampler"]]["wrapS"] == 10497  # repeat
    assert doc["extensionsUsed"] == ["KHR_texture_transform"]


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
    nodes, materials = scene_nodes(root, [fill, building], GltfParams(marks=False, signals=False), library=NONE)
    by_name = {n.name: n for n in nodes}
    assert set(by_name) == {"roads", "buildings"}
    assert by_name["roads"].primitives[-1].material == "lane_driving"  # the fill joins the roads
    colors = {m.name: m.color for m in materials}
    assert colors["building_roof"] == (0.7, 0.6, 0.5)  # the triangle faces up: a roof
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
    nodes, materials = scene_nodes(root, [], GltfParams(), library=NONE)
    assert [n.name for n in nodes] == ["roads", "marks", "signals"]
    nodes, _ = scene_nodes(root, [], GltfParams(marks=False, signals=False), library=NONE)
    assert [n.name for n in nodes] == ["roads"]


def test_box_uvs_run_along_walls_and_up_from_their_base():
    wall = np.array([[[0.0, 0.0, 10.0], [4.0, 0.0, 10.0], [4.0, 0.0, 13.0]]])  # facing -y
    uv = box_uvs(wall, np.array([[0.0, -1.0, 0.0]]))[0]
    np.testing.assert_allclose(np.abs(uv[1, 0] - uv[0, 0]), 4.0)  # along the wall [m]
    np.testing.assert_allclose(uv[:, 1], [0.0, 0.0, -3.0])  # up from the base; glTF v runs down
    roof = np.array([[[1.0, 2.0, 13.0], [3.0, 2.0, 13.0], [3.0, 5.0, 13.0]]])
    np.testing.assert_allclose(box_uvs(roof, np.array([[0.0, 0.0, 1.0]]))[0], roof[0, :, :2])


def test_buildings_split_into_walls_and_roofs():
    tris = [((0, 0, 0), (4, 0, 0), (4, 0, 3)), ((0, 0, 3), (4, 0, 3), (4, 4, 3))]
    parts = building_parts(Mesh("buildings", tris, [(0, -1, 0), (0, 0, 1)], (0.7, 0.6, 0.5)))
    assert [p.material for p in parts] == ["building_wall", "building_roof"]
    assert all(len(p.indices) == 3 and p.uvs is not None for p in parts)


def test_ground_splits_into_land_vegetation_and_water():
    land, veg, water = (TERRAIN_COLORS[k] for k in (LAND, VEGETATION, WATER))
    mix = tuple(0.2 * a + 0.8 * b for a, b in zip(land, veg))  # mostly vegetation
    vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 0, 0), (2, 1, 0), (3, 0, 0), (3, 1, 0)]
    colors = [land, land, land, mix, mix, mix, water, water]
    tris = [0, 1, 2, 1, 4, 3, 4, 5, 3, 4, 6, 7]  # land, land-ish (1 land + 2 mix: veg), veg, water+mix
    mesh = IndexedMesh("ground", vertices, [(0, 0, 1)] * 8, colors, tris)
    by = {p.material: p for p in ground_parts(mesh)}
    assert set(by) == {"ground_land", "ground_vegetation"}  # the last triangle is not all water
    assert len(by["ground_land"].indices) == 3 and len(by["ground_vegetation"].indices) == 9
    mesh.indices = [6, 7, 5]
    mesh.colors = [land, land, land, mix, mix, water, water, water]
    (wet,) = ground_parts(mesh, colors=False)
    assert wet.material == "water" and wet.colors is None
    np.testing.assert_allclose(wet.uvs, wet.positions[:, :2])


def test_imaged_signs_get_one_face_over_the_plate(built):
    _, root = built
    prims, _ = signal_primitives(root, imaged=frozenset({"sign_DE_206"}))
    by = {p.material: p for p in prims}
    face = by["sign_DE_206"]
    assert face.uvs.min() == pytest.approx(0.0) and face.uvs.max() == pytest.approx(1.0)
    assert "sign_rim_white" not in by  # the stop sign's rim is in its image
    up = face.uvs[face.positions[:, 2].argmax()]
    assert up[1] == pytest.approx(0.0, abs=0.08)  # the plate's top shows the image's top (v = 0)


def test_texture_library_applies_to_named_materials(tmp_path, monkeypatch):
    monkeypatch.delenv("KADMOS_TEXTURES")
    (tmp_path / "asphalt").mkdir()
    (tmp_path / "signs").mkdir()
    for f in ("asphalt/color.jpg", "asphalt/normal.jpg", "signs/sign_DE_206.png"):
        (tmp_path / f).write_bytes(b"x")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "textures": {"asphalt": {"files": {"color": "asphalt/color.jpg", "normal": "asphalt/normal.jpg",
                                           "roughness": "asphalt/roughness.jpg"}, "tile": [2.5, 2.5]},
                     "gone": {"files": {"color": "gone/color.jpg"}, "tile": [1, 1]}},
        "materials": {"lane_driving": "asphalt", "concrete": "gone"},
        "signs": {"sign_DE_206": {"file": "signs/sign_DE_206.png"}, "sign_DE_205": {"file": "signs/x.png"}},
    }))
    lib = load_library(tmp_path)
    assert set(lib.materials) == {"lane_driving"} and set(lib.signs) == {"sign_DE_206"}
    m = lib.apply(Material("lane_driving", (0.3, 0.3, 0.3)))
    assert m.color == (1.0, 1.0, 1.0) and m.tile == (2.5, 2.5) and m.roughness_map is None  # file missing
    assert m.normal_map == tmp_path / "asphalt/normal.jpg"
    assert lib.apply(Material("sign_DE_206")).alpha_mask
    assert lib.apply(Material("concrete")) == Material("concrete")
    assert load_library(tmp_path / "missing").empty and load_library("").empty
    monkeypatch.setenv("KADMOS_TEXTURES", "off")
    assert load_library(tmp_path).empty
    monkeypatch.setenv("KADMOS_TEXTURES", str(tmp_path))
    assert not load_library("elsewhere").empty
    monkeypatch.delenv("KADMOS_TEXTURES")
    # the export embeds them
    nodes, materials = scene_nodes(None, [Mesh("junction_fill", [((0, 0, 0), (1, 0, 0), (0, 1, 0))],
                                                [(0, 0, 1)], (0.3, 0.3, 0.3))], library=lib)
    assert materials[0].texture == tmp_path / "asphalt/color.jpg"
