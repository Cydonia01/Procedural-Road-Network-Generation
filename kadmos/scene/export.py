"""The whole scene as one .glb for Blender: our road mesh (kadmos.scene.roads) and
the scene meshes the .osgt holds (ground, junction fills, structures, buildings).

Nodes: "roads" (one primitive per lane material, plus the junction fills in
lane_driving), "marks" (kadmos.scene.marks), "signals" (signs and lights,
kadmos.scene.signals), "ground" (ground_land, ground_vegetation, water),
"structures" (concrete), "buildings" (building_wall, building_roof).

UVs are in metres: (s, t) on roads and marks, (x, y) on fills, ground and
anything facing up, along and up a face on walls; sign faces 0..1. Materials
are plain colours (gltf.lane_colors, mark colours, sign colours,
buildings.color, structures.color; untextured ground keeps its terrain
colours as vertex colours). With a texture library (gltf.textures, from
scripts/fetch_textures.py) the materials it names get its images, embedded
in the .glb and tiled at their real size (kadmos.scene.textures).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import numpy as np
from lxml import etree

from kadmos.config import DEFAULTS, section
from kadmos.maps.terrain import LAND, TERRAIN_COLORS, VEGETATION, WATER
from kadmos.scene.gltf import Material, Node, Primitive, from_mesh, write_glb
from kadmos.scene.marks import MARK_COLORS, mark_primitives
from kadmos.scene.osgt import IndexedMesh, Mesh
from kadmos.scene.roads import road_primitives
from kadmos.scene.signals import signal_primitives
from kadmos.scene.textures import TextureLibrary, load_library

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "gltf")
RGB = tuple[float, float, float]


@dataclass(frozen=True)
class GltfParams:
    step: float = _D["step"]  # lane strips and marks sampled this often along a road [m]
    marks: bool = _D["marks"]  # lane marks and marking objects (stop lines, arrows)
    mark_lift: float = _D["mark_lift"]  # marks this far above the road surface [m]
    signals: bool = _D["signals"]  # signs and traffic lights
    textures: str = _D["textures"]  # texture library (scripts/fetch_textures.py); none there: plain colours
    # base colour per lane type, RGB in [0, 1]; "other" for the types not listed
    lane_colors: dict[str, RGB] = field(default_factory=lambda: {k: tuple(v) for k, v in _D["lane_colors"].items()})

    def __post_init__(self) -> None:
        if self.step <= 0 or self.mark_lift < 0:
            raise ValueError("gltf needs step > 0 and mark_lift >= 0")
        if "other" not in self.lane_colors or "driving" not in self.lane_colors:
            raise ValueError("gltf lane_colors needs 'driving' and 'other'")
        if not all(len(c) == 3 and all(0.0 <= v <= 1.0 for v in c) for c in self.lane_colors.values()):
            raise ValueError("gltf lane_colors must be RGB in [0, 1]")


WATER_ROUGHNESS = 0.05  # calm water reflects [-]


def _soup(material: str, tris: np.ndarray, normals: np.ndarray, uvs: np.ndarray) -> Primitive:
    """A Primitive from triangles (m, 3, 3), one normal each (m, 3) and UVs (m, 3, 2)."""
    pos = tris.reshape(-1, 3)
    return Primitive(material, pos, np.repeat(normals, 3, axis=0), np.arange(len(pos), dtype=np.uint32),
                     uvs.reshape(-1, 2))


def box_uvs(tris: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """UVs (m, 3, 2) in metres: (x, y) on faces facing up or down; on the others the
    distance along the face's horizontal direction and the height above the triangle's
    lowest corner (a wall's base), negative (glTF's v runs down the image), so a facade
    texture stands upright and starts at the ground."""
    up = np.abs(normals[:, 2]) > 0.5
    across = np.column_stack([-normals[:, 1], normals[:, 0]])
    norm = np.linalg.norm(across, axis=1, keepdims=True)
    across = np.where(norm > 1e-9, across / np.maximum(norm, 1e-9), [1.0, 0.0])
    u_side = np.einsum("mkj,mj->mk", tris[:, :, :2], across)
    v_side = -(tris[:, :, 2] - tris[:, :, 2].min(axis=1, keepdims=True))
    side = np.stack([u_side, v_side], axis=2)
    return np.where(up[:, None, None], tris[:, :, :2], side)


def building_parts(mesh: Mesh) -> list[Primitive]:
    """Walls (building_wall) and roofs (building_roof) with box UVs."""
    tris, normals = np.asarray(mesh.tris, float).reshape(-1, 3, 3), np.asarray(mesh.normals, float).reshape(-1, 3)
    uvs = box_uvs(tris, normals)
    roof = normals[:, 2] > 0.5
    return [_soup(name, tris[mask], normals[mask], uvs[mask])
            for name, mask in (("building_wall", ~roof), ("building_roof", roof)) if mask.any()]


def ground_parts(mesh: IndexedMesh, colors: bool = True) -> list[Primitive]:
    """The ground by terrain class, per triangle: water (all corners in the water colour),
    vegetation (mostly vegetation colour: the colours blend land and vegetation linearly),
    land; UVs (x, y). colors: keep the vertex colours (no textures) on land and vegetation."""
    pos = np.asarray(mesh.vertices, float).reshape(-1, 3)
    nrm = np.asarray(mesh.normals, float).reshape(-1, 3)
    col = np.asarray(mesh.colors, float).reshape(-1, 3)
    tri = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
    land, veg, water = (np.array(TERRAIN_COLORS[k], float) for k in (LAND, VEGETATION, WATER))
    d = veg - land
    weight = np.clip((col - land) @ d / (d @ d), 0.0, 1.0)  # vegetation share of each vertex
    wet = np.linalg.norm(col - water, axis=1) < 0.02
    tri_wet = wet[tri].all(axis=1)
    tri_veg = ~tri_wet & (weight[tri].mean(axis=1) > 0.5)
    out = []
    for name, mask in (("ground_land", ~tri_wet & ~tri_veg), ("ground_vegetation", tri_veg), ("water", tri_wet)):
        if not mask.any():
            continue
        used, inv = np.unique(tri[mask], return_inverse=True)
        out.append(Primitive(name, pos[used], nrm[used], inv.astype(np.uint32).ravel(), pos[used, :2].copy(),
                             col[used] if colors and name != "water" else None))
    return out


def scene_nodes(xodr: Union[str, Path, etree._Element, None], meshes: list[Union[Mesh, IndexedMesh]],
                params: GltfParams = GltfParams(), evals: Optional[dict] = None,
                library: Optional[TextureLibrary] = None) -> tuple[list[Node], list[Material]]:
    """Nodes and the materials they use: the .xodr's lanes, marks and signals (if given) and
    the scene meshes; with textures from library (default: the one in params.textures)."""
    library = load_library(params.textures) if library is None else library
    nodes: dict[str, Node] = {}
    colors: dict[str, RGB] = {f"lane_{k}": tuple(v) for k, v in params.lane_colors.items()}
    colors.update({f"mark_{k}": v for k, v in MARK_COLORS.items()})
    special = {"water": Material("water", TERRAIN_COLORS[WATER], WATER_ROUGHNESS)}
    extra: list[Material] = []
    if xodr is not None:
        root = xodr if isinstance(xodr, etree._Element) else etree.parse(str(xodr)).getroot()
        nodes["roads"] = Node("roads", road_primitives(root, params.step, set(params.lane_colors), evals))
        if params.marks:
            nodes["marks"] = Node("marks", mark_primitives(root, params.step, params.mark_lift, evals))
        if params.signals:
            prims, extra = signal_primitives(root, evals, frozenset(library.signs))
            nodes["signals"] = Node("signals", prims)
    for mesh in meshes:
        if mesh.name == "ground" and isinstance(mesh, IndexedMesh):
            textured = {"ground_land", "ground_vegetation"} <= set(library.materials)
            prims, node = ground_parts(mesh, colors=not textured), "ground"
            # white: the vertex colours, or the textures, show as they are
            colors.update({"ground_land": (1.0, 1.0, 1.0), "ground_vegetation": (1.0, 1.0, 1.0)})
        elif mesh.name == "buildings" and isinstance(mesh, Mesh):
            prims, node = building_parts(mesh), "buildings"
            colors.update({"building_wall": tuple(mesh.colors), "building_roof": tuple(mesh.colors)})
        elif mesh.name == "structures" and isinstance(mesh, Mesh):
            tris = np.asarray(mesh.tris, float).reshape(-1, 3, 3)
            normals = np.asarray(mesh.normals, float).reshape(-1, 3)
            prims, node = [_soup("concrete", tris, normals, box_uvs(tris, normals))], "structures"
            colors["concrete"] = tuple(mesh.colors)
        elif mesh.name == "junction_fill":
            prims, node = [from_mesh(mesh, "lane_driving", planar_uv=True)], "roads"
        else:
            prims, node = [from_mesh(mesh, mesh.name)], mesh.name
            colors.setdefault(mesh.name, tuple(mesh.colors) if isinstance(mesh, Mesh)
                              and not isinstance(mesh.colors, list) else (1.0, 1.0, 1.0))
        nodes.setdefault(node, Node(node)).primitives.extend(prims)
    used = {p.material for n in nodes.values() for p in n.primitives}
    by_name = {m.name: m for m in extra}
    by_name.update({name: special[name] if name in special else Material(name, colors[name])
                    for name in used if name not in by_name})
    materials = [library.apply(by_name[name]) for name in sorted(used)]
    return list(nodes.values()), materials


def export_glb(path: Union[str, Path], xodr: Union[str, Path, etree._Element, None],
               meshes: list[Union[Mesh, IndexedMesh]], params: GltfParams = GltfParams(),
               evals: Optional[dict] = None) -> None:
    """Write the scene (lanes, marks and signals of the .xodr and the scene meshes) to one .glb."""
    nodes, materials = scene_nodes(xodr, meshes, params, evals)
    origin = write_glb(path, nodes, materials)
    log.info("glTF origin (subtracted from every point) [m]: %.0f %.0f %.0f", *origin)
