"""3D ground from the input maps, cut around the roads esmini draws.

The elevation map is resampled to a grid every ``cell_size`` metres. With
the road surfaces of the built .xodr (roadgen.scene.surface):

- **Footprint cut.** Every road, connecting road and junction fill is cut out
  of the ground. A grid cell that a road or the shoreline crosses is
  re-triangulated (constrained Delaunay) over the part of the cell outside
  the roads, so the ground meets each road along its outer edge, at the
  edge's height less ``below_road``, reaching ``under_road`` in under the
  edge (esmini draws curved edges as chords; no gap shows between them and
  the ground): sidewalks (raised by <height> in the .xodr) meet the ground at
  their top, and no triangle lies over a road.
- **Side slopes.** Within ``slope_reach`` of a road edge the ground is held
  between the edge's height plus and minus ``side_slope`` times the distance
  (embankment where the road runs above the land, cut where it runs below),
  instead of a vertical step. Water is only ever lowered.
- **Bridges and tunnels.** Land under a bridge stays ``under_bridge`` below
  its deck; ground over a tunnel is left alone (both are not cut out).
- **Shoreline.** The water area is the union of the water cells, rounded
  over ``shore_smoothing`` terrain cells; cells it crosses are split
  along it, so the shore is a smooth line in water colour on one side and
  land colour on the other. Land colours still blend over a cell between
  land and vegetation.

poke_through() counts samples of the driven surface the ground covers.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, box

from roadgen.config import DEFAULTS, section
from roadgen.maps.model import LAND, WATER, MapLayer
from roadgen.maps.terrain import TERRAIN_COLORS
from roadgen.scene.osgt import IndexedMesh
from roadgen.scene.surface import RoadSurfaces

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "ground")
TILE = 8  # cells per side of a tile cut at once
GRID = 1e-4  # overlay precision (snap rounding, robust) [m]
EPS = 1e-3  # [m]


@dataclass(frozen=True)
class GroundParams:
    cell_size: float = _D["cell_size"]  # mesh resolution [m]
    below_road: float = _D["below_road"]  # the ground meets the road edges this far down [m]
    under_road: float = _D["under_road"]  # ... and reaches this far in under them [m]
    side_slope: float = _D["side_slope"]  # steepest embankment or cut beside a road (rise / run)
    slope_reach: float = _D["slope_reach"]  # ... within this distance of a road edge [m]
    under_bridge: float = _D["under_bridge"]  # land under a bridge stays this far below the deck [m]
    shore_smoothing: float = _D["shore_smoothing"]  # shoreline rounded with a radius of this many terrain cells

    def __post_init__(self) -> None:
        if (self.cell_size <= 0 or self.below_road < 0 or self.under_road < 0 or self.side_slope <= 0 or self.slope_reach < 0
                or self.under_bridge < 0 or self.shore_smoothing < 0):
            raise ValueError("need cell_size > 0, side_slope > 0, and below_road, under_road, slope_reach, "
                             "under_bridge and shore_smoothing >= 0")


@dataclass
class GroundMesh:
    """The ground: vertices with a normal and colour each, counter-clockwise triangles
    (seen from above), and the grid cell each triangle lies in (row * (cols - 1) + col)."""

    xs: np.ndarray  # grid lines along x
    ys: np.ndarray  # grid lines along y
    vertices: np.ndarray  # (n, 3)
    normals: np.ndarray  # (n, 3)
    colors: np.ndarray  # (n, 3)
    triangles: np.ndarray  # (m, 3) vertex indices
    cell: np.ndarray  # (m,)

    def indexed(self) -> IndexedMesh:
        return IndexedMesh("ground", [tuple(p) for p in self.vertices.tolist()],
                           [tuple(n) for n in self.normals.tolist()],
                           [tuple(c) for c in self.colors.tolist()], self.triangles.ravel().tolist())

    def height(self, px: np.ndarray, py: np.ndarray) -> np.ndarray:
        """Height of the ground at points; nan where no triangle is (roads, outside)."""
        px, py = np.asarray(px, float), np.asarray(py, float)
        out = np.full(px.shape, np.nan)
        nx = len(self.xs) - 1
        i = np.searchsorted(self.xs, px, side="right") - 1
        j = np.searchsorted(self.ys, py, side="right") - 1
        i = np.where(px == self.xs[-1], nx - 1, i)
        j = np.where(py == self.ys[-1], len(self.ys) - 2, j)
        ok = (i >= 0) & (i < nx) & (j >= 0) & (j < len(self.ys) - 1)
        if not ok.any() or not len(self.triangles):
            return out
        order = np.argsort(self.cell, kind="stable")
        cells = self.cell[order]
        idx = np.flatnonzero(ok)
        c = j[idx] * nx + i[idx]
        lo, hi = np.searchsorted(cells, c, side="left"), np.searchsorted(cells, c, side="right")
        qx, qy = px[idx], py[idx]
        found = np.full(len(idx), np.nan)
        for k in range(int((hi - lo).max(initial=0))):
            live = (lo + k < hi) & np.isnan(found)
            if not live.any():
                break
            t = self.triangles[order[lo[live] + k]]
            a, b, d = (self.vertices[t[:, m]] for m in range(3))
            x, y = qx[live], qy[live]
            det = (b[:, 1] - d[:, 1]) * (a[:, 0] - d[:, 0]) + (d[:, 0] - b[:, 0]) * (a[:, 1] - d[:, 1])
            det = np.where(np.abs(det) < 1e-12, np.nan, det)
            w0 = ((b[:, 1] - d[:, 1]) * (x - d[:, 0]) + (d[:, 0] - b[:, 0]) * (y - d[:, 1])) / det
            w1 = ((d[:, 1] - a[:, 1]) * (x - d[:, 0]) + (a[:, 0] - d[:, 0]) * (y - d[:, 1])) / det
            w2 = 1.0 - w0 - w1
            tol = -1e-9
            inside = (w0 >= tol) & (w1 >= tol) & (w2 >= tol)
            z = w0 * a[:, 2] + w1 * b[:, 2] + w2 * d[:, 2]
            sub = found[live]
            sub[inside] = z[inside]
            found[live] = sub
        out[idx] = found
        return out


def grid_lines(elevation: MapLayer, cell_size: float) -> tuple[np.ndarray, np.ndarray]:
    x0, x1, y0, y1 = elevation.grid.bounds
    return (np.linspace(x0, x1, max(2, math.ceil((x1 - x0) / cell_size) + 1)),
            np.linspace(y0, y1, max(2, math.ceil((y1 - y0) / cell_size) + 1)))


def ground_heights(elevation: MapLayer, surfaces: Optional[RoadSurfaces] = None,
                   params: GroundParams = GroundParams(), terrain: Optional[MapLayer] = None,
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Grid vertices (x, y, z), each of shape (rows, cols): the elevation map, with side
    slopes beside the roads, low under bridges, untouched over tunnels."""
    xs, ys = grid_lines(elevation, params.cell_size)
    x, y = np.meshgrid(xs, ys)
    z = elevation.sample(x, y).astype(float)
    if surfaces is None or not len(surfaces.edges):
        return x, y, z
    wet = terrain.sample(x, y) == WATER if terrain is not None else np.zeros(z.shape, bool)
    pts = np.column_stack([x.ravel(), y.ravel()])
    d_edge, j = cKDTree(surfaces.edges[:, :2]).query(pts, distance_upper_bound=params.slope_reach)
    near = np.isfinite(d_edge)
    # Distance to the nearest bridge or tunnel surface (its centre line less its half width).
    d_span = np.full(len(pts), np.inf)
    deck = np.full(len(pts), np.inf)  # the lowest bridge deck over a vertex
    reach = params.cell_size * math.sqrt(2.0)
    for sp in surfaces.spans:
        d, k = cKDTree(sp.centre[:, :2]).query(pts, distance_upper_bound=sp.half_width + params.slope_reach)
        hit = np.isfinite(d)
        d = np.where(hit, np.maximum(d - sp.half_width, 0.0), np.inf)
        d_span = np.minimum(d_span, d)
        if sp.kind == "bridge":
            under = hit & (d <= reach)
            deck[under] = np.minimum(deck[under], sp.centre[k[under], 2])
    zf = z.ravel().copy()
    slope = near & (d_edge <= d_span)
    ze = surfaces.edges[j[slope], 2] - params.below_road
    lo, hi = ze - params.side_slope * d_edge[slope], ze + params.side_slope * d_edge[slope]
    w = wet.ravel()[slope]
    zf[slope] = np.where(w, np.minimum(zf[slope], hi), np.clip(zf[slope], lo, hi))
    zf = np.minimum(zf, deck - params.under_bridge)
    return x, y, zf.reshape(z.shape)


def water_area(terrain: Optional[MapLayer], smoothing: float):
    """The water class as a (Multi)Polygon: the union of its cells, rounded by a closing
    and an opening with a radius of `smoothing` cells, simplified (a third of a cell) and
    corner-cut (Chaikin), so the stair steps of the raster become a smooth shore; None
    without water. The raster is first extended past the
    map's edges (edge cells repeated), so water reaching an edge keeps it."""
    if terrain is None:
        return None
    mask = terrain.data == WATER
    if not mask.any():
        return None
    g = terrain.grid
    c, r = g.cell_size, smoothing * g.cell_size
    pad = math.ceil(2 * smoothing) + 1
    rows, cols = np.nonzero(np.pad(mask, pad, mode="edge"))
    x0, y0 = g.origin[0] - pad * c, g.origin[1] - pad * c
    area = shapely.coverage_union_all(shapely.box(x0 + cols * c, y0 + rows * c,
                                                  x0 + (cols + 1) * c, y0 + (rows + 1) * c))
    if r > 0:  # closing and opening, then the remaining waves cut off and the corners rounded
        area = area.buffer(r, quad_segs=4).buffer(-2 * r, quad_segs=4).buffer(r, quad_segs=4)
        area = _smooth(area, 0.3 * c)
    area = area.intersection(box(*_xy_bounds(terrain)))
    return None if area.is_empty else area


def _xy_bounds(layer: MapLayer) -> tuple[float, float, float, float]:
    x0, x1, y0, y1 = layer.grid.bounds
    return x0, y0, x1, y1


def ground_mesh(elevation: MapLayer, terrain: Optional[MapLayer] = None,
                surfaces: Optional[RoadSurfaces] = None, params: GroundParams = GroundParams(),
                heights: Optional[tuple[np.ndarray, np.ndarray, np.ndarray]] = None) -> GroundMesh:
    """The ground mesh, cut around the road surfaces and split along the shoreline."""
    x, y, z = heights if heights is not None else ground_heights(elevation, surfaces, params, terrain)
    xs, ys = x[0], y[:, 0]
    rows, cols = x.shape
    water = water_area(terrain, params.shore_smoothing)
    parts = surfaces.parts if surfaces is not None else []
    tree = shapely.STRtree(parts) if parts else None
    edge_tree = cKDTree(surfaces.edges[:, :2]) if surfaces is not None and len(surfaces.edges) else None

    # Which cells a road or the shoreline crosses.
    ci, cj = np.meshgrid(np.arange(cols - 1), np.arange(rows - 1))
    ci, cj = ci.ravel(), cj.ravel()
    cells = shapely.box(xs[ci], ys[cj], xs[ci + 1], ys[cj + 1])
    cut = np.zeros(len(cells), bool)
    if tree is not None:
        cut[np.unique(tree.query(cells, predicate="intersects")[0])] = True
    if water is not None:
        shore = water.boundary
        shapely.prepare(shore)
        cut |= shapely.intersects(shore, cells)

    # Shared grid vertices for the plain cells.
    gy, gx = np.gradient(z, ys, xs)
    normals = np.stack([-gx, -gy, np.ones_like(z)], axis=-1)
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    grid_colors = land_colors(terrain, x, y, params.cell_size)
    if water is not None:
        shapely.prepare(water)
        grid_colors[shapely.contains_xy(water, x, y) | shapely.intersects_xy(water.boundary, x, y)] = \
            TERRAIN_COLORS[WATER]
    k = np.arange(rows * cols).reshape(rows, cols)
    plain = ~cut
    pi, pj = ci[plain], cj[plain]
    v00, v10, v01, v11 = k[pj, pi], k[pj, pi + 1], k[pj + 1, pi], k[pj + 1, pi + 1]
    tris = [np.stack([v00, v10, v11], axis=-1), np.stack([v00, v11, v01], axis=-1)]
    tri_cell = [np.flatnonzero(plain)] * 2
    verts = [np.stack([x, y, z], axis=-1).reshape(-1, 3)]
    norms = [normals.reshape(-1, 3)]
    cols_ = [grid_colors.reshape(-1, 3)]
    n_vert = rows * cols
    wet_flags: list[np.ndarray] = []

    # Cut cells, a tile at a time.
    cut_idx = np.flatnonzero(cut)
    tile_of = (cj[cut_idx] // TILE) * ((cols - 1 + TILE - 1) // TILE) + ci[cut_idx] // TILE
    for t in np.unique(tile_of):
        mine = cut_idx[tile_of == t]
        bx0, by0 = xs[ci[mine].min()], ys[cj[mine].min()]
        bx1, by1 = xs[ci[mine].max() + 1], ys[cj[mine].max() + 1]
        tile = box(bx0, by0, bx1, by1)
        local = None
        if tree is not None:
            hits = tree.query(tile.buffer(params.under_road + 1.0), predicate="intersects")
            if len(hits):
                local = _polygonal(shapely.union_all([parts[h] for h in hits], grid_size=GRID))
                if params.under_road > 0:
                    local = _polygonal(shapely.set_precision(
                        local.buffer(-params.under_road, quad_segs=2), GRID))
        land = cells[mine]
        if local is not None:
            land = _polygonal_all(shapely.difference(land, local, grid_size=GRID))
        regions = [(land, False)]
        if water is not None:
            wt = _polygonal(shapely.intersection(water, tile, grid_size=GRID))
            if not wt.is_empty:
                regions = [(shapely.difference(land, wt, grid_size=GRID), False),
                           (shapely.intersection(land, wt, grid_size=GRID), True)]
        rim = local.boundary if local is not None else None
        if rim is not None:
            shapely.prepare(rim)
        for geoms, wet in regions:
            pieces, which = shapely.get_parts(geoms, return_index=True)
            poly = shapely.get_type_id(pieces) == 3
            pieces, which = pieces[poly], which[poly]
            if not len(pieces):
                continue
            keep = shapely.area(pieces) > 1e-6
            pieces, which = pieces[keep], which[keep]
            tri_geoms, src = shapely.get_parts(shapely.constrained_delaunay_triangles(pieces), return_index=True)
            if not len(tri_geoms):
                continue
            coords = shapely.get_coordinates(shapely.get_exterior_ring(tri_geoms)).reshape(-1, 4, 2)[:, :3]
            area2 = ((coords[:, 1, 0] - coords[:, 0, 0]) * (coords[:, 2, 1] - coords[:, 0, 1])
                     - (coords[:, 2, 0] - coords[:, 0, 0]) * (coords[:, 1, 1] - coords[:, 0, 1]))
            ok = np.abs(area2) > 1e-8
            coords, src, area2 = coords[ok], src[ok], area2[ok]
            coords = np.where((area2 < 0)[:, None, None], coords[:, ::-1], coords)
            p = coords.reshape(-1, 2)
            pz = _vertex_heights(p, xs, ys, z, rim, edge_tree, surfaces, params)
            v = np.column_stack([p, pz]).reshape(-1, 3, 3)
            # Smooth shading: the grid's normals at each vertex, as on the plain cells.
            n = np.stack([grid_surface(xs, ys, normals[..., a], p[:, 0], p[:, 1]) for a in range(3)], axis=-1)
            n /= np.linalg.norm(n, axis=-1, keepdims=True)
            colors = (np.broadcast_to(TERRAIN_COLORS[WATER], p.shape[:1] + (3,)) if wet
                      else land_colors(terrain, p[:, 0], p[:, 1], params.cell_size))
            m = len(v)
            verts.append(v.reshape(-1, 3))
            norms.append(n)
            cols_.append(np.asarray(colors, float).reshape(-1, 3))
            tris.append(n_vert + np.arange(3 * m).reshape(m, 3))
            tri_cell.append(mine[which[src]])
            wet_flags.append(np.full(3 * m, wet))
            n_vert += 3 * m
    vertices, tris_all = np.vstack(verts), np.vstack(tris).astype(np.int64)
    # One vertex per point (and side of the shore): grid vertices keep their index, the cut
    # cells' copies of a point merge.
    key = np.column_stack([np.round(vertices[:, :2] / GRID).astype(np.int64),
                           np.concatenate([np.zeros(rows * cols, bool), *wet_flags]),
                           np.concatenate([np.arange(rows * cols), np.full(n_vert - rows * cols, -1)])])
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    tris_all = first[inverse.ravel()][tris_all]
    used = np.zeros(n_vert, bool)
    used[tris_all.ravel()] = True  # grid vertices under the roads are not
    new = np.cumsum(used) - 1
    mesh = GroundMesh(xs, ys, vertices[used], np.vstack(norms)[used], np.vstack(cols_)[used],
                      new[tris_all], np.concatenate(tri_cell))
    log.info("ground: %d x %d grid, %d of %d cells cut, %d vertices, %d triangles",
             cols, rows, len(cut_idx), len(cells), len(mesh.vertices), len(mesh.triangles))
    return mesh


def _polygonal(g):
    """The polygons of a geometry (snap rounding may leave lines and points behind)."""
    if shapely.get_type_id(g) in (3, 6):
        return g
    parts = [p for p in shapely.get_parts(g) if shapely.get_type_id(p) in (3, 6)]
    return shapely.union_all(parts) if parts else Polygon()


def _polygonal_all(geoms: np.ndarray) -> np.ndarray:
    odd = ~np.isin(shapely.get_type_id(geoms), (3, 6))
    if odd.any():
        geoms = geoms.copy()
        geoms[odd] = [_polygonal(g) for g in geoms[odd]]
    return geoms


def _chaikin(ring: np.ndarray, rounds: int) -> np.ndarray:
    """Corner cutting (Chaikin) of a closed ring (first point repeated at the end)."""
    pts = ring[:-1]
    for _ in range(rounds):
        nxt = np.roll(pts, -1, axis=0)
        pts = np.stack([0.75 * pts + 0.25 * nxt, 0.25 * pts + 0.75 * nxt], axis=1).reshape(-1, 2)
    return np.vstack([pts, pts[:1]])


def _smooth(area, tolerance: float, rounds: int = 3):
    polys = []
    for p in shapely.get_parts(area.simplify(tolerance)):
        if shapely.get_type_id(p) != 3 or p.is_empty:
            continue
        shell = _chaikin(np.asarray(p.exterior.coords), rounds)
        holes = [_chaikin(np.asarray(r.coords), rounds) for r in p.interiors if len(r.coords) >= 4]
        polys.append(Polygon(shell, holes).buffer(0))
    return shapely.union_all(polys) if polys else Polygon()


def _vertex_heights(p: np.ndarray, xs: np.ndarray, ys: np.ndarray, z: np.ndarray, rim, edge_tree,
                    surfaces: Optional[RoadSurfaces], params: GroundParams) -> np.ndarray:
    """Heights of cut-cell vertices: points on a road outline get the outline's height less
    below_road (between its two nearest samples), other grid corners keep theirs, the
    others (shoreline points) lie on the grid's surface."""
    out = grid_surface(xs, ys, z, p[:, 0], p[:, 1])
    corner = _on_grid(p, xs, ys)
    if corner.any():
        out[corner] = z[np.searchsorted(ys, p[corner, 1]), np.searchsorted(xs, p[corner, 0])]
    if rim is None or edge_tree is None:
        return out
    on = shapely.dwithin(rim, shapely.points(p), 2 * GRID)  # a grid corner there too
    if on.any():
        d, j = edge_tree.query(p[on], k=2)
        w = 1.0 / np.maximum(d, 1e-9)
        ze = (surfaces.edges[j, 2] * w).sum(axis=1) / w.sum(axis=1)
        out[on] = ze - params.below_road
    return out


def _on_grid(p: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    i = np.clip(np.searchsorted(xs, p[:, 0]), 0, len(xs) - 1)
    j = np.clip(np.searchsorted(ys, p[:, 1]), 0, len(ys) - 1)
    return (np.abs(xs[i] - p[:, 0]) < EPS) & (np.abs(ys[j] - p[:, 1]) < EPS)


def grid_surface(xs: np.ndarray, ys: np.ndarray, z: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Height of the uncut grid (two triangles per cell, split along v00-v11) at points."""
    i = np.clip(np.searchsorted(xs, px, side="right") - 1, 0, len(xs) - 2)
    j = np.clip(np.searchsorted(ys, py, side="right") - 1, 0, len(ys) - 2)
    u = np.clip((px - xs[i]) / (xs[i + 1] - xs[i]), 0.0, 1.0)
    v = np.clip((py - ys[j]) / (ys[j + 1] - ys[j]), 0.0, 1.0)
    z00, z10, z01, z11 = z[j, i], z[j, i + 1], z[j + 1, i], z[j + 1, i + 1]
    lower = u >= v  # triangle v00, v10, v11
    return np.where(lower, z00 + u * (z10 - z00) + v * (z11 - z10),
                    z00 + v * (z01 - z00) + u * (z11 - z01))


def land_colors(terrain: Optional[MapLayer], x: np.ndarray, y: np.ndarray, cell: float) -> np.ndarray:
    """Land colours (shape of x, plus 3): the mean over a 3 x 3 patch of samples a third of
    a cell apart, leaving water out (the shoreline is drawn by geometry), so land and
    vegetation blend instead of stepping cell by cell."""
    land = np.array(TERRAIN_COLORS[LAND], float)
    if terrain is None:
        return np.broadcast_to(land, (*np.shape(x), 3)).copy()
    palette = np.zeros((max(TERRAIN_COLORS) + 1, 3))
    for c, rgb in TERRAIN_COLORS.items():
        palette[c] = rgb
    total = np.zeros((*np.shape(x), 3))
    count = np.zeros(np.shape(x))
    offsets = (-cell / 3, 0.0, cell / 3)
    for dx in offsets:
        for dy in offsets:
            cls = terrain.sample(x + dx, y + dy).astype(int)
            dry = cls != WATER
            total += np.where(dry[..., None], palette[cls], 0.0)
            count += dry
    return np.where((count > 0)[..., None], total / np.maximum(count, 1)[..., None], land)


def poke_through(mesh: GroundMesh, surfaces: RoadSurfaces, params: GroundParams = GroundParams(),
                 tol: float = 0.01) -> tuple[int, int, float]:
    """(driven-surface samples over the ground's area, samples the ground covers by more
    than tol, worst depth [m]). Samples within under_road of the footprint's outline,
    where the ground reaches in under the road by design, are not counted as covered."""
    s = surfaces.samples
    inside = ((s[:, 0] >= mesh.xs[0]) & (s[:, 0] <= mesh.xs[-1])
              & (s[:, 1] >= mesh.ys[0]) & (s[:, 1] <= mesh.ys[-1])) if len(s) else np.zeros(0, bool)
    if not inside.any():
        return 0, 0, 0.0
    depth = mesh.height(s[inside, 0], s[inside, 1]) - s[inside, 2]
    depth = np.nan_to_num(depth, nan=-np.inf)
    cand = np.flatnonzero(depth > tol)
    if len(cand) and surfaces.parts:
        tree = shapely.STRtree(surfaces.parts)
        pts = s[inside][cand, :2]
        for k, (x, y) in enumerate(pts):
            pt = shapely.Point(x, y)
            near = tree.query(pt, predicate="dwithin", distance=params.under_road + 0.05)
            outline = shapely.union_all([surfaces.parts[h] for h in near]).boundary
            if outline.distance(pt) <= params.under_road + 0.05:
                depth[cand[k]] = -np.inf
    covered = depth > tol
    worst = float(depth[covered].max()) if covered.any() else 0.0
    return int(inside.sum()), int(covered.sum()), worst
