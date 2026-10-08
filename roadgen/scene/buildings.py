"""Simple 3D buildings: one extruded box per city block.

Blocks are the bounded faces of the planar road graph (shapely
``polygonize``). A block's footprint is the face minus every road widened by
its half-width plus a setback, so dead-end roads inside a block stay clear
too; convex corners are rounded so buildings keep clear of the turning
paths inside junctions. Each footprint is extruded to a random height.

Formats:
- .osgt (OpenSceneGraph ASCII, z-up): loads in esmini as SceneGraphFile in
  an .xosc. esmini's bundled OSG has no .obj reader. With a SceneGraphFile
  esmini needs ``--enforce_generate_model`` to still draw the roads, which
  roadgen's runner adds automatically. esmini only loads it with a window.
- .obj (+ .mtl, y-up by default): for other tools (Blender, MeshLab, ...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Union

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, triangulate, unary_union

from roadgen.graph.model import Edge, RoadGraph

log = logging.getLogger(__name__)

PathLike = Union[str, Path]


@dataclass(frozen=True)
class BuildingParams:
    seed: int = 0
    setback: float = 3.0  # sidewalk between road edge and building [m]
    corner_radius: float = 6.0  # rounding of convex footprint corners [m]
    min_height: float = 12.0  # [m]
    max_height: float = 60.0  # [m]
    min_area: float = 150.0  # smaller footprints are dropped [m^2]
    color: tuple[float, float, float] = (0.78, 0.76, 0.72)
    # .obj only: write y-up (the usual OBJ convention) instead of OpenDRIVE's z-up.
    y_up: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.min_height <= self.max_height:
            raise ValueError("need 0 < min_height <= max_height")
        if self.setback < 0 or self.corner_radius < 0 or self.min_area < 0:
            raise ValueError("setback, corner_radius and min_area must be non-negative")


def road_half_extent(e: Edge) -> float:
    """Widest side of the road from its reference line (one-ways sit on one side)."""
    return max(e.lanes_forward, e.lanes_backward) * e.lane_width


def blocks(graph: RoadGraph) -> list[Polygon]:
    """Bounded faces of the road graph, in a deterministic order."""
    lines = [
        LineString([(graph.nodes[e.from_node].x, graph.nodes[e.from_node].y),
                    (graph.nodes[e.to_node].x, graph.nodes[e.to_node].y)])
        for _, e in sorted(graph.edges.items())
    ]
    faces = list(polygonize(unary_union(lines)))
    return sorted(faces, key=lambda f: (round(f.centroid.x, 3), round(f.centroid.y, 3)))


def footprints(graph: RoadGraph, params: BuildingParams) -> list[Polygon]:
    """Buildable area per block: the face minus the widened roads, corners rounded."""
    roads = unary_union([
        LineString([(graph.nodes[e.from_node].x, graph.nodes[e.from_node].y),
                    (graph.nodes[e.to_node].x, graph.nodes[e.to_node].y)])
        .buffer(road_half_extent(e) + params.setback, cap_style="round")  # round: clears dead-end tips
        for e in graph.edges.values()
    ])
    out: list[Polygon] = []
    r = params.corner_radius
    for face in blocks(graph):
        area = face.difference(roads)
        if r > 0:  # morphological opening rounds convex corners
            area = area.buffer(-r, quad_segs=3).buffer(r, quad_segs=3, join_style="round")
        for poly in _polygons(area):
            if poly.area >= params.min_area:
                out.append(orient(poly, 1.0))
    return out


def _polygons(geom) -> Iterator[Polygon]:
    if isinstance(geom, Polygon) and not geom.is_empty:
        yield geom
    elif isinstance(geom, MultiPolygon):
        yield from (g for g in geom.geoms if not g.is_empty)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            yield from _polygons(g)


# --- triangulation ----------------------------------------------------------------------


def _ear_clip(ring: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple CCW polygon (no repeated closing point) by ear clipping."""
    idx = list(range(len(ring)))
    tris: list[tuple[int, int, int]] = []

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c) -> bool:
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0

    guard = 0
    while len(idx) > 3 and guard < 10 * len(ring) ** 2:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = ring[i0], ring[i1], ring[i2]
            if cross(a, b, c) <= 1e-12:
                continue  # reflex or degenerate corner
            if any(inside(ring[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            del idx[k]
            break
        else:
            break  # no ear found (numerically awkward polygon)
    if len(idx) == 3:
        tris.append(tuple(idx))  # type: ignore[arg-type]
    return tris


def roof_triangles(poly: Polygon) -> list[tuple[tuple[float, float], ...]]:
    """Triangles covering the footprint (ear clipping; Delaunay fallback with holes)."""
    if not poly.interiors:
        ring = list(poly.exterior.coords)[:-1]
        tris = _ear_clip(ring)
        if len(tris) == len(ring) - 2:
            return [tuple(ring[i] for i in t) for t in tris]
    return [
        tuple(orient(t, 1.0).exterior.coords)[:3]
        for t in triangulate(poly)
        if poly.contains(t.representative_point())
    ]


# --- mesh + export -------------------------------------------------------------------------

Vec3 = tuple[float, float, float]


def building_mesh(graph: RoadGraph, params: BuildingParams = BuildingParams()) -> tuple[list[tuple[Vec3, Vec3, Vec3]], list[Vec3], int]:
    """(triangles, one normal per triangle, number of buildings), z-up world coordinates.

    Triangles are counter-clockwise seen from outside the building.
    """
    rng = np.random.default_rng(params.seed)
    polys = footprints(graph, params)
    heights = rng.uniform(params.min_height, params.max_height, size=len(polys))
    tris: list[tuple[Vec3, Vec3, Vec3]] = []
    normals: list[Vec3] = []
    for poly, h in zip(polys, heights):
        h = float(h)
        for tri in roof_triangles(poly):
            tris.append(tuple((x, y, h) for x, y in tri))  # type: ignore[arg-type]
            normals.append((0.0, 0.0, 1.0))
        # Walls: exterior CCW and holes CW, so (dy, -dx) always points out of the solid.
        for ring in [poly.exterior, *poly.interiors]:
            pts = list(ring.coords)
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                dx, dy = x1 - x0, y1 - y0
                length = float(np.hypot(dx, dy))
                if length < 1e-6:
                    continue
                nrm = (dy / length, -dx / length, 0.0)
                a, b, c, d = (x0, y0, 0.0), (x1, y1, 0.0), (x1, y1, h), (x0, y0, h)
                tris += [(a, b, c), (a, c, d)]
                normals += [nrm, nrm]
    return tris, normals, len(polys)


def write_osgt(path: Path, tris, normals, color: tuple[float, float, float]) -> None:
    """OpenSceneGraph ASCII scene (.osgt, serializer version 161 / OSG 3.6), z-up.

    One Geode holding one Geometry: per-vertex positions and normals, an
    overall color, and a single DrawArrays(GL_TRIANGLES).
    """
    n = 3 * len(tris)

    def array(uid: int, kind: str, binding: str, rows: list[str]) -> list[str]:
        return [f"        osg::{kind} {{", f"          UniqueID {uid} ",
                f"          Binding {binding} ", f"          vector {len(rows)} {{",
                *(f"            {r} " for r in rows), "          }", "        }"]

    r, g, b = color
    out = [
        "#Ascii Scene ", "#Version 161 ", "#Generator roadgen 0.1 ", "",  # name + version
        "osg::Geode {", "  UniqueID 1 ", '  Name "buildings" ', "  Drawables 1 {",
        "    osg::Geometry {", "      UniqueID 2 ",
        "      PrimitiveSetList 1 {", "        osg::DrawArrays {", "          UniqueID 3 ",
        "          Mode TRIANGLES ", "          First 0 ", f"          Count {n} ",
        "        }", "      }",
        "      VertexArray TRUE {",
        *array(4, "Vec3Array", "BIND_PER_VERTEX",
               [f"{x:.3f} {y:.3f} {z:.3f}" for tri in tris for x, y, z in tri]),
        "      }",
        "      NormalArray TRUE {",
        *array(5, "Vec3Array", "BIND_PER_VERTEX",
               [f"{x:.4f} {y:.4f} {z:.4f}" for nrm in normals for x, y, z in (nrm, nrm, nrm)]),
        "      }",
        "      ColorArray TRUE {",
        *array(6, "Vec4Array", "BIND_OVERALL", [f"{r:.3f} {g:.3f} {b:.3f} 1"]),
        "      }",
        "    }", "  }", "}", "",
    ]
    path.write_text("\n".join(out))


def write_obj(path: Path, tris, normals, color: tuple[float, float, float], y_up: bool) -> None:
    """Wavefront .obj + .mtl (y-up by convention unless y_up is False)."""
    def vec(tag: str, x: float, y: float, z: float, fmt: str) -> str:
        if y_up:  # z-up world -> OBJ y-up: (x, y, z) -> (x, z, -y), a proper rotation
            x, y, z = x, z, -y
        return f"{tag} {x:{fmt}} {y:{fmt}} {z:{fmt}}"

    mtl = path.with_suffix(".mtl")
    r, g, b = color
    mtl.write_text(f"newmtl building\nKa {r:.3f} {g:.3f} {b:.3f}\nKd {r:.3f} {g:.3f} {b:.3f}\n"
                   "Ks 0.05 0.05 0.05\nNs 10\nd 1.0\n")
    lines = [f"mtllib {mtl.name}", "usemtl building"]
    for k, (tri, nrm) in enumerate(zip(tris, normals)):
        lines.append(vec("vn", *nrm, ".4f"))
        lines += [vec("v", *p, ".3f") for p in tri]
        lines.append(f"f {3 * k + 1}//{k + 1} {3 * k + 2}//{k + 1} {3 * k + 3}//{k + 1}")
    path.write_text("\n".join(lines) + "\n")


def export_buildings(graph: RoadGraph, path: PathLike, params: BuildingParams = BuildingParams()) -> int:
    """Write buildings to .osgt (loadable by esmini as SceneGraphFile) or .obj.

    Returns the number of buildings.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tris, normals, count = building_mesh(graph, params)
    if path.suffix == ".osgt":
        write_osgt(path, tris, normals, params.color)
    elif path.suffix == ".obj":
        write_obj(path, tris, normals, params.color, params.y_up)
    else:
        raise ValueError(f"unsupported building format {path.suffix!r}; use .osgt or .obj")
    log.info("buildings: %d footprints, %d triangles -> %s", count, len(tris), path)
    return count
