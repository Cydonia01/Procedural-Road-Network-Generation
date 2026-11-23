"""The whole scene as one .glb for Blender: our road mesh (kadmos.scene.roads) and
the scene meshes the .osgt holds (ground, junction fills, structures, buildings).

Nodes: "roads" (one primitive per lane material, plus the junction fills in
lane_driving), "ground", "structures", "buildings". Materials are plain
colours (gltf.lane_colors, buildings.color, structures.color; the ground keeps
its terrain colours as vertex colours), named so they can be given textures
in Blender. Roads have UVs (s, t) and fills and ground UVs (x, y), in metres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from kadmos.config import DEFAULTS, section
from kadmos.scene.gltf import Material, Node, from_mesh, write_glb
from kadmos.scene.osgt import IndexedMesh, Mesh
from kadmos.scene.roads import road_primitives

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "gltf")
RGB = tuple[float, float, float]


@dataclass(frozen=True)
class GltfParams:
    step: float = _D["step"]  # lane strips sampled this often along a road [m]
    # base colour per lane type, RGB in [0, 1]; "other" for the types not listed
    lane_colors: dict[str, RGB] = field(default_factory=lambda: {k: tuple(v) for k, v in _D["lane_colors"].items()})

    def __post_init__(self) -> None:
        if self.step <= 0:
            raise ValueError("gltf step must be > 0")
        if "other" not in self.lane_colors or "driving" not in self.lane_colors:
            raise ValueError("gltf lane_colors needs 'driving' and 'other'")
        if not all(len(c) == 3 and all(0.0 <= v <= 1.0 for v in c) for c in self.lane_colors.values()):
            raise ValueError("gltf lane_colors must be RGB in [0, 1]")


# Scene mesh name -> (node, material, planar UVs). Others keep their own name for both.
SCENE_PARTS = {
    "ground": ("ground", "ground", True),
    "junction_fill": ("roads", "lane_driving", True),
    "structures": ("structures", "concrete", False),
    "buildings": ("buildings", "buildings", False),
}


def scene_nodes(xodr: Union[str, Path, etree._Element, None], meshes: list[Union[Mesh, IndexedMesh]],
                params: GltfParams = GltfParams(), evals: Optional[dict] = None
                ) -> tuple[list[Node], list[Material]]:
    """Nodes and the materials they use: the .xodr's lanes (if given) and the scene meshes."""
    nodes: dict[str, Node] = {}
    colors: dict[str, RGB] = {f"lane_{k}": tuple(v) for k, v in params.lane_colors.items()}
    if xodr is not None:
        nodes["roads"] = Node("roads", road_primitives(xodr, params.step, set(params.lane_colors), evals))
    for mesh in meshes:
        node, material, planar = SCENE_PARTS.get(mesh.name, (mesh.name, mesh.name, False))
        prim = from_mesh(mesh, material, planar)
        if material not in colors:
            # one colour for the mesh: the material's; vertex colours: white, so they show as they are
            colors[material] = (tuple(mesh.colors) if isinstance(mesh, Mesh) and not isinstance(mesh.colors, list)
                                else (1.0, 1.0, 1.0))
        nodes.setdefault(node, Node(node)).primitives.append(prim)
    used = {p.material for n in nodes.values() for p in n.primitives}
    materials = [Material(name, colors[name]) for name in sorted(used)]
    return list(nodes.values()), materials


def export_glb(path: Union[str, Path], xodr: Union[str, Path, etree._Element, None],
               meshes: list[Union[Mesh, IndexedMesh]], params: GltfParams = GltfParams(),
               evals: Optional[dict] = None) -> None:
    """Write the scene (lanes of the .xodr and the scene meshes) to one .glb."""
    nodes, materials = scene_nodes(xodr, meshes, params, evals)
    origin = write_glb(path, nodes, materials)
    log.info("glTF origin (subtracted from every point) [m]: %.0f %.0f %.0f", *origin)
