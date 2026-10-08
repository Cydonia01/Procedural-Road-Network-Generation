"""Manhattan grid: avenues run along +y, streets along +x (before rotation).

Node (i, j) sits at (i * avenue_spacing, j * street_spacing) for
i < n_avenues, j < n_streets, then the grid is rotated by rotation_deg and
translated to origin. Avenue edges join (i, j)-(i, j+1); street edges join
(i, j)-(i+1, j). Two-way edges point along +j / +i and take their lanes as
(forward, backward) along that direction.

One-way patterns: with "alternating", consecutive avenues (or streets) flip
direction, starting with +j (or +i) for index 0, and every one-way road puts
all of its lanes (the sum of the lane tuple) forward. A lane tuple with a
zero entry is one-way even under pattern "none".

Boundary: "dead_end" leaves the grid closed at its outermost avenues and
streets, so one-way patterns or dropped segments may leave traps there.
"loop" extends every avenue and street by one segment out to a two-way ring
road around the grid, so traffic can always get back in.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

import networkx as nx
import numpy as np

from roadgen.generators.base import RoadNetworkGenerator, assign_node_kinds, register
from roadgen.graph.model import Edge, Node, RoadClass, RoadGraph

log = logging.getLogger(__name__)

OnewayPattern = Literal["none", "alternating"]
BoundaryMode = Literal["dead_end", "loop"]
ONEWAY_PATTERNS = ("none", "alternating")
BOUNDARY_MODES = ("dead_end", "loop")


@dataclass(frozen=True)
class ManhattanParams:
    n_avenues: int
    n_streets: int
    avenue_spacing: float = 250.0
    street_spacing: float = 80.0
    rotation_deg: float = 0.0
    origin: tuple[float, float] = (0.0, 0.0)
    avenue_lanes: tuple[int, int] = (2, 2)
    street_lanes: tuple[int, int] = (1, 1)
    lane_width: float = 3.5
    avenue_speed: float = 13.9
    street_speed: float = 11.1
    jitter: float = 0.0  # max node displacement per axis [m], uniform
    edge_drop_prob: float = 0.0  # chance to drop each street segment
    avenue_oneway_pattern: OnewayPattern = "none"
    street_oneway_pattern: OnewayPattern = "none"
    boundary_mode: BoundaryMode = "dead_end"
    ring_offset: float = 60.0  # distance from the outermost grid lines to the ring [m]
    ring_lanes: tuple[int, int] = (2, 2)

    def __post_init__(self) -> None:
        if self.n_avenues < 1 or self.n_streets < 1 or self.n_avenues * self.n_streets < 2:
            raise ValueError("need at least two grid nodes")
        if self.avenue_spacing <= 0 or self.street_spacing <= 0:
            raise ValueError("spacings must be positive")
        if self.jitter < 0:
            raise ValueError("jitter must be non-negative")
        if not 0.0 <= self.edge_drop_prob <= 1.0:
            raise ValueError("edge_drop_prob must be in [0, 1]")
        for lanes in (self.avenue_lanes, self.street_lanes, self.ring_lanes):
            if len(lanes) != 2 or min(lanes) < 0 or sum(lanes) < 1:
                raise ValueError(f"invalid lane counts {lanes}")
        for pattern in (self.avenue_oneway_pattern, self.street_oneway_pattern):
            if pattern not in ONEWAY_PATTERNS:
                raise ValueError(f"unknown one-way pattern {pattern!r}")
        if self.boundary_mode not in BOUNDARY_MODES:
            raise ValueError(f"unknown boundary_mode {self.boundary_mode!r}")
        if self.ring_offset <= 0:
            raise ValueError("ring_offset must be positive")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "ManhattanParams":
        """Named parameter set; keyword arguments override individual fields."""
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # Midtown-like: the street grid is rotated ~29 deg from true north, avenues
    # ~250 m apart and streets ~80 m apart, both alternating one-way. Avenues
    # carry 4 lanes, cross streets 2, speeds at the 25 mph city limit.
    "manhattan_like": {
        "n_avenues": 10,
        "n_streets": 30,
        "avenue_spacing": 250.0,
        "street_spacing": 80.0,
        "rotation_deg": 29.0,
        "avenue_lanes": (4, 0),
        "street_lanes": (2, 0),
        "avenue_oneway_pattern": "alternating",
        "street_oneway_pattern": "alternating",
        "avenue_speed": 11.2,
        "street_speed": 11.2,
        "boundary_mode": "loop",
    },
}


ManhattanParams.PRESETS = PRESETS  # type: ignore[attr-defined]


def node_id(params: ManhattanParams, i: int, j: int) -> int:
    return i * params.n_streets + j + 1


@dataclass
class _Builder:
    """Mutable state while assembling one graph."""

    params: ManhattanParams
    graph: RoadGraph
    next_edge: int = 1

    def add(
        self, a: int, b: int, road_class: RoadClass, lanes: tuple[int, int],
        pattern: OnewayPattern, index: int, speed: float,
    ) -> int:
        """Add an edge a -> b (a on the lower-index side), applying the pattern."""
        if pattern == "alternating":
            total = sum(lanes)
            if index % 2:
                a, b = b, a
            fwd, bwd = total, 0
        else:
            fwd, bwd = lanes
        eid = self.next_edge
        self.graph.add_edge(
            Edge(eid, a, b, road_class, fwd, bwd, lane_width=self.params.lane_width, speed_limit=speed)
        )
        self.next_edge += 1
        return eid


@register("manhattan")
class ManhattanGenerator(RoadNetworkGenerator[ManhattanParams]):
    params_type = ManhattanParams
    description = "parametric grid of avenues and streets (one-way patterns, ring road)"

    def generate(self, params: ManhattanParams, seed: int) -> RoadGraph:
        rng = np.random.default_rng(seed)
        graph = RoadGraph(
            metadata={
                "name": f"manhattan_{params.n_avenues}x{params.n_streets}_s{seed}",
                "generator": self.name,
                "seed": seed,
                "params": asdict(params),
            }
        )
        b = _Builder(params, graph)
        self._place_nodes(graph, params, rng)
        street_ids = self._add_grid_edges(b)
        if params.boundary_mode == "loop":
            self._add_ring(b)
        dropped = self._drop_street_edges(graph, params, street_ids, rng)
        assign_node_kinds(graph)
        log.debug(
            "manhattan %dx%d: %d nodes, %d edges (%d dropped)",
            params.n_avenues, params.n_streets, len(graph.nodes), len(graph.edges), dropped,
        )
        return graph

    @staticmethod
    def _transform(p: ManhattanParams, gx: float, gy: float) -> tuple[float, float]:
        theta = math.radians(p.rotation_deg)
        c, s = math.cos(theta), math.sin(theta)
        return float(p.origin[0] + c * gx - s * gy), float(p.origin[1] + s * gx + c * gy)

    def _place_nodes(self, graph: RoadGraph, p: ManhattanParams, rng: np.random.Generator) -> None:
        # One draw for the whole grid keeps the stream independent of loop order.
        noise = rng.uniform(-p.jitter, p.jitter, size=(p.n_avenues, p.n_streets, 2)) if p.jitter else None
        for i in range(p.n_avenues):
            for j in range(p.n_streets):
                gx, gy = i * p.avenue_spacing, j * p.street_spacing
                if noise is not None:
                    gx, gy = gx + noise[i, j, 0], gy + noise[i, j, 1]
                graph.add_node(Node(node_id(p, i, j), *self._transform(p, gx, gy)))

    @staticmethod
    def _add_grid_edges(b: _Builder) -> list[int]:
        """Avenues then streets; returns the street edge ids (drop candidates)."""
        p = b.params
        for i in range(p.n_avenues):
            for j in range(p.n_streets - 1):
                b.add(node_id(p, i, j), node_id(p, i, j + 1), "avenue", p.avenue_lanes,
                      p.avenue_oneway_pattern, i, p.avenue_speed)
        street_ids = []
        for i in range(p.n_avenues - 1):
            for j in range(p.n_streets):
                street_ids.append(
                    b.add(node_id(p, i, j), node_id(p, i + 1, j), "street", p.street_lanes,
                          p.street_oneway_pattern, j, p.street_speed)
                )
        return street_ids

    def _add_ring(self, b: _Builder) -> None:
        """Ring road around the grid, joined to every avenue and street end."""
        p, graph = b.params, b.graph
        xmax = (p.n_avenues - 1) * p.avenue_spacing
        ymax = (p.n_streets - 1) * p.street_spacing
        lo_x, hi_x, lo_y, hi_y = -p.ring_offset, xmax + p.ring_offset, -p.ring_offset, ymax + p.ring_offset

        next_node = p.n_avenues * p.n_streets + 1

        def add_node(gx: float, gy: float) -> int:
            nonlocal next_node
            nid = next_node
            graph.add_node(Node(nid, *self._transform(p, gx, gy)))
            next_node += 1
            return nid

        bottom = [add_node(i * p.avenue_spacing, lo_y) for i in range(p.n_avenues)]
        top = [add_node(i * p.avenue_spacing, hi_y) for i in range(p.n_avenues)]
        left = [add_node(lo_x, j * p.street_spacing) for j in range(p.n_streets)]
        right = [add_node(hi_x, j * p.street_spacing) for j in range(p.n_streets)]
        bl, br, tl, tr = add_node(lo_x, lo_y), add_node(hi_x, lo_y), add_node(lo_x, hi_y), add_node(hi_x, hi_y)

        # Extensions continue each avenue/street with its own lanes and direction.
        for i in range(p.n_avenues):
            for a, c in ((bottom[i], node_id(p, i, 0)), (node_id(p, i, p.n_streets - 1), top[i])):
                b.add(a, c, "avenue", p.avenue_lanes, p.avenue_oneway_pattern, i, p.avenue_speed)
        for j in range(p.n_streets):
            for a, c in ((left[j], node_id(p, 0, j)), (node_id(p, p.n_avenues - 1, j), right[j])):
                b.add(a, c, "street", p.street_lanes, p.street_oneway_pattern, j, p.street_speed)

        # Two-way ring, counter-clockwise: bottom, right, top (reversed), left (reversed).
        ring = [bl, *bottom, br, *right, tr, *reversed(top), tl, *reversed(left)]
        for a, c in zip(ring, ring[1:] + ring[:1]):
            b.add(a, c, "avenue", p.ring_lanes, "none", 0, p.avenue_speed)

    @staticmethod
    def _drop_street_edges(
        graph: RoadGraph, p: ManhattanParams, street_ids: list[int], rng: np.random.Generator
    ) -> int:
        """Drop grid street segments at random without breaking reachability.

        A segment is dropped only if every direction it carried traffic in is
        still possible via other roads, so connectivity (and, for one-way
        patterns, strong connectivity between the endpoints) is preserved.
        """
        if p.edge_drop_prob == 0.0:
            return 0
        g = nx.MultiDiGraph()
        g.add_nodes_from(graph.nodes)
        for e in graph.edges.values():
            for u, v, lanes in ((e.from_node, e.to_node, e.lanes_forward), (e.to_node, e.from_node, e.lanes_backward)):
                if lanes > 0:
                    g.add_edge(u, v, key=e.id)
        # Draw for every candidate up front so the outcome depends only on the seed.
        draws = rng.random(len(street_ids))
        dropped = 0
        for eid, r in zip(sorted(street_ids), draws):
            if r >= p.edge_drop_prob:
                continue
            e = graph.edges[eid]
            arcs = [(u, v) for u, v, lanes in (
                (e.from_node, e.to_node, e.lanes_forward), (e.to_node, e.from_node, e.lanes_backward)
            ) if lanes > 0]
            for u, v in arcs:
                g.remove_edge(u, v, key=eid)
            if all(nx.has_path(g, u, v) for u, v in arcs):
                del graph.edges[eid]
                dropped += 1
            else:
                for u, v in arcs:
                    g.add_edge(u, v, key=eid)
        return dropped

