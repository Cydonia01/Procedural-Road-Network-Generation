"""Heights for a RoadGraph from the input maps, between generation and the
OpenDRIVE builder (which never sees a map):

1. every node gets the elevation averaged over a disc of diameter
   ``smoothing``, at the node or, for a bend, at the fitted road's corner
   midpoint; at a junction this is the height of the junction area;
2. every edge gets heights every ``sample_spacing`` metres: the elevation
   averaged along the road over ``smoothing`` (a road is graded, not draped
   over every bump), shifted linearly so that it meets its nodes' heights.
   Terrain and water are sampled along the fitted road
   (roadgen.geometry.alignment), so a bend's heights and bridges are those
   under the curve the builder will draw, not under the straight edges;
3. stretches over water (from the terrain map, sampled at its own
   resolution) become bridges: the deck runs straight from shore to shore
   and stays at least ``bridge_clearance`` above the water surface. They
   are stored as ``Edge.bridges``; a node on water is raised the same way.

Deterministic, and the input graph is left unchanged.
"""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import asdict, dataclass
from typing import Callable, Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.geometry.alignment import NetworkAlignment, network_alignment
from roadgen.geometry.design import DesignParams
from roadgen.geometry.junction_space import DEFAULT_MARGIN
from roadgen.graph.model import RoadGraph
from roadgen.maps.model import WATER, MapLayer

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "drape")
SMOOTHING_SAMPLES = 9  # points averaged per height, along a road or across a node disc
MERGE_GAP = 2.0  # water stretches closer than this many terrain cells form one bridge


@dataclass(frozen=True)
class DrapeParams:
    sample_spacing: float = _D["sample_spacing"]  # max distance between profile points [m]
    smoothing: float = _D["smoothing"]  # length the terrain is averaged over [m]
    bridge_clearance: float = _D["bridge_clearance"]  # min deck height above water [m]

    def __post_init__(self) -> None:
        if self.sample_spacing <= 0 or self.smoothing < 0 or self.bridge_clearance < 0:
            raise ValueError("need sample_spacing > 0, smoothing >= 0 and bridge_clearance >= 0")


def drape(graph: RoadGraph, elevation: MapLayer, terrain: Optional[MapLayer] = None,
          params: DrapeParams = DrapeParams(), design: DesignParams = DesignParams(),
          junction_margin: float = DEFAULT_MARGIN) -> RoadGraph:
    """A copy of graph with Node.z, Edge.profile and Edge.bridges filled in.

    design, junction_margin: as for the OpenDRIVE builder, so the fitted roads
    sampled here are the ones it builds.
    """
    if elevation.kind != "elevation" or (terrain is not None and terrain.kind != "terrain"):
        raise ValueError("drape needs an elevation map (and optionally a terrain map)")
    out = copy.deepcopy(graph)
    try:
        na: Optional[NetworkAlignment] = network_alignment(out, design, junction_margin)
    except ValueError as e:  # the builder will reject the graph too; drape along straight edges
        log.warning("drape: no fitted roads (%s); sampling along straight edges", e)
        na = None
    # A bend node lies on its road at the corner's midpoint, not at the vertex.
    on_road: dict[int, tuple[float, float]] = {}
    for ca in na.chains if na else ():
        for n in ca.chain.inner:
            p = ca.alignment.pose(ca.node_station(n))
            on_road[n] = (p.x, p.y)
    for node in out.nodes.values():
        node.z = _node_height(*on_road.get(node.id, (node.x, node.y)), elevation, terrain, params)
    max_grade = 0.0
    for eid, edge in sorted(out.edges.items()):
        a, b = out.nodes[edge.from_node], out.nodes[edge.to_node]
        length = out.edge_length(eid)
        n = max(0, math.ceil(length / params.sample_spacing) - 1)
        d = np.linspace(0.0, length, n + 2)
        pos = _positions(out, eid, na)
        spans = _water_spans(pos, length, terrain)
        z = _edge_heights(a, b, d, length, elevation, params, spans, pos)
        edge.profile = [round(float(v), 3) for v in z[1:-1]]
        rounded = ((round(s0, 2), min(round(s1, 2), length)) for s0, s1 in spans)
        edge.bridges = [(s0, s1) for s0, s1 in rounded if s1 - s0 >= 0.01]
        if length > 0:
            max_grade = max(max_grade, float(np.max(np.abs(np.diff(z)) / np.diff(d))))
    out.metadata["drape"] = asdict(params)
    log.info("drape: %d nodes, %d edges, %d bridges, steepest grade %.1f %%",
             len(out.nodes), len(out.edges), sum(len(e.bridges) for e in out.edges.values()),
             100 * max_grade)
    return out


def _node_height(x: float, y: float, elevation: MapLayer, terrain: Optional[MapLayer],
                 params: DrapeParams) -> float:
    r = params.smoothing / 2
    angles = np.linspace(0, 2 * math.pi, SMOOTHING_SAMPLES - 1, endpoint=False)
    px = np.concatenate([[x], x + r * np.cos(angles)])
    py = np.concatenate([[y], y + r * np.sin(angles)])
    z = float(np.mean(elevation.sample(px, py)))
    if terrain is not None and terrain.sample(x, y) == WATER:
        z = max(z, float(elevation.sample(x, y)) + params.bridge_clearance)
    return round(z, 3)


Positions = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]


def _positions(graph: RoadGraph, edge_id: int, na: Optional[NetworkAlignment]) -> Positions:
    """(x, y) of the road at distances d from the edge's from_node (any shape of d)."""
    e = graph.edges[edge_id]
    a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
    length = graph.edge_length(edge_id)

    def pos(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        d = np.asarray(d, dtype=float)
        if na is not None:
            x, y = na.edge_points(graph, edge_id, d.ravel())
            return x.reshape(d.shape), y.reshape(d.shape)
        t = d / length if length else np.zeros_like(d)
        return a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)

    return pos


def _water_spans(pos: Positions, length: float,
                 terrain: Optional[MapLayer]) -> list[tuple[float, float]]:
    """(start, end) distances of the stretches of the road over water."""
    if terrain is None or length == 0:
        return []
    step = terrain.grid.cell_size / 2
    d = np.linspace(0.0, length, max(2, math.ceil(length / step) + 1))
    wet = terrain.sample(*pos(d)) == WATER
    spans: list[tuple[float, float]] = []
    k = 0
    while k < len(d):
        if not wet[k]:
            k += 1
            continue
        j = k
        while j + 1 < len(d) and wet[j + 1]:
            j += 1
        # Extend half a step to the shore on both sides (not past the ends).
        s0, s1 = max(0.0, d[k] - step / 2), min(length, d[j] + step / 2)
        if spans and s0 - spans[-1][1] < MERGE_GAP * terrain.grid.cell_size:
            spans[-1] = (spans[-1][0], s1)
        else:
            spans.append((float(s0), float(s1)))
        k = j + 1
    return spans


def _edge_heights(a, b, d: np.ndarray, length: float, elevation: MapLayer,
                  params: DrapeParams, spans: list[tuple[float, float]], pos: Positions) -> np.ndarray:
    """Heights at distances d from a: smoothed terrain, ends at the node heights,
    straight over water."""

    def smoothed(dist: np.ndarray) -> np.ndarray:
        offsets = np.linspace(-params.smoothing / 2, params.smoothing / 2, SMOOTHING_SAMPLES)
        dd = dist[:, None] + offsets[None, :]
        return elevation.sample(*pos(dd)).mean(axis=1)

    bounds = np.array([s for span in spans for s in span], dtype=float)
    every = np.concatenate([d, bounds])
    z = smoothed(every)
    # Shift linearly so the ends meet the nodes exactly.
    t = every / length if length else np.zeros_like(every)
    z += (1 - t) * (a.z - z[0]) + t * (b.z - z[len(d) - 1])
    z_d, z_bounds = z[:len(d)], z[len(d):]
    for k, (s0, s1) in enumerate(spans):
        inside = (d >= s0) & (d <= s1)
        if not inside.any():
            continue
        z0, z1 = z_bounds[2 * k], z_bounds[2 * k + 1]
        deck = z0 + (z1 - z0) * (d[inside] - s0) / max(s1 - s0, 1e-9)
        water = elevation.sample(*pos(d[inside])) + params.bridge_clearance
        z_d[inside] = np.maximum(deck, water)
    z_d[0], z_d[-1] = a.z, b.z
    return z_d
