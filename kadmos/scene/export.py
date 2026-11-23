"""The whole scene as one .glb for Blender: our road mesh (kadmos.scene.roads) and
the scene meshes the .osgt holds (ground, junction fills, structures, buildings).

Nodes: "roads" (one primitive per lane material, plus the junction fills in
lane_driving), "marks" (kadmos.scene.marks), "signals" (signs and lights,
kadmos.scene.signals), "ground", "structures", "buildings". Materials are
plain colours (gltf.lane_colors, mark colours, sign colours, buildings.color,
structures.color; the ground keeps its terrain colours as vertex colours),
named so they can be given textures in Blender. Roads and marks have UVs
(s, t) and fills and ground UVs (x, y), in metres; sign faces 0..1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from kadmos.config import DEFAULTS, section
from kadmos.scene.gltf import Material, Node, from_mesh, write_glb
from kadmos.scene.marks import MARK_COLORS, mark_primitives
from kadmos.scene.osgt import IndexedMesh, Mesh
from kadmos.scene.roads import road_primitives
from kadmos.scene.signals import signal_primitives

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "gltf")
RGB = tuple[float, float, float]


@dataclass(frozen=True)
class GltfParams:
    step: float = _D["step"]  # lane strips and marks sampled this often along a road [m]
    marks: bool = _D["marks"]  # lane marks and marking objects (stop lines, arrows)
    mark_lift: float = _D["mark_lift"]  # marks this far above the road surface [m]
    signals: bool = _D["signals"]  # signs and traffic lights
    # base colour per lane type, RGB in [0, 1]; "other" for the types not listed
    lane_colors: dict[str, RGB] = field(default_factory=lambda: {k: tuple(v) for k, v in _D["lane_colors"].items()})

    def __post_init__(self) -> None:
        if self.step <= 0 or self.mark_lift < 0:
            raise ValueError("gltf needs step > 0 and mark_lift >= 0")
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
    colors.update({f"mark_{k}": v for k, v in MARK_COLORS.items()})
    extra: list[Material] = []
    if xodr is not None:
        root = xodr if isinstance(xodr, etree._Element) else etree.parse(str(xodr)).getroot()
        nodes["roads"] = Node("roads", road_primitives(root, params.step, set(params.lane_colors), evals))
        if params.marks:
            nodes["marks"] = Node("marks", mark_primitives(root, params.step, params.mark_lift, evals))
        if params.signals:
            prims, extra = signal_primitives(root, evals)
            nodes["signals"] = Node("signals", prims)
    for mesh in meshes:
        node, material, planar = SCENE_PARTS.get(mesh.name, (mesh.name, mesh.name, False))
        prim = from_mesh(mesh, material, planar)
        if material not in colors:
            # one colour for the mesh: the material's; vertex colours: white, so they show as they are
            colors[material] = (tuple(mesh.colors) if isinstance(mesh, Mesh) and not isinstance(mesh.colors, list)
                                else (1.0, 1.0, 1.0))
        nodes.setdefault(node, Node(node)).primitives.append(prim)
    used = {p.material for n in nodes.values() for p in n.primitives}
    materials = [Material(name, colors[name]) for name in sorted(used - {m.name for m in extra})]
    materials += [m for m in extra if m.name in used]
    return list(nodes.values()), materials


def export_glb(path: Union[str, Path], xodr: Union[str, Path, etree._Element, None],
               meshes: list[Union[Mesh, IndexedMesh]], params: GltfParams = GltfParams(),
               evals: Optional[dict] = None) -> None:
    """Write the scene (lanes of the .xodr and the scene meshes) to one .glb."""
    nodes, materials = scene_nodes(xodr, meshes, params, evals)
    origin = write_glb(path, nodes, materials)
    log.info("glTF origin (subtracted from every point) [m]: %.0f %.0f %.0f", *origin)
