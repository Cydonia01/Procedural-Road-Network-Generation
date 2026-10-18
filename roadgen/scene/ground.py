"""3D ground from the input maps, so that esmini's roads (which follow
their <elevationProfile>) don't float over an empty scene.

The elevation map is resampled to a triangle mesh every ``cell_size``
metres and colored by terrain class (water / land / vegetation). Given a
draped RoadGraph, the ground is levelled ``below_road`` under every road
(out to ``road_margin`` beyond its edge plus one mesh cell) and under every
junction area, using the same junction trims and profile fit as the
OpenDRIVE builder, so the ground never pokes through a road. Under bridges
the ground is left alone, and water is only ever lowered.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.graph.model import RoadGraph
from roadgen.maps.model import LAND, WATER, MapLayer
from roadgen.maps.terrain import TERRAIN_COLORS
from roadgen.opendrive.builder import DEFAULT_JUNCTION_MARGIN, node_trims
from roadgen.opendrive.elevation import evaluate, road_elevation
from roadgen.scene.buildings import road_half_extent
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
                   junction_margin: float = DEFAULT_JUNCTION_MARGIN,
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
    trims = {n: node_trims(graph, n, junction_margin)
             for n in sorted(graph.nodes) if graph.degree(n) >= 2}
    reach_extra = params.road_margin + params.cell_size
    for eid, e in sorted(graph.edges.items()):
        a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
        length = graph.edge_length(eid)
        reach = road_half_extent(e) + reach_extra
        idx = _near(x, y, (min(a.x, b.x) - reach, max(a.x, b.x) + reach),
                    (min(a.y, b.y) - reach, max(a.y, b.y) + reach))
        if idx is None:
            continue
        ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
        rx, ry = x[idx] - a.x, y[idx] - a.y
        along, across = rx * ux + ry * uy, np.abs(rx * uy - ry * ux)
        inside = (along >= 0) & (along <= length) & (across <= reach)
        for s0, s1 in e.bridges:
            inside &= (along < s0) | (along > s1)
        if not inside.any():
            continue
        t0 = trims.get(e.from_node, {}).get(eid, 0.0)
        t1 = trims.get(e.to_node, {}).get(eid, 0.0)
        d, zs = graph.edge_profile(eid)
        records = road_elevation(d, zs, t0, length - t1, a.z, b.z)
        road_z = np.array([a.z if s <= t0 else b.z if s >= length - t1 else evaluate(records, s - t0)[0]
                           for s in along[inside]])
        sub = target[idx]
        sub[inside] = np.minimum(sub[inside], road_z - params.below_road)
        target[idx] = sub
    for n, arm_trims in trims.items():
        node = graph.nodes[n]
        r = max(arm_trims.values()) + reach_extra
        idx = _near(x, y, (node.x - r, node.x + r), (node.y - r, node.y + r))
        if idx is None:
            continue
        disc = np.hypot(x[idx] - node.x, y[idx] - node.y) <= r
        sub = target[idx]
        sub[disc] = np.minimum(sub[disc], node.z - params.below_road)
        target[idx] = sub
    level = np.isfinite(target)
    wet = terrain.sample(x, y) == WATER if terrain is not None else np.zeros(z.shape, bool)
    z = np.where(level & ~wet, target, np.where(level, np.minimum(z, target), z))
    return x, y, z


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
                junction_margin: float = DEFAULT_JUNCTION_MARGIN) -> IndexedMesh:
    """Two triangles per mesh cell; smooth normals; colored by terrain class per vertex."""
    x, y, z = ground_heights(elevation, graph, params, terrain, junction_margin)
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
