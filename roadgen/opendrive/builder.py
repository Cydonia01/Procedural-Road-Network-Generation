"""RoadGraph -> OpenDRIVE.

Nodes of degree 1 are dead ends; every other node becomes a junction. A
degree-2 node (a bend or a change of lane count) is a two-arm junction in
which every lane continues. Each edge becomes one <road> with a
single <line> geometry placed at the absolute (x, y, hdg) of the graph,
trimmed back to the junction boundary at junction ends. scenariogeneration's
automatic geometry adjustment is bypassed.

Heights (only if the graph has any, see RoadGraph.has_elevation): each
road gets an <elevationProfile> fitted to its edge's heights, flat at both
ends at the node's height; connecting roads lie flat at their junction
node's height. Edge.bridges become <objects><bridge> records.

Id scheme: edge roads get ids 1..E in edge-id order; then, per junction node
in node-id order, the junction gets the next id and its connecting roads the
ids after it. All ids are therefore unique across roads and junctions.
"""

from __future__ import annotations

import json
import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Union

from scenariogeneration import xodr

from roadgen.config import DEFAULTS
from roadgen.graph.model import Edge, RoadGraph
from roadgen.opendrive.elevation import road_elevation
from roadgen.opendrive.geometry import segment_start, trim
from roadgen.opendrive.junctions import (
    Arm,
    Movement,
    RoadEnd,
    build_junction,
    junction_trims,
)
from roadgen.opendrive.lanes import make_lanes

log = logging.getLogger(__name__)

BRIDGE_TYPE = "concrete"  # OpenDRIVE e_bridgeType

ODR_REV_MAJOR = "1"
ODR_REV_MINOR = "6"
ROAD_TYPE = xodr.RoadType.town
# scenariogeneration stamps datetime.now(); a fixed value keeps output reproducible.
HEADER_DATE = "1970-01-01T00:00:00"
DEFAULT_JUNCTION_MARGIN = DEFAULTS["opendrive"]["junction_margin"]

PathLike = Union[str, Path]


def sidecar_path(xodr_path: PathLike) -> Path:
    """Path of the graph->OpenDRIVE id mapping written next to an .xodr file."""
    return Path(xodr_path).with_suffix(".roadmap.json")


def oriented_lanes(edge: Edge) -> tuple[int, int, bool]:
    """(right_lanes, left_lanes, reversed) for the road built from edge.

    Backward-only one-way edges get a reversed reference line so that all
    lanes sit on the right side.
    """
    if edge.lanes_forward == 0:
        return edge.lanes_backward, 0, True
    return edge.lanes_forward, edge.lanes_backward, False


def node_trims(graph: RoadGraph, node_id: int, margin: float = DEFAULT_JUNCTION_MARGIN) -> dict[int, float]:
    """Per incident edge: how far from the junction node its road stops [m]."""
    node = graph.nodes[node_id]
    arms = []
    for e in graph.incident_edges(node_id):
        right, left, reverse = oriented_lanes(e)
        start_node = e.to_node if reverse else e.from_node
        other = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        heading = math.atan2(other.y - node.y, other.x - node.x)
        # Leaving the node along +s keeps the lane sides; along -s swaps them.
        lw, rw = left * e.lane_width, right * e.lane_width
        if node_id != start_node:
            lw, rw = rw, lw
        arms.append(Arm(e.id, heading, lw, rw))
    try:
        return junction_trims(arms, margin)
    except ValueError as err:
        raise ValueError(f"node {node_id}: {err}") from None


class OpenDriveBuilder:
    def __init__(
        self, name: str = "roadgen", junction_margin: float = DEFAULT_JUNCTION_MARGIN
    ) -> None:
        self.name = name
        self.junction_margin = junction_margin
        self.odr: Optional[xodr.OpenDrive] = None
        self.edge_to_road: dict[int, int] = {}
        self.node_to_junction: dict[int, int] = {}
        self.movements: list[Movement] = []
        self.bridges: dict[int, list[tuple[float, float]]] = {}  # road id -> (s, length)

    def build(self, graph: RoadGraph) -> xodr.OpenDrive:
        errors = graph.validate()
        if errors:
            raise ValueError("invalid RoadGraph: " + "; ".join(errors))
        junction_nodes = [n for n in sorted(graph.nodes) if graph.degree(n) >= 2]
        trims = {n: self._trim_distances(graph, n) for n in junction_nodes}

        odr = xodr.OpenDrive(self.name, ODR_REV_MAJOR, ODR_REV_MINOR)
        heights = graph.has_elevation
        edge_to_road: dict[int, int] = {}
        bridges: dict[int, list[tuple[float, float]]] = {}
        ends: dict[int, list[RoadEnd]] = {n: [] for n in junction_nodes}
        for road_id, (edge_id, edge) in enumerate(sorted(graph.edges.items()), 1):
            road, road_ends, spans = self._make_road(graph, edge, road_id, trims, heights)
            if spans:
                bridges[road_id] = spans
            for node_id, end in road_ends:
                ends[node_id].append(end)
            odr.add_road(road)
            edge_to_road[edge_id] = road_id
            log.debug("edge %d -> road %d", edge_id, road_id)

        next_id = len(edge_to_road) + 1
        node_to_junction: dict[int, int] = {}
        movements: list[Movement] = []
        for node_id in junction_nodes:
            junction_id = next_id
            z = graph.nodes[node_id].z if heights else None
            junction, roads, moves = build_junction(junction_id, ends[node_id], junction_id + 1, z)
            for end in ends[node_id]:
                road = odr.roads[str(end.road_id)]
                link = road.add_successor if end.contact == "end" else road.add_predecessor
                link(xodr.ElementType.junction, junction_id)
            for road in roads:
                odr.add_road(road)
            odr.add_junction(junction)
            node_to_junction[node_id] = junction_id
            movements += moves
            next_id = junction_id + 1 + len(roads)
            log.debug("node %d -> junction %d (%d connecting roads)", node_id, junction_id, len(roads))

        self.odr = odr
        self.edge_to_road = edge_to_road
        self.node_to_junction = node_to_junction
        self.movements = movements
        self.bridges = bridges
        return odr

    def write(self, path: PathLike) -> Path:
        """Write the .xodr and its id-mapping sidecar JSON; return the sidecar path."""
        if self.odr is None:
            raise RuntimeError("call build() before write()")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        root = self.odr.get_element()
        root.find("header").set("date", HEADER_DATE)
        self._add_bridges(root)
        # scenariogeneration's prettyprint round-trips through minidom, which
        # dominates write time on large networks; ET.indent is equivalent and fast.
        ET.indent(root, space="    ")
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        sidecar = sidecar_path(path)
        sidecar.write_text(
            json.dumps(
                {
                    "xodr": path.name,
                    "edge_to_road": {str(e): r for e, r in sorted(self.edge_to_road.items())},
                    "node_to_junction": {
                        str(n): j for n, j in sorted(self.node_to_junction.items())
                    },
                    "movements": [asdict(m) for m in self.movements],
                },
                indent=2,
            )
            + "\n"
        )
        return sidecar

    def _add_bridges(self, root: ET.Element) -> None:
        """<objects><bridge> per bridge span (scenariogeneration has no bridges)."""
        if not self.bridges:
            return
        for road in root.findall("road"):
            spans = self.bridges.get(int(road.get("id")))
            if not spans:
                continue
            objects = road.find("objects")
            if objects is None:
                objects = ET.Element("objects")
                road.insert(list(road).index(road.find("lanes")) + 1, objects)
            for k, (s, length) in enumerate(spans):
                ET.SubElement(objects, "bridge", {
                    "s": f"{s:.3f}", "length": f"{length:.3f}", "name": f"bridge_{road.get('id')}_{k}",
                    "id": f"{road.get('id')}{k:03d}", "type": BRIDGE_TYPE,
                })

    def _trim_distances(self, graph: RoadGraph, node_id: int) -> dict[int, float]:
        return node_trims(graph, node_id, self.junction_margin)

    @staticmethod
    def _make_road(
        graph: RoadGraph, edge: Edge, road_id: int, trims: dict[int, dict[int, float]],
        heights: bool = False,
    ) -> tuple[xodr.Road, list[tuple[int, RoadEnd]], list[tuple[float, float]]]:
        """The road, its ends at junctions, and its bridges as (s, length)."""
        right, left, reverse = oriented_lanes(edge)
        start_node, end_node = edge.from_node, edge.to_node
        if reverse:
            start_node, end_node = end_node, start_node
        a, b = graph.nodes[start_node], graph.nodes[end_node]

        trim_start = trims[start_node][edge.id] if start_node in trims else 0.0
        trim_end = trims[end_node][edge.id] if end_node in trims else 0.0
        try:
            p0, p1 = trim((a.x, a.y), (b.x, b.y), trim_start, trim_end)
        except ValueError as e:
            raise ValueError(f"edge {edge.id} is too short for its junctions: {e}") from None
        pose, length = segment_start(p0, p1)

        planview = xodr.PlanView()
        planview.add_fixed_geometry(xodr.Line(length), pose.x, pose.y, pose.hdg)
        road = xodr.Road(
            road_id,
            planview,
            make_lanes(right, left, edge.lane_width),
            name=f"edge_{edge.id}",
        )
        road.add_type(ROAD_TYPE, speed=edge.speed_limit, speed_unit="m/s")

        # Edge distances measured along this road's direction.
        edge_length = graph.edge_length(edge.id)
        d, z = graph.edge_profile(edge.id)
        spans = list(edge.bridges)
        if reverse:
            d, z = edge_length - d[::-1], z[::-1]
            spans = [(edge_length - s1, edge_length - s0) for s0, s1 in spans]
        if heights:
            for rec in road_elevation(d, z, trim_start, edge_length - trim_end, a.z, b.z):
                road.add_elevation(*rec)
        bridges = []
        for s0, s1 in sorted(spans):
            s0, s1 = max(s0 - trim_start, 0.0), min(s1 - trim_start, length)
            if s1 - s0 > 1e-3:
                bridges.append((s0, s1 - s0))

        ends = []
        for node_id, contact, contact_pose in (
            (start_node, "start", pose),
            (end_node, "end", pose._replace(x=p1[0], y=p1[1])),
        ):
            if node_id in trims:
                ends.append(
                    (
                        node_id,
                        RoadEnd(
                            edge_id=edge.id,
                            road_id=road_id,
                            contact=contact,
                            pose=contact_pose,
                            right_lanes=right,
                            left_lanes=left,
                            lane_width=edge.lane_width,
                            speed_limit=edge.speed_limit,
                        ),
                    )
                )
        return road, ends, bridges
