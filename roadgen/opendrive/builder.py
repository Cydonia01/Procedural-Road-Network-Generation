"""RoadGraph -> OpenDRIVE.

The road shape comes from roadgen.geometry.alignment: every chain of edges
through road bends (two-road nodes the road simply continues through, see
roadgen.geometry.chains) becomes one <road> whose plan view is the chain's
fitted lines, spirals and arcs, placed at absolute poses (scenariogeneration's
automatic geometry adjustment is bypassed). Nodes where a chain ends with two
or more roads become junctions; nodes of degree 1 are dead ends.

Along a road (roadgen.geometry.profiles): lane sections with width tapers
where the lane count or width changes, <type> speed records with the edges'
limits lowered to corner advisory speeds, an <elevationProfile> through all
height samples (flat at both ends at a junction node's height; connecting
roads lie flat at it), and Edge.bridges as <objects><bridge> records.

Id scheme: chain roads get ids 1..C in chain order (by smallest edge id);
then, per junction node in node-id order, the junction gets the next id and
its connecting roads the ids after it. All ids are therefore unique across
roads and junctions.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Union

from scenariogeneration import xodr

from roadgen.config import DEFAULTS
from roadgen.geometry import GEOMETRY_VERSION
from roadgen.geometry.alignment import (
    ChainAlignment,
    NetworkAlignment,
    network_alignment,
)
from roadgen.geometry.design import DesignParams
from roadgen.geometry.profiles import bridge_spans, lane_layout, speed_profile, tunnel_spans
from roadgen.geometry.vertical import (
    JunctionPlane,
    chain_elevation,
    chain_superelevation,
    junction_planes,
)
from roadgen.graph.model import RoadGraph
from roadgen.opendrive.junctions import (
    JunctionParams,
    Movement,
    RoadEnd,
    build_junction,
    piece_geometry,
)
from roadgen.opendrive.lanes import make_lane_sections

log = logging.getLogger(__name__)

BRIDGE_TYPE = "concrete"  # OpenDRIVE e_bridgeType
TUNNEL_TYPE = "standard"  # OpenDRIVE e_tunnelType

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


class OpenDriveBuilder:
    def __init__(
        self, name: str = "roadgen", junction_margin: float = DEFAULT_JUNCTION_MARGIN,
        design: DesignParams = DesignParams(), junctions: JunctionParams = JunctionParams(),
    ) -> None:
        self.name = name
        self.junction_margin = junction_margin
        self.design = design
        self.junction_params = junctions
        self.odr: Optional[xodr.OpenDrive] = None
        self.alignment: Optional[NetworkAlignment] = None
        self.edge_to_road: dict[int, int] = {}
        self.edge_s_range: dict[int, tuple[int, float, float]] = {}  # edge -> (road, s0, s1)
        self.node_to_junction: dict[int, int] = {}
        self.movements: list[Movement] = []
        self.bridges: dict[int, list[tuple[float, float]]] = {}  # road id -> (s, length)
        self.tunnels: dict[int, list[tuple[float, float]]] = {}  # road id -> (s, length)

    def build(self, graph: RoadGraph) -> xodr.OpenDrive:
        errors = graph.validate()
        if errors:
            raise ValueError("invalid RoadGraph: " + "; ".join(errors))
        na = network_alignment(graph, self.design, self.junction_margin)

        odr = xodr.OpenDrive(self.name, ODR_REV_MAJOR, ODR_REV_MINOR)
        heights = graph.has_elevation
        edge_to_road: dict[int, int] = {}
        edge_s_range: dict[int, tuple[int, float, float]] = {}
        bridges: dict[int, list[tuple[float, float]]] = {}
        tunnels: dict[int, list[tuple[float, float]]] = {}
        ends: dict[int, list[RoadEnd]] = {n: [] for n in na.junctions}
        junctions = set(na.junctions)
        planes = junction_planes(graph, na, self.design) if heights else None
        for road_id, ca in enumerate(na.chains, 1):
            road, road_ends, spans = self._make_road(graph, ca, road_id, junctions, planes, self.design)
            if spans:
                bridges[road_id] = spans
            if tunnel_spans(graph, ca):
                tunnels[road_id] = tunnel_spans(graph, ca)
            for node_id, end in road_ends:
                ends[node_id].append(end)
            odr.add_road(road)
            for eid in ca.chain.edge_ids:
                edge_to_road[eid] = road_id
                edge_s_range[eid] = (road_id, *ca.edge_range(eid))
            log.debug("edges %s -> road %d", ca.chain.edge_ids, road_id)

        next_id = len(na.chains) + 1
        node_to_junction: dict[int, int] = {}
        movements: list[Movement] = []
        for node_id in na.junctions:
            junction_id = next_id
            plane = planes[node_id] if planes is not None else None
            junction, roads, moves = build_junction(junction_id, ends[node_id], junction_id + 1, plane,
                                                    self.junction_params, graph.nodes[node_id].kind == "ramp")
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

        fallbacks = sum(m.path == "poly3" for m in movements)
        if fallbacks:
            log.info("%d of %d junction turns use a paramPoly3 (no clothoid fit)", fallbacks, len(movements))
        self.odr = odr
        self.alignment = na
        self.edge_to_road = edge_to_road
        self.edge_s_range = edge_s_range
        self.node_to_junction = node_to_junction
        self.movements = movements
        self.bridges = bridges
        self.tunnels = tunnels
        return odr

    def write(self, path: PathLike) -> Path:
        """Write the .xodr and its id-mapping sidecar JSON; return the sidecar path."""
        if self.odr is None:
            raise RuntimeError("call build() before write()")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        root = self.odr.get_element()
        root.find("header").set("date", HEADER_DATE)
        self._add_structures(root, "bridge", self.bridges, BRIDGE_TYPE)
        self._add_structures(root, "tunnel", self.tunnels, TUNNEL_TYPE)
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
                    "geometry_version": GEOMETRY_VERSION,
                    "edge_s_range": {
                        str(e): [r, round(s0, 6), round(s1, 6)]
                        for e, (r, s0, s1) in sorted(self.edge_s_range.items())
                    },
                    "corners": self._corner_records(),
                },
                indent=2,
            )
            + "\n"
        )
        return sidecar

    def _corner_records(self) -> list[dict]:
        """Fitted corners per road, for consumers that need curve data without the .xodr."""
        if self.alignment is None:
            return []
        out = []
        for road_id, ca in enumerate(self.alignment.chains, 1):
            for c in ca.corners:
                out.append({
                    "road": road_id, "node": c.node, "s_start": round(c.s_start, 6),
                    "s_end": round(c.s_end, 6), "radius": round(c.radius, 6),
                    "lane_radius": round(c.lane_radius, 6),
                    "design_speed": c.design_speed, "max_speed": round(c.max_speed, 6),
                    "advisory_speed": None if c.advisory_speed is None else round(c.advisory_speed, 6),
                })
        return out

    @staticmethod
    def _add_structures(root: ET.Element, tag: str, spans_by_road: dict[int, list[tuple[float, float]]],
                        kind: str) -> None:
        """<objects><bridge> / <tunnel> per span (scenariogeneration has neither)."""
        if not spans_by_road:
            return
        for road in root.findall("road"):
            spans = spans_by_road.get(int(road.get("id")))
            if not spans:
                continue
            objects = road.find("objects")
            if objects is None:
                objects = ET.Element("objects")
                road.insert(list(road).index(road.find("lanes")) + 1, objects)
            # Bridge ids end in 0-499, tunnel ids in 500-999, so they stay unique per road.
            base = 0 if tag == "bridge" else 500
            for k, (s, length) in enumerate(spans):
                ET.SubElement(objects, tag, {
                    "s": f"{s:.3f}", "length": f"{length:.3f}", "name": f"{tag}_{road.get('id')}_{k}",
                    "id": f"{road.get('id')}{base + k:03d}", "type": kind,
                })

    @staticmethod
    def _make_road(
        graph: RoadGraph, ca: ChainAlignment, road_id: int, junctions: set[int],
        planes: Optional[dict[int, JunctionPlane]] = None, design: DesignParams = DesignParams(),
    ) -> tuple[xodr.Road, list[tuple[int, RoadEnd]], list[tuple[float, float]]]:
        """The chain's road, its ends at junctions, and its bridges as (s, length)."""
        planview = xodr.PlanView()
        for piece in ca.alignment.pieces:
            planview.add_fixed_geometry(piece_geometry(piece), piece.start.x, piece.start.y, piece.start.hdg)
        ids = ca.chain.edge_ids
        name = f"edge_{ids[0]}" if len(ids) == 1 else "edges_" + "_".join(map(str, ids))
        road = xodr.Road(road_id, planview, make_lane_sections(lane_layout(graph, ca)), name=name)
        for s, speed in speed_profile(graph, ca):
            road.add_type(ROAD_TYPE, s=round(s, 6), speed=speed, speed_unit="m/s")

        if planes is not None:
            for rec in chain_elevation(graph, ca, planes):
                road.add_elevation(*rec)
        flat = {n: JunctionPlane(graph.nodes[n].x, graph.nodes[n].y, 0.0) for n in junctions}
        for rec in chain_superelevation(graph, ca, planes if planes is not None else flat, design):
            road.add_superelevation(*rec)

        ends = []
        for node_id, contact, pose, k in (
            (ca.chain.start, "start", ca.alignment.start, 0),
            (ca.chain.end, "end", ca.alignment.end, len(ids) - 1),
        ):
            if node_id not in junctions:
                continue
            edge = graph.edges[ids[k]]
            right, left = ca.chain.edges[k].lanes(edge)
            ends.append((node_id, RoadEnd(
                edge_id=edge.id, road_id=road_id, contact=contact, pose=pose,
                right_lanes=right, left_lanes=left, lane_width=edge.lane_width,
                speed_limit=edge.speed_limit,
            )))
        return road, ends, bridge_spans(graph, ca)
