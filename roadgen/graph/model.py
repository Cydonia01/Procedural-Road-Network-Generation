"""RoadGraph: the generator-agnostic road network model.

Generators produce a RoadGraph; the OpenDRIVE builder consumes one. Coordinates
are in meters, headings in radians, speeds in m/s.

Heights are optional: a flat graph has every Node.z = 0 and no Edge.profile.
roadgen.maps.drape fills them in from an elevation map.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import networkx as nx
import numpy as np

NodeKind = Literal["junction", "dead_end", "auto"]
RoadClass = Literal["highway", "avenue", "street"]

# Edges shorter than this are treated as zero-length.
MIN_EDGE_LENGTH = 1e-6


@dataclass
class Node:
    id: int
    x: float
    y: float
    kind: NodeKind = "auto"
    z: float = 0.0  # height [m]; at a junction, of the junction area at the node
    radius_cap: Optional[float] = None  # largest corner radius at this bend [m] (set by `align`)


@dataclass
class Edge:
    """A road between two nodes. Forward lanes run from_node -> to_node."""

    id: int
    from_node: int
    to_node: int
    road_class: RoadClass
    lanes_forward: int
    lanes_backward: int
    lane_width: float = 3.5
    speed_limit: float = 13.9
    # Heights [m] at evenly spaced points strictly between from_node and
    # to_node (k * length / (n + 1), k = 1..n); empty = straight between node heights.
    profile: list[float] = field(default_factory=list)
    # Stretches over water (or over another road) as (start, end) distances [m] from from_node.
    bridges: list[tuple[float, float]] = field(default_factory=list)
    # Stretches under ground as (start, end) distances [m] from from_node.
    tunnels: list[tuple[float, float]] = field(default_factory=list)

    @property
    def total_lanes(self) -> int:
        return self.lanes_forward + self.lanes_backward

    @property
    def is_one_way(self) -> bool:
        return (self.lanes_forward == 0) != (self.lanes_backward == 0)


@dataclass
class RoadGraph:
    nodes: dict[int, Node] = field(default_factory=dict)
    edges: dict[int, Edge] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: Node) -> Node:
        if node.id in self.nodes:
            raise ValueError(f"duplicate node id {node.id}")
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> Edge:
        if edge.id in self.edges:
            raise ValueError(f"duplicate edge id {edge.id}")
        self.edges[edge.id] = edge
        return edge

    def incident_edges(self, node_id: int) -> list[Edge]:
        """Edges touching node_id, sorted by edge id."""
        return [
            e
            for _, e in sorted(self.edges.items())
            if node_id in (e.from_node, e.to_node)
        ]

    def neighbors(self, node_id: int) -> list[int]:
        """Distinct node ids connected to node_id by an edge, sorted."""
        result = set()
        for e in self.incident_edges(node_id):
            result.add(e.to_node if e.from_node == node_id else e.from_node)
        return sorted(result)

    def degree(self, node_id: int) -> int:
        return len(self.incident_edges(node_id))

    def edge_length(self, edge_id: int) -> float:
        a, b = self._endpoints(edge_id)
        return math.hypot(b.x - a.x, b.y - a.y)

    def edge_heading(self, edge_id: int) -> float:
        """Heading from from_node to to_node in radians, in (-pi, pi]."""
        a, b = self._endpoints(edge_id)
        return math.atan2(b.y - a.y, b.x - a.x)

    def edge_profile(self, edge_id: int) -> tuple[np.ndarray, np.ndarray]:
        """(distance from from_node [m], height [m]) of the edge, endpoints included."""
        e = self.edges[edge_id]
        a, b = self._endpoints(edge_id)
        d = np.linspace(0.0, self.edge_length(edge_id), len(e.profile) + 2)
        return d, np.array([a.z, *e.profile, b.z], dtype=float)

    @property
    def has_elevation(self) -> bool:
        """Whether any node or edge has a height; False for a flat graph."""
        return (any(n.z != 0.0 for n in self.nodes.values())
                or any(e.profile for e in self.edges.values()))

    def to_networkx(self) -> nx.Graph:
        """Undirected graph with node/edge attributes copied from the model."""
        g = nx.Graph()
        for n in self.nodes.values():
            g.add_node(n.id, x=n.x, y=n.y, kind=n.kind)
        for e in self.edges.values():
            g.add_edge(
                e.from_node,
                e.to_node,
                id=e.id,
                from_node=e.from_node,
                to_node=e.to_node,
                road_class=e.road_class,
                lanes_forward=e.lanes_forward,
                lanes_backward=e.lanes_backward,
                lane_width=e.lane_width,
                speed_limit=e.speed_limit,
                length=self.edge_length(e.id),
            )
        return g

    def validate(self) -> list[str]:
        """Return a list of human-readable errors; empty means valid."""
        errors: list[str] = []
        seen_pairs: dict[frozenset[int], int] = {}
        for eid, e in sorted(self.edges.items()):
            missing = [n for n in (e.from_node, e.to_node) if n not in self.nodes]
            for n in missing:
                errors.append(f"edge {eid}: references missing node {n}")
            if not missing and self.edge_length(eid) < MIN_EDGE_LENGTH:
                errors.append(f"edge {eid}: zero length")

            pair = frozenset((e.from_node, e.to_node))
            if pair in seen_pairs:
                errors.append(
                    f"edge {eid}: duplicates edge {seen_pairs[pair]} "
                    f"between nodes {e.from_node} and {e.to_node}"
                )
            else:
                seen_pairs[pair] = eid

            if e.lanes_forward < 0 or e.lanes_backward < 0:
                errors.append(f"edge {eid}: negative lane count")
            if e.total_lanes < 1:
                errors.append(f"edge {eid}: has no lanes")
            if not missing and any(not 0 <= s0 < s1 <= self.edge_length(eid) + 1e-6
                                   for s0, s1 in e.bridges):
                errors.append(f"edge {eid}: bridge outside the edge")
            if not missing and any(not 0 <= s0 < s1 <= self.edge_length(eid) + 1e-6
                                   for s0, s1 in e.tunnels):
                errors.append(f"edge {eid}: tunnel outside the edge")
        return errors

    def _endpoints(self, edge_id: int) -> tuple[Node, Node]:
        e = self.edges[edge_id]
        return self.nodes[e.from_node], self.nodes[e.to_node]
