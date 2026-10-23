"""3D ground from the input maps, so that esmini's roads (which follow
their <elevationProfile>) don't float over an empty scene.

The elevation map is resampled to a triangle mesh every ``cell_size``
metres and colored by terrain class (water / land / vegetation). Given a
draped RoadGraph, the ground is levelled ``below_road`` under every road
(out to ``road_margin`` beyond its edge plus one mesh cell) and under every
junction area, using the same fitted roads, elevation and banking, junction
trims and junction planes as the OpenDRIVE builder (roadgen.geometry), so
the ground never pokes through a road. Under bridges
the ground is left alone, and water is only ever lowered.
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
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN as DEFAULT_JUNCTION_MARGIN
from roadgen.geometry.profiles import bridge_spans
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

    def __post_init__(self) -> None:
        if self.cell_size <= 0 or self.road_margin < 0 or self.below_road < 0:
            raise ValueError("need cell_size > 0, road_margin >= 0 and below_road >= 0")


def ground_heights(elevation: MapLayer, graph: Optional[RoadGraph] = None,
                   params: GroundParams = GroundParams(), terrain: Optional[MapLayer] = None,
                   junction_margin: float = DEFAULT_JUNCTION_MARGIN, design: DesignParams = DesignParams(),
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mesh vertices (x, y, z), each of shape (rows, cols), levelled under graph's roads."""
    x0, x1, y0, y1 = elevation.grid.bounds
    xs = np.linspace(x0, x1, max(2, math.ceil((x1 - x0) / params.cell_size) + 1))
    ys = np.linspace(y0, y1, max(2, math.ceil((y1 - y0) / params.cell_size) + 1))
    x, y = np.meshgrid(xs, ys)
    z = elevation.sample(x, y)
    if graph is None or not graph.has_elevation:
        return x, y, z
    target = np.full(z.shape, np.inf)
    try:
        na = network_alignment(graph, design, junction_margin)
    except ValueError as err:  # the builder rejects such a graph too
        log.warning("ground: roads not levelled (%s)", err)
        return x, y, z
    planes = junction_planes(graph, na, design)
    reach_extra = params.road_margin + params.cell_size
    for ca in na.chains:
        _level_chain(graph, ca, planes, design, x, y, target, params, reach_extra)
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
    return x, y, z


def _level_chain(graph: RoadGraph, ca: ChainAlignment, planes, design: DesignParams,
                 x: np.ndarray, y: np.ndarray, target: np.ndarray, params: GroundParams,
                 reach_extra: float) -> None:
    """Lower target under one chain road: below its surface (elevation and banking)
    out to its widest half width plus reach_extra, except under bridges."""
    smp = ca.alignment.sample(params.cell_size / 2)
    reach = max(half_width(graph.edges[eid]) for eid in ca.chain.edge_ids) + reach_extra
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
    for s0, length in bridge_spans(graph, ca):
        keep &= (s < s0) | (s > s0 + length)
    k, jj, s = k[keep], jj[keep], s[keep]
    # Signed lateral offset (left positive) from the nearest centre-line sample.
    h = smp.hdg[jj]
    t = -(px[k] - smp.x[jj]) * np.sin(h) + (py[k] - smp.y[jj]) * np.cos(h)
    road_z = evaluate_many(chain_elevation(graph, ca, planes), s)
    sup = chain_superelevation(graph, ca, planes, design)
    if sup:
        road_z = road_z + t * np.sin(evaluate_many(sup, s))
    sub = target[idx].ravel()
    sub[k] = np.minimum(sub[k], road_z - params.below_road)
    target[idx] = sub.reshape(target[idx].shape)


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
                design: DesignParams = DesignParams()) -> IndexedMesh:
    """Two triangles per mesh cell; smooth normals; colored by terrain class per vertex."""
    x, y, z = ground_heights(elevation, graph, params, terrain, junction_margin, design)
    rows, cols = x.shape
    gy, gx = np.gradient(z, y[:, 0], x[0])
    normals = np.stack([-gx, -gy, np.ones_like(z)], axis=-1)
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    classes = terrain.sample(x, y) if terrain is not None else np.full(z.shape, LAND)
    k = np.arange(rows * cols).reshape(rows, cols)
    v00, v10, v01, v11 = k[:-1, :-1], k[:-1, 1:], k[1:, :-1], k[1:, 1:]
    tris = np.stack([v00, v10, v11, v00, v11, v01], axis=-1).ravel()
    log.info("ground: %d x %d vertices, %d triangles", cols, rows, len(tris) // 3)
    return IndexedMesh(
        "ground",
        [tuple(p) for p in np.stack([x, y, z], axis=-1).reshape(-1, 3).tolist()],
        [tuple(n) for n in normals.reshape(-1, 3).tolist()],
        [TERRAIN_COLORS[int(c)] for c in classes.ravel()],
        tris.tolist(),
    )
