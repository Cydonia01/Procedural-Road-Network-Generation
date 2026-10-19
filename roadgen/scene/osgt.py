"""OpenSceneGraph ASCII (.osgt, serializer version 161 / OSG 3.6) writer.

esmini takes a single SceneGraphFile per scenario, so every part of the
scene (ground, buildings, ...) goes into one file: one Geode with one
Geometry per mesh. A Mesh is a triangle soup (one normal and optionally one
color per triangle, drawn with DrawArrays); an IndexedMesh shares vertices
between triangles (DrawElementsUInt), which keeps a ground grid small.
World coordinates, z-up, as in OpenDRIVE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

Vec3 = tuple[float, float, float]
Triangle = tuple[Vec3, Vec3, Vec3]
RGB = tuple[float, float, float]


@dataclass
class Mesh:
    """Triangles (counter-clockwise seen from outside), one normal each, and
    one color for the whole mesh or one per triangle."""

    name: str
    tris: list[Triangle] = field(default_factory=list)
    normals: list[Vec3] = field(default_factory=list)
    colors: Union[RGB, list[RGB]] = (0.8, 0.8, 0.8)


@dataclass
class IndexedMesh:
    """Shared vertices with a normal and a color each; every 3 indices form a
    counter-clockwise triangle."""

    name: str
    vertices: list[Vec3]
    normals: list[Vec3]
    colors: list[RGB]
    indices: list[int]


def write_osgt(path: Union[str, Path], meshes: list[Union[Mesh, IndexedMesh]]) -> None:
    uid = iter(range(2, 1 << 30))  # 1 is the Geode
    drawables = []
    for mesh in meshes:
        if isinstance(mesh, IndexedMesh):
            primitive = [
                "        osg::DrawElementsUInt {", f"          UniqueID {next(uid)} ",
                "          Mode TRIANGLES ", f"          vector {len(mesh.indices)} {{",
                *(" ".join(map(str, mesh.indices[k:k + 3])) for k in range(0, len(mesh.indices), 3)),
                "          }", "        }",
            ]
            vertices, normals, colors = mesh.vertices, mesh.normals, mesh.colors
            binding = "BIND_PER_VERTEX"
        else:
            primitive = [
                "        osg::DrawArrays {", f"          UniqueID {next(uid)} ",
                "          Mode TRIANGLES ", "          First 0 ", f"          Count {3 * len(mesh.tris)} ",
                "        }",
            ]
            vertices = [p for tri in mesh.tris for p in tri]
            normals = [n for n in mesh.normals for _ in range(3)]
            per_triangle = isinstance(mesh.colors, list)
            colors = ([c for c in mesh.colors for _ in range(3)] if per_triangle  # type: ignore[union-attr]
                      else [mesh.colors])
            binding = "BIND_PER_VERTEX" if per_triangle else "BIND_OVERALL"
        drawables += [
            "    osg::Geometry {", f"      UniqueID {next(uid)} ", f'      Name "{mesh.name}" ',
            "      PrimitiveSetList 1 {", *primitive, "      }",
            "      VertexArray TRUE {",
            *_array(next(uid), "Vec3Array", "BIND_PER_VERTEX",
                    [f"{x:.3f} {y:.3f} {z:.3f}" for x, y, z in vertices]),
            "      }",
            "      NormalArray TRUE {",
            *_array(next(uid), "Vec3Array", "BIND_PER_VERTEX",
                    [f"{x:.4f} {y:.4f} {z:.4f}" for x, y, z in normals]),
            "      }",
            "      ColorArray TRUE {",
            *_array(next(uid), "Vec4Array", binding,
                    [f"{r:.3f} {g:.3f} {b:.3f} 1" for r, g, b in colors]),
            "      }",
            "    }",
        ]
    name = "_".join(m.name for m in meshes) or "scene"
    out = [
        "#Ascii Scene ", "#Version 161 ", "#Generator roadgen 0.1 ", "",  # name + version
        "osg::Geode {", "  UniqueID 1 ", f'  Name "{name}" ', f"  Drawables {len(meshes)} {{",
        *drawables, "  }", "}", "",
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out))


def _array(uid: int, kind: str, binding: str, rows: list[str]) -> list[str]:
    return [f"        osg::{kind} {{", f"          UniqueID {uid} ",
            f"          Binding {binding} ", f"          vector {len(rows)} {{",
            *(f"            {r} " for r in rows), "          }", "        }"]
