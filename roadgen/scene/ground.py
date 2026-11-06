"""3D ground from the input maps, so that esmini's roads (which follow
their <elevationProfile>) don't float over an empty scene.

The elevation map is resampled to a triangle mesh every ``cell_size``
metres and colored by terrain class (water / land / vegetation, blended
over a cell so shores are not stair-stepped). Given a draped RoadGraph, the
ground is levelled ``below_road`` under every road and under every junction
area, using the same fitted roads, elevation and banking, junction trims and
junction planes as the OpenDRIVE builder (roadgen.geometry), so the ground
never pokes through a road (stage R4):

- every vertex of a triangle that touches a road is levelled: out to
  ``road_margin`` beyond the road's edge plus a cell diagonal;
- the banking is followed only across the road; beyond its edge the ground
  stays at the edge's height (a flat triangle cannot follow a warped bank);
- on sag curves a vertex goes down by as much as the road dips below the
  chord to a vertex a cell diagonal along it;
- under bridges, land is lowered to ``under_bridge`` below the deck (never
  raised); over tunnels the ground is left alone; water is only ever lowered.

- last, every triangle that still covers a sample of the road surface
  (surface_samples) has its vertices pressed down below it.

poke_through() counts road-surface samples that the mesh covers.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.spatial import cKDTree

from roadgen.config import DEFAULTS, section
from roadgen.geometry.alignment import ChainAlignment, half_width, network_alignment
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    loop_edges,
    outer_half_width,
)
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN as DEFAULT_JUNCTION_MARGIN
from roadgen.geometry.profiles import bridge_spans, tunnel_spans
from roadgen.geometry.vertical import (
    chain_elevation,
    chain_superelevation,
    evaluate_many,
    junction_planes,
)
from roadgen.graph.model import RoadGraph
from roadgen.maps.model import LAND, WATER, MapLayer
from roadgen.maps.terrain import TERRAIN_COLORS
from roadgen.scene.osgt import IndexedMesh

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "ground")


@dataclass(frozen=True)
class GroundParams:
    cell_size: float = _D["cell_size"]  # mesh resolution [m]
    road_margin: float = _D["road_margin"]  # levelled beyond each road's edge [m]
    below_road: float = _D["below_road"]  # ground under roads sits this far down [m]
    under_bridge: float = _D["under_bridge"]  # land under a bridge stays this far below the deck [m]

    def __post_init__(self) -> None:
        if self.cell_size <= 0 or self.road_margin < 0 or self.below_road < 0 or self.under_bridge < 0:
            raise ValueError("need cell_size > 0, road_margin >= 0, below_road >= 0 and under_bridge >= 0")


def ground_heights(elevation: MapLayer, graph: Optional[RoadGraph] = None,
                   params: GroundParams = GroundParams(), terrain: Optional[MapLayer] = None,
                   junction_margin: float = DEFAULT_JUNCTION_MARGIN, design: DesignParams = DesignParams(),
                   cross_section: Optional[CrossSectionParams] = None,
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mesh vertices (x, y, z), each of shape (rows, cols), levelled under graph's roads
    (with a cross-section, under their sidewalks and other side lanes too)."""
    x0, x1, y0, y1 = elevation.grid.bounds
    xs = np.linspace(x0, x1, max(2, math.ceil((x1 - x0) / params.cell_size) + 1))
    ys = np.linspace(y0, y1, max(2, math.ceil((y1 - y0) / params.cell_size) + 1))
    x, y = np.meshgrid(xs, ys)
    z = elevation.sample(x, y)
    if graph is None or not graph.has_elevation:
        return x, y, z
    target = np.full(z.shape, np.inf)
    cap = np.full(z.shape, np.inf)  # under bridges: the ground may be no higher
    try:
        na = network_alignment(graph, design, junction_margin, cross_section=cross_section)
    except ValueError as err:  # the builder rejects such a graph too
        log.warning("ground: roads not levelled (%s)", err)
        return x, y, z
    planes = junction_planes(graph, na, design)
    reach_extra = params.road_margin + params.cell_size * math.sqrt(2.0)  # a triangle's far corner
    loops = loop_edges(graph) if cross_section is not None else frozenset()

    def extent(e) -> float:
        return half_width(e) if cross_section is None else outer_half_width(e, cross_section, e.id in loops)

    for ca in na.chains:
        _level_chain(graph, ca, planes, design, x, y, target, params, reach_extra, extent, cap)
    for n in na.junctions:
        r = max(na.trims[n].values()) + reach_extra
        node = graph.nodes[n]
        idx = _near(x, y, (node.x - r, node.x + r), (node.y - r, node.y + r))
        if idx is None:
            continue
        disc = np.hypot(x[idx] - node.x, y[idx] - node.y) <= r
        plane_z = planes[n].height(x[idx][disc], y[idx][disc])
        sub = target[idx]
        sub[disc] = np.minimum(sub[disc], plane_z - params.below_road)
        target[idx] = sub
    level = np.isfinite(target)
    wet = terrain.sample(x, y) == WATER if terrain is not None else np.zeros(z.shape, bool)
    z = np.where(level & ~wet, target, np.where(level, np.minimum(z, target), z))
    z = np.minimum(z, cap)
    # Triangles that still cover a road (spanning it obliquely, or over a tight sag) go down.
    px, py, pz = surface_samples(graph, na, planes, design, cross_section, params.cell_size / 8)
    return x, y, _press_down(x, y, z, px, py, pz, params.below_road / 2)


def _level_chain(graph: RoadGraph, ca: ChainAlignment, planes, design: DesignParams,
                 x: np.ndarray, y: np.ndarray, target: np.ndarray, params: GroundParams,
                 reach_extra: float, extent=half_width, cap: Optional[np.ndarray] = None) -> None:
    """Lower target under one chain road: below its surface (elevation and banking)
    out to its widest half width plus reach_extra; under bridges lower cap to
    under_bridge below the deck instead; over tunnels leave both alone."""
    smp = ca.alignment.sample(params.cell_size / 2)
    width = max(extent(graph.edges[eid]) for eid in ca.chain.edge_ids)
    reach = width + reach_extra
    idx = _near(x, y, (smp.x.min() - reach, smp.x.max() + reach), (smp.y.min() - reach, smp.y.max() + reach))
    if idx is None:
        return
    px, py = x[idx].ravel(), y[idx].ravel()
    dist, j = cKDTree(smp.xy()).query(np.column_stack([px, py]), distance_upper_bound=reach)
    hit = np.isfinite(dist)
    if not hit.any():
        return
    k = np.flatnonzero(hit)
    jj = j[hit]
    s = smp.s[jj]
    keep = np.ones(len(s), bool)
    for s0, length in tunnel_spans(graph, ca):
        keep &= (s < s0) | (s > s0 + length)
    bridge = np.zeros(len(s), bool)
    for s0, length in bridge_spans(graph, ca):
        bridge |= (s >= s0) & (s <= s0 + length)
    k, jj, s, bridge = k[keep], jj[keep], s[keep], bridge[keep]
    # Signed lateral offset (left positive) from the nearest centre-line sample, clamped
    # to the road: beyond its edge the ground stays at the edge's height.
    h = smp.hdg[jj]
    t = np.clip(-(px[k] - smp.x[jj]) * np.sin(h) + (py[k] - smp.y[jj]) * np.cos(h), -width, width)
    elevation = chain_elevation(graph, ca, planes)
    sup = chain_superelevation(graph, ca, planes, design)
    def surface(ds: float) -> np.ndarray:
        sj = np.clip(s + ds, 0.0, ca.alignment.length)
        zj = evaluate_many(elevation, sj)
        return zj + t * np.sin(evaluate_many(sup, sj)) if sup else zj

    # Sag curves: a triangle edge from here to a vertex a cell diagonal along the road is
    # a chord; lower by as much as the road dips below it halfway.
    span = params.cell_size * math.sqrt(2.0)
    road_z = surface(0.0)
    sag = np.zeros(len(s))
    for sign in (-1.0, 1.0):
        sag = np.maximum(sag, (road_z + surface(sign * span)) / 2 - surface(sign * span / 2))
    road_z = road_z - sag
    sub = target[idx].ravel()
    on = ~bridge
    sub[k[on]] = np.minimum(sub[k[on]], road_z[on] - params.below_road)
    target[idx] = sub.reshape(target[idx].shape)
    if cap is not None and bridge.any():
        sub = cap[idx].ravel()
        sub[k[bridge]] = np.minimum(sub[k[bridge]], road_z[bridge] - params.under_bridge)
        cap[idx] = sub.reshape(cap[idx].shape)


def _near(x: np.ndarray, y: np.ndarray, xr: tuple[float, float],
          yr: tuple[float, float]) -> Optional[tuple[slice, slice]]:
    """Index of the vertex block inside the box xr x yr, or None."""
    c0, c1 = np.searchsorted(x[0], xr[0]), np.searchsorted(x[0], xr[1], side="right")
    r0, r1 = np.searchsorted(y[:, 0], yr[0]), np.searchsorted(y[:, 0], yr[1], side="right")
    if c0 >= c1 or r0 >= r1:
        return None
    return slice(r0, r1), slice(c0, c1)


def ground_mesh(elevation: MapLayer, terrain: Optional[MapLayer] = None,
                graph: Optional[RoadGraph] = None, params: GroundParams = GroundParams(),
                junction_margin: float = DEFAULT_JUNCTION_MARGIN,
                design: DesignParams = DesignParams(),
                cross_section: Optional[CrossSectionParams] = None) -> IndexedMesh:
    """Two triangles per mesh cell; smooth normals; colored by terrain class per vertex."""
    x, y, z = ground_heights(elevation, graph, params, terrain, junction_margin, design, cross_section)
    return mesh_from_heights(x, y, z, terrain, params.cell_size)


def mesh_from_heights(x: np.ndarray, y: np.ndarray, z: np.ndarray, terrain: Optional[MapLayer],
                      cell_size: float) -> IndexedMesh:
    """The ground mesh of ground_heights() output."""
    rows, cols = x.shape
    gy, gx = np.gradient(z, y[:, 0], x[0])
    normals = np.stack([-gx, -gy, np.ones_like(z)], axis=-1)
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    colors = vertex_colors(terrain, x, y, cell_size)
    k = np.arange(rows * cols).reshape(rows, cols)
    v00, v10, v01, v11 = k[:-1, :-1], k[:-1, 1:], k[1:, :-1], k[1:, 1:]
    tris = np.stack([v00, v10, v11, v00, v11, v01], axis=-1).ravel()
    log.info("ground: %d x %d vertices, %d triangles", cols, rows, len(tris) // 3)
    return IndexedMesh(
        "ground",
        [tuple(p) for p in np.stack([x, y, z], axis=-1).reshape(-1, 3).tolist()],
        [tuple(n) for n in normals.reshape(-1, 3).tolist()],
        [tuple(c) for c in colors.reshape(-1, 3).tolist()],
        tris.tolist(),
    )


def vertex_colors(terrain: Optional[MapLayer], x: np.ndarray, y: np.ndarray, cell: float) -> np.ndarray:
    """(rows, cols, 3) terrain colors, each the mean over a 3 x 3 patch of samples a third of
    a cell apart, so class borders blend instead of stepping cell by cell."""
    if terrain is None:
        return np.broadcast_to(np.array(TERRAIN_COLORS[LAND]), (*x.shape, 3)).copy()
    palette = np.zeros((max(TERRAIN_COLORS) + 1, 3))
    for c, rgb in TERRAIN_COLORS.items():
        palette[c] = rgb
    out = np.zeros((*x.shape, 3))
    offsets = (-cell / 3, 0.0, cell / 3)
    for dx in offsets:
        for dy in offsets:
            out += palette[terrain.sample(x + dx, y + dy).astype(int)]
    return out / len(offsets) ** 2


def mesh_height(x: np.ndarray, y: np.ndarray, z: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Height of the ground mesh (two triangles per cell, split along v00-v11) at points."""
    xs, ys = x[0], y[:, 0]
    i = np.clip(np.searchsorted(xs, px, side="right") - 1, 0, len(xs) - 2)
    j = np.clip(np.searchsorted(ys, py, side="right") - 1, 0, len(ys) - 2)
    u = np.clip((px - xs[i]) / (xs[i + 1] - xs[i]), 0.0, 1.0)
    v = np.clip((py - ys[j]) / (ys[j + 1] - ys[j]), 0.0, 1.0)
    z00, z10, z01, z11 = z[j, i], z[j, i + 1], z[j + 1, i], z[j + 1, i + 1]
    lower = u >= v  # triangle v00, v10, v11
    return np.where(lower, z00 + u * (z10 - z00) + v * (z11 - z10),
                    z00 + v * (z01 - z00) + u * (z11 - z01))


def surface_samples(graph: RoadGraph, na, planes, design: DesignParams,
                    cross_section: Optional[CrossSectionParams] = None,
                    step: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(x, y, z) of points on the road surfaces: every step along each road (outside
    bridges and tunnels) at five offsets across its driving and side lanes, and on each
    junction's plane inside its smallest trim."""
    loops = loop_edges(graph) if cross_section is not None else frozenset()
    px, py, pz = [], [], []
    for ca in na.chains:
        smp = ca.alignment.sample(step)
        keep = np.ones(len(smp.s), bool)
        for s0, length in bridge_spans(graph, ca) + tunnel_spans(graph, ca):
            keep &= (smp.s < s0) | (smp.s > s0 + length)
        if not keep.any():
            continue
        s, h = smp.s[keep], smp.hdg[keep]
        width = min(half_width(graph.edges[e]) if cross_section is None
                    else outer_half_width(graph.edges[e], cross_section, e in loops) for e in ca.chain.edge_ids)
        zc = evaluate_many(chain_elevation(graph, ca, planes), s)
        sup = chain_superelevation(graph, ca, planes, design)
        bank = np.sin(evaluate_many(sup, s)) if sup else np.zeros(len(s))
        for t in np.linspace(-width, width, 5):
            px.append(smp.x[keep] - t * np.sin(h))
            py.append(smp.y[keep] + t * np.cos(h))
            pz.append(zc + t * bank)
    for n in na.junctions:
        node, r = graph.nodes[n], 0.8 * min(na.trims[n].values())
        ang = np.linspace(0.0, 2 * np.pi, 8, endpoint=False)
        qx = np.concatenate([[node.x], node.x + r * np.cos(ang)])
        qy = np.concatenate([[node.y], node.y + r * np.sin(ang)])
        px.append(qx)
        py.append(qy)
        pz.append(planes[n].height(qx, qy))
    if not px:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    return np.concatenate(px), np.concatenate(py), np.concatenate(pz)


def _inside(x: np.ndarray, y: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    return (px >= x[0, 0]) & (px <= x[0, -1]) & (py >= y[0, 0]) & (py <= y[-1, 0])


def _press_down(x: np.ndarray, y: np.ndarray, z: np.ndarray, px: np.ndarray, py: np.ndarray,
                pz: np.ndarray, below: float, rounds: int = 3) -> np.ndarray:
    """Lower the three vertices of every triangle that covers a surface point until it lies
    below the point by at least below (each vertex by the most any of its points needs)."""
    z = z.copy()
    keep = _inside(x, y, px, py)
    px, py, pz = px[keep], py[keep], pz[keep]
    xs, ys = x[0], y[:, 0]
    for _ in range(rounds):
        excess = mesh_height(x, y, z, px, py) - (pz - below)
        bad = excess > 1e-6
        if not bad.any():
            break
        qx, qy, d = px[bad], py[bad], excess[bad]
        i = np.clip(np.searchsorted(xs, qx, side="right") - 1, 0, len(xs) - 2)
        j = np.clip(np.searchsorted(ys, qy, side="right") - 1, 0, len(ys) - 2)
        u = (qx - xs[i]) / (xs[i + 1] - xs[i])
        v = (qy - ys[j]) / (ys[j + 1] - ys[j])
        lower = u >= v
        drop = np.zeros(z.shape)
        for jj, ii in ((j, i), (j + 1, i + 1), (np.where(lower, j, j + 1), np.where(lower, i + 1, i))):
            np.maximum.at(drop, (jj, ii), d)
        z -= drop
    return z


def poke_through(x: np.ndarray, y: np.ndarray, z: np.ndarray, graph: RoadGraph,
                 junction_margin: float = DEFAULT_JUNCTION_MARGIN, design: DesignParams = DesignParams(),
                 cross_section: Optional[CrossSectionParams] = None, step: float = 2.0,
                 tol: float = 0.01) -> tuple[int, int, float]:
    """(road-surface samples, samples the ground covers by more than tol, worst depth [m])
    over surface_samples()."""
    na = network_alignment(graph, design, junction_margin, cross_section=cross_section)
    px, py, pz = surface_samples(graph, na, junction_planes(graph, na, design), design, cross_section, step)
    inside = _inside(x, y, px, py)
    if not inside.any():
        return 0, 0, 0.0
    depth = mesh_height(x, y, z, px[inside], py[inside]) - pz[inside]
    return int(inside.sum()), int((depth > tol).sum()), float(depth.max())
