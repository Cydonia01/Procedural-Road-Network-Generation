"""Binary glTF 2.0 (.glb) writer, for Blender and other 3D tools.

A scene is a list of Nodes (roads, ground, buildings, ...), each a list of
Primitives: indexed triangles with positions, normals, optional UVs and
vertex colours, and one Material by name. World coordinates are OpenDRIVE's
(x east, y north, z up); glTF is y-up, so every point is written as
(x, z, -y) after subtracting an origin near the network's centre, which keeps
float32 vertices precise on big maps. Blender's importer turns the scene
back to z-up, so the network appears in OpenDRIVE axes around the origin;
the origin itself is kept in the scene's extras ("kadmos_origin").

Plain numpy and struct: no glTF library needed.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import numpy as np

from kadmos.scene.osgt import IndexedMesh, Mesh

log = logging.getLogger(__name__)

RGB = tuple[float, float, float]
FLOAT, UINT = 5126, 5125  # glTF component types
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


@dataclass(frozen=True)
class Material:
    """A PBR metallic-roughness material: a base colour (sRGB, as displayed; written
    linear), and a texture image (relative URI) when one is given."""

    name: str
    color: RGB = (0.8, 0.8, 0.8)
    roughness: float = 0.9
    metallic: float = 0.0
    texture: Optional[str] = None


@dataclass
class Primitive:
    """Indexed triangles (counter-clockwise seen from outside) in world coordinates (z-up)."""

    material: str
    positions: np.ndarray  # (n, 3)
    normals: np.ndarray  # (n, 3), unit length
    indices: np.ndarray  # (3 m,)
    uvs: Optional[np.ndarray] = None  # (n, 2)
    colors: Optional[np.ndarray] = None  # (n, 3) sRGB in [0, 1] (written linear)


@dataclass
class Node:
    name: str
    primitives: list[Primitive] = field(default_factory=list)


def from_mesh(mesh: Union[Mesh, IndexedMesh], material: str, planar_uv: bool = False) -> Primitive:
    """A Primitive from a scene mesh (.osgt); per-vertex or per-triangle colours become
    vertex colours, a single colour is left to the material. planar_uv: UVs = (x, y) [m]."""
    if isinstance(mesh, IndexedMesh):
        pos = np.asarray(mesh.vertices, float).reshape(-1, 3)
        nrm = np.asarray(mesh.normals, float).reshape(-1, 3)
        col = np.asarray(mesh.colors, float).reshape(-1, 3)
        idx = np.asarray(mesh.indices, np.uint32)
    else:
        pos = np.asarray(mesh.tris, float).reshape(-1, 3)
        nrm = np.repeat(np.asarray(mesh.normals, float).reshape(-1, 3), 3, axis=0)
        col = (np.repeat(np.asarray(mesh.colors, float).reshape(-1, 3), 3, axis=0)
               if isinstance(mesh.colors, list) else None)
        idx = np.arange(len(pos), dtype=np.uint32)
    return Primitive(material, pos, nrm, idx, pos[:, :2].copy() if planar_uv else None, col)


def scene_origin(nodes: list[Node]) -> np.ndarray:
    """Centre of the bounding box of every position, rounded to whole metres."""
    pts = [p.positions for n in nodes for p in n.primitives if len(p.positions)]
    if not pts:
        return np.zeros(3)
    allp = np.vstack(pts)
    return np.round((allp.min(axis=0) + allp.max(axis=0)) / 2)


def linear(c: np.ndarray) -> np.ndarray:
    """sRGB colour values in [0, 1] -> linear, as glTF stores colours."""
    c = np.clip(np.asarray(c, float), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _y_up(v: np.ndarray) -> np.ndarray:
    """z-up (x, y, z) -> y-up (x, z, -y), a proper rotation."""
    return np.column_stack([v[:, 0], v[:, 2], -v[:, 1]])


def write_glb(path: Union[str, Path], nodes: list[Node], materials: list[Material],
              origin: Optional[np.ndarray] = None) -> np.ndarray:
    """Write the nodes as one .glb; returns the origin subtracted (z-up world) [m].
    Primitives without triangles are left out; every material a primitive names must
    be in materials."""
    origin = scene_origin(nodes) if origin is None else np.asarray(origin, float)
    mat_index = {m.name: k for k, m in enumerate(materials)}
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    images: list[dict] = []
    textures: list[dict] = []

    def add(data: np.ndarray, kind: str, target: int, bounds: bool = False) -> int:
        raw = np.ascontiguousarray(data).tobytes()
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(raw), "target": target})
        blob.extend(raw)
        acc = {"bufferView": len(views) - 1, "componentType": UINT if data.dtype == np.uint32 else FLOAT,
               "count": int(data.shape[0]), "type": kind}
        if bounds:
            acc["min"] = [float(v) for v in data.min(axis=0)]
            acc["max"] = [float(v) for v in data.max(axis=0)]
        accessors.append(acc)
        return len(accessors) - 1

    gl_meshes, gl_nodes = [], []
    for node in nodes:
        prims = []
        for p in node.primitives:
            if len(p.indices) < 3:
                continue
            if p.material not in mat_index:
                raise ValueError(f"primitive of {node.name!r} names unknown material {p.material!r}")
            attrs = {
                "POSITION": add(_y_up(p.positions - origin).astype(np.float32), "VEC3", ARRAY_BUFFER, True),
                "NORMAL": add(_y_up(p.normals).astype(np.float32), "VEC3", ARRAY_BUFFER),
            }
            if p.uvs is not None:
                attrs["TEXCOORD_0"] = add(np.asarray(p.uvs, np.float32), "VEC2", ARRAY_BUFFER)
            if p.colors is not None:
                attrs["COLOR_0"] = add(linear(p.colors).astype(np.float32), "VEC3", ARRAY_BUFFER)
            prims.append({"attributes": attrs, "indices": add(np.asarray(p.indices, np.uint32), "SCALAR",
                                                              ELEMENT_ARRAY_BUFFER),
                          "material": mat_index[p.material], "mode": 4})
        if prims:
            gl_meshes.append({"name": node.name, "primitives": prims})
            gl_nodes.append({"name": node.name, "mesh": len(gl_meshes) - 1})

    gl_materials = []
    for m in materials:
        pbr: dict = {"baseColorFactor": [*map(float, linear(m.color)), 1.0],
                     "metallicFactor": float(m.metallic), "roughnessFactor": float(m.roughness)}
        if m.texture:
            images.append({"uri": m.texture})
            textures.append({"source": len(images) - 1})
            pbr["baseColorTexture"] = {"index": len(textures) - 1}
        gl_materials.append({"name": m.name, "pbrMetallicRoughness": pbr})

    doc: dict = {
        "asset": {"version": "2.0", "generator": "kadmos"},
        "scene": 0,
        "scenes": [{"name": "kadmos", "nodes": list(range(len(gl_nodes))),
                    "extras": {"kadmos_origin": [float(v) for v in origin]}}],
        "nodes": gl_nodes, "meshes": gl_meshes, "materials": gl_materials,
        "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    if images:
        doc["images"], doc["textures"] = images, textures
    while len(blob) % 4:
        blob.append(0)
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    log.info("wrote %s (%d nodes, %d primitives, %.1f MB)", path, len(gl_nodes),
             sum(len(m["primitives"]) for m in gl_meshes), len(out) / 1e6)
    return origin


def read_glb(path: Union[str, Path]) -> tuple[dict, bytes]:
    """(JSON document, binary chunk) of a .glb: for tests and checks."""
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError(f"{path}: not a glTF 2.0 binary")
    n, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise ValueError(f"{path}: first chunk is not JSON")
    doc = json.loads(data[20:20 + n])
    rest = 20 + n
    blob = b""
    if rest < len(data):
        m, kind = struct.unpack_from("<I4s", data, rest)
        blob = data[rest + 8:rest + 8 + m]
    return doc, blob


def accessor_array(doc: dict, blob: bytes, index: int) -> np.ndarray:
    """An accessor's data as a numpy array (n, k)."""
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = np.uint32 if acc["componentType"] == UINT else np.float32
    arr = np.frombuffer(blob, dtype, acc["count"] * width, view["byteOffset"] + acc.get("byteOffset", 0))
    return arr.reshape(acc["count"], width)
