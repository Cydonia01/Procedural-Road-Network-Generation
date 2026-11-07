"""Simple 3D buildings: one extruded box per parcel.

Blocks are the bounded faces of the planar road graph (shapely
``polygonize``), drawn along the fitted roads (roadgen.geometry.alignment)
so that buildings follow the curves the OpenDRIVE builder builds. A block's footprint is the face minus every road widened by
its half-width plus a setback, so dead-end roads inside a block stay clear
too; convex corners are rounded so buildings keep clear of the turning
paths inside junctions. Each block area is split into parcels
(roadgen.scene.parcels) with one inset building each, as tall as the
population under it.

Given an elevation map, a building stands on the ground: its base is
FOUNDATION below the lowest ground under it, and its height is added to the
highest. Given a terrain map, footprints touching water are dropped.

Formats:
- .osgt (OpenSceneGraph ASCII, z-up): loads in esmini as SceneGraphFile in
  an .xosc. esmini's bundled OSG has no .obj reader. With a SceneGraphFile
  esmini needs ``--enforce_generate_model`` to still draw the roads, which
  the `view` command adds. esmini only loads it with a window.
- .obj (+ .mtl, y-up by default): for other tools (Blender, MeshLab, ...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Union

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, substring, triangulate, unary_union

from roadgen.config import DEFAULTS, section
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    loop_edges,
    outer_half_width,
    pocket_ends,
)
from roadgen.geometry.junction_space import node_trims
from roadgen.graph.model import Edge, RoadGraph
from roadgen.maps.model import WATER, MapLayer
from roadgen.scene.osgt import Mesh
from roadgen.scene.osgt import write_osgt as write_meshes

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

_D = section(DEFAULTS, "buildings")
FOUNDATION = 1.0  # [m] a building's base goes this far below the lowest ground under it


@dataclass(frozen=True)
class BuildingParams:
    seed: int = DEFAULTS["seed"]
    setback: float = _D["setback"]  # sidewalk between road edge and building [m]
    corner_radius: float = _D["corner_radius"]  # rounding of convex footprint corners [m]
    min_height: float = _D["min_height"]  # [m]
    max_height: float = _D["max_height"]  # [m]
    min_area: float = _D["min_area"]  # smaller footprints are dropped [m^2]
    color: tuple[float, float, float] = _D["color"]
    # Parcels: one building per parcel.
    frontage: tuple[float, float] = _D["frontage"]  # parcel width along the street, min and max [m]
    depth: tuple[float, float] = _D["depth"]  # parcel depth, min and max [m]
    inset: float = _D["inset"]  # building inset from its parcel's edges (front and side setbacks) [m]
    height_density: float = _D["height_density"]  # max_height from this density [people/km^2]; 0 = random
    # .obj only: write y-up (the usual OBJ convention) instead of OpenDRIVE's z-up.
    y_up: bool = _D["y_up"]

    def __post_init__(self) -> None:
        if not 0 < self.min_height <= self.max_height:
            raise ValueError("need 0 < min_height <= max_height")
        if not 0 < self.frontage[0] <= self.frontage[1] or not 0 < self.depth[0] <= self.depth[1] \
                or self.inset < 0 or self.height_density < 0:
            raise ValueError("need 0 < frontage and depth (min <= max), inset >= 0, height_density >= 0")
        if self.setback < 0 or self.corner_radius < 0 or self.min_area < 0:
            raise ValueError("setback, corner_radius and min_area must be non-negative")


def road_half_extent(e: Edge, cross_section: Optional[CrossSectionParams] = None, loop: bool = False) -> float:
    """Widest side of the road from its reference line (one-ways sit on one side); with a
    cross-section, its sidewalks and other side lanes included."""
    if cross_section is not None:
        return outer_half_width(e, cross_section, loop)
    return max(e.lanes_forward, e.lanes_backward) * e.lane_width


def road_lines(graph: RoadGraph, cross_section: Optional[CrossSectionParams] = None
               ) -> list[tuple[Edge, LineString]]:
    """Each edge's road centre line in edge-id order: along the fitted road (with the
    cross-section's trims), or the straight edge if the graph can't be fitted."""
    na = None
    if not graph.validate():
        try:
            na = network_alignment(graph, cross_section=cross_section)
        except ValueError as err:
            log.debug("buildings: straight roads (%s)", err)
    out = []
    for _, e in sorted(graph.edges.items()):
        a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
        line = LineString(na.edge_line(graph, e.id) if na else [(a.x, a.y), (b.x, b.y)])
        out.append((e, line))
    return out


def blocks(graph: RoadGraph, lines: Optional[list[tuple[Edge, LineString]]] = None) -> list[Polygon]:
    """Bounded faces of the road graph, in a deterministic order."""
    lines = road_lines(graph) if lines is None else lines
    faces = list(polygonize(unary_union([line for _, line in lines])))
    return sorted(faces, key=lambda f: (round(f.centroid.x, 3), round(f.centroid.y, 3)))


def footprints(graph: RoadGraph, params: BuildingParams,
               cross_section: Optional[CrossSectionParams] = None) -> list[Polygon]:
    """Buildable area per block: the face minus the widened roads, corners rounded. With
    a cross-section the roads include their sidewalks, so setback is the yard behind them,
    and with its turn lanes the approaches with a left-turn pocket a lane more."""
    lines = road_lines(graph, cross_section)
    loops = loop_edges(graph) if cross_section is not None else frozenset()
    widened = [
        line.buffer(road_half_extent(e, cross_section, e.id in loops) + params.setback,
                    cap_style="round")  # round: clears dead-end tips
        for e, line in lines
    ]
    turn_lanes = cross_section.turn_lanes if cross_section is not None else None
    if turn_lanes is not None:  # a left-turn pocket widens its approach by a lane (either side)
        by_id = {e.id: (e, line) for e, line in lines}
        for n in sorted(graph.nodes):
            pockets = pocket_ends(graph, n, turn_lanes)
            trims = node_trims(graph, n, cross_section=cross_section) if pockets else {}
            for eid in sorted(pockets):
                e, line = by_id[eid]
                # The pocket starts at the junction's trim (the line runs to the node).
                reach = min(trims.get(eid, 0.0) + turn_lanes.length(e.road_class) + turn_lanes.pocket_taper,
                            line.length)
                stretch = substring(line, line.length - reach, line.length) if n == e.to_node \
                    else substring(line, 0.0, reach)
                widened.append(stretch.buffer(road_half_extent(e, cross_section, e.id in loops)
                                              + e.lane_width + params.setback, cap_style="flat"))
    roads = unary_union(widened)
    out: list[Polygon] = []
    r = params.corner_radius
    for face in blocks(graph, lines):
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


def building_mesh(
    graph: RoadGraph, params: BuildingParams = BuildingParams(),
    elevation: Optional[MapLayer] = None, terrain: Optional[MapLayer] = None,
    cross_section: Optional[CrossSectionParams] = None, population: Optional[MapLayer] = None,
) -> tuple[list[tuple[Vec3, Vec3, Vec3]], list[Vec3], int]:
    """(triangles, one normal per triangle, number of buildings), z-up world coordinates.

    Triangles are counter-clockwise seen from outside the building. Each block area is
    split into parcels with one building each (roadgen.scene.parcels), as tall as the
    population map says.
    """
    rng = np.random.default_rng(params.seed)
    polys, heights = building_footprints(graph, params, rng, terrain, cross_section, population)
    tris: list[tuple[Vec3, Vec3, Vec3]] = []
    normals: list[Vec3] = []
    count = 0
    for poly, h in zip(polys, heights):
        xs, ys = np.array(poly.exterior.coords).T
        p = poly.representative_point()
        xs, ys = np.append(xs, p.x), np.append(ys, p.y)
        if terrain is not None and np.any(terrain.sample(xs, ys) == WATER):
            continue
        base, top = 0.0, float(h)
        if elevation is not None:
            ground = elevation.sample(xs, ys)
            base, top = float(ground.min()) - FOUNDATION, float(ground.max()) + float(h)
        count += 1
        for tri in roof_triangles(poly):
            tris.append(tuple((x, y, top) for x, y in tri))  # type: ignore[arg-type]
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
                a, b, c, d = (x0, y0, base), (x1, y1, base), (x1, y1, top), (x0, y0, top)
                tris += [(a, b, c), (a, c, d)]
                normals += [nrm, nrm]
    return tris, normals, count


def building_footprints(graph: RoadGraph, params: BuildingParams, rng: np.random.Generator,
                        terrain: Optional[MapLayer] = None, cross_section: Optional[CrossSectionParams] = None,
                        population: Optional[MapLayer] = None) -> tuple[list[Polygon], list[float]]:
    """Each building's footprint and height [m]."""
    from roadgen.scene.parcels import (
        building_footprint,
        building_height,
        on_open_land,
        split_parcels,
        with_frontage,
    )

    areas = footprints(graph, params, cross_section)
    polys, heights = [], []
    for area in areas:
        for parcel in with_frontage(split_parcels(area, rng, params.frontage, params.depth), area,
                                    params.frontage[0] / 2):
            fp = building_footprint(parcel, params.inset, params.min_area)
            if fp is None or not on_open_land(fp, terrain):
                continue
            polys.append(fp)
            heights.append(building_height(fp, rng, params.min_height, params.max_height, population,
                                           params.height_density))
    return polys, heights


def write_osgt(path: Path, tris, normals, color: tuple[float, float, float]) -> None:
    """Buildings alone as an OpenSceneGraph ASCII scene (see roadgen.scene.osgt)."""
    write_meshes(path, [Mesh("buildings", tris, normals, tuple(color))])


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


def export_buildings(
    graph: RoadGraph, path: PathLike, params: BuildingParams = BuildingParams(),
    elevation: Optional[MapLayer] = None, terrain: Optional[MapLayer] = None,
    cross_section: Optional[CrossSectionParams] = None,
) -> int:
    """Write buildings to .osgt (loadable by esmini as SceneGraphFile) or .obj.

    Returns the number of buildings.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tris, normals, count = building_mesh(graph, params, elevation, terrain, cross_section)
    if path.suffix == ".osgt":
        write_osgt(path, tris, normals, params.color)
    elif path.suffix == ".obj":
        write_obj(path, tris, normals, params.color, params.y_up)
    else:
        raise ValueError(f"unsupported building format {path.suffix!r}; use .osgt or .obj")
    log.info("buildings: %d footprints, %d triangles -> %s", count, len(tris), path)
    return count
