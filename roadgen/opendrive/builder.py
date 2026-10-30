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

With HD maps on (stage B): side lanes and medians, road marks, and per
junction a control kind (roadgen.opendrive.control) with <priority> records
between conflicting movements, stop and yield lines as roadMark objects and
signs and traffic lights as <signal>s (roadgen.opendrive.furniture), and
per junction with lights a fixed-time plan (roadgen.opendrive.lights) with a
top-level <controller> and a <junction><controller>. Signals are numbered
1..N; furniture objects from (C + 1) * 1000 on, above every bridge and
tunnel id.

Id scheme: chain roads get ids 1..C in chain order (by smallest edge id);
then, per junction node in node-id order, the junction gets the next id and
its connecting roads the ids after it. All ids are therefore unique across
roads and junctions.
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
from roadgen.geometry import GEOMETRY_VERSION
from roadgen.geometry.alignment import (
    ChainAlignment,
    NetworkAlignment,
    network_alignment,
)
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    edge_cross_section,
    loop_edges,
)
from roadgen.geometry.design import DesignParams
from roadgen.geometry.profiles import (
    ADVISORY_DECEL,
    LaneLayout,
    bridge_spans,
    lane_layout,
    speed_profile,
    tunnel_spans,
)
from roadgen.geometry.vertical import (
    JunctionPlane,
    chain_elevation,
    chain_superelevation,
    junction_planes,
)
from roadgen.graph.model import RoadGraph
from roadgen.opendrive.control import (
    Control,
    approach_from,
    conflicts,
    junction_control,
    priority_pairs,
    priority_runs,
    yields_to,
)
from roadgen.opendrive.furniture import (
    FURT_LINE,
    LIGHT_SIZE,
    LINE_SETBACK,
    PRIORITY_SIGN_DISTANCE,
    SIGN_SIZE,
    SPEED_AFTER_JUNCTION,
    STACK,
    ObjectRecord,
    RoadSide,
    SignalRecord,
    approach_furniture,
    carriageway_edge,
    crosswalk_at,
    crosswalk_furniture,
    curve_warnings,
    end_geometry,
    speed_signs,
)
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.junctions import (
    CornerRoad,
    JunctionParams,
    Movement,
    RoadEnd,
    build_junction,
    corner_roads,
    piece_geometry,
)
from roadgen.opendrive.lanes import make_lane_sections
from roadgen.opendrive.lights import phase_groups, signal_plan
from roadgen.opendrive.marks import RoadMarkParams, road_marks

log = logging.getLogger(__name__)

BRIDGE_TYPE = "concrete"  # OpenDRIVE e_bridgeType
MARK_LIFT = 0.02  # line and crosswalk corners this far above the road surface (no z-fighting in esmini) [m]
TUNNEL_TYPE = "standard"  # OpenDRIVE e_tunnelType

ODR_REV_MAJOR = "1"
ODR_REV_MINOR = "6"
ROAD_TYPE = xodr.RoadType.town
# scenariogeneration stamps datetime.now(); a fixed value keeps output reproducible.
HEADER_DATE = "1970-01-01T00:00:00"
DEFAULT_JUNCTION_MARGIN = DEFAULTS["opendrive"]["junction_margin"]

PathLike = Union[str, Path]


def _directions(lanes_at, s: float) -> set[int]:
    """Directions of travel of a road at station s: +1 with driving lanes on the right."""
    ids = [lid for lid, kind, *_ in lanes_at(s) if kind == "driving"]
    return {d for d, here in ((1, any(i < 0 for i in ids)), (-1, any(i > 0 for i in ids))) if here}


def _validity(lanes_at, s: float, direction: int) -> Optional[tuple[int, int]]:
    """Driving lanes of a direction of travel at s, as (innermost, outermost)."""
    ids = [lid for lid, kind, *_ in lanes_at(s) if kind == "driving" and (lid < 0) == (direction > 0)]
    return (min(ids, key=abs), max(ids, key=abs)) if ids else None


def _cubic_at(records: list, s: float) -> float:
    """Value at s of piecewise cubic records (s, a, b, c, d); 0 without records."""
    rec = None
    for r in records:
        if r[0] <= s + 1e-9:
            rec = r
    if rec is None:
        return 0.0
    ds = s - rec[0]
    return rec[1] + rec[2] * ds + rec[3] * ds * ds + rec[4] * ds ** 3


def sidecar_path(xodr_path: PathLike) -> Path:
    """Path of the graph->OpenDRIVE id mapping written next to an .xodr file."""
    return Path(xodr_path).with_suffix(".roadmap.json")


class OpenDriveBuilder:
    def __init__(
        self, name: str = "roadgen", junction_margin: float = DEFAULT_JUNCTION_MARGIN,
        design: DesignParams = DesignParams(), junctions: JunctionParams = JunctionParams(),
        hd: HdMapParams = HdMapParams(),
    ) -> None:
        self.name = name
        self.junction_margin = junction_margin
        self.design = design
        self.junction_params = junctions
        self.hd = hd
        self.odr: Optional[xodr.OpenDrive] = None
        self.alignment: Optional[NetworkAlignment] = None
        self.edge_to_road: dict[int, int] = {}
        self.edge_s_range: dict[int, tuple[int, float, float]] = {}  # edge -> (road, s0, s1)
        self.node_to_junction: dict[int, int] = {}
        self.movements: list[Movement] = []
        self.bridges: dict[int, list[tuple[float, float]]] = {}  # road id -> (s, length)
        self.tunnels: dict[int, list[tuple[float, float]]] = {}  # road id -> (s, length)
        self.cross_sections: dict[int, str] = {}  # edge id -> cross-section profile (HD maps on)
        self.corner_roads: list[CornerRoad] = []  # side lanes around junction corners (HD maps on)
        self.junction_records: list[dict] = []  # control per junction (HD maps on)
        self.movement_control: dict[int, dict] = {}  # connecting road -> role, priority, yields_to
        self.priorities: dict[int, list[tuple[int, int]]] = {}  # junction id -> (high, low) connecting roads
        self.conflicts: dict[int, list[tuple[int, int]]] = {}  # junction id -> conflicting connecting roads
        self.signals: list[SignalRecord] = []
        self.objects: list[ObjectRecord] = []
        self.signal_plans: list[dict] = []  # fixed-time light plans per junction with lights
        self.priority_runs = 0  # priority roads through 2+ priority junctions (306 / 307)

    def build(self, graph: RoadGraph) -> xodr.OpenDrive:
        errors = graph.validate()
        if errors:
            raise ValueError("invalid RoadGraph: " + "; ".join(errors))
        na = network_alignment(graph, self.design, self.junction_margin,
                               cross_section=self.hd.active_cross_section)

        odr = xodr.OpenDrive(self.name, ODR_REV_MAJOR, ODR_REV_MINOR)
        heights = graph.has_elevation
        edge_to_road: dict[int, int] = {}
        edge_s_range: dict[int, tuple[int, float, float]] = {}
        bridges: dict[int, list[tuple[float, float]]] = {}
        tunnels: dict[int, list[tuple[float, float]]] = {}
        ends: dict[int, list[RoadEnd]] = {n: [] for n in na.junctions}
        self.signals, self.objects = [], []
        self._layouts: dict[int, LaneLayout] = {}
        self._roll: dict[int, tuple[list, list]] = {}  # road id -> elevation, superelevation records
        junctions = set(na.junctions)
        loops = loop_edges(graph) if self.hd.enabled else frozenset()
        planes = junction_planes(graph, na, self.design) if heights else None
        for road_id, ca in enumerate(na.chains, 1):
            road, road_ends, spans, layout, roll = self._make_road(
                graph, ca, road_id, junctions, planes, self.design, self.hd.active_cross_section, loops,
                self.hd.active_marks)
            self._layouts[road_id] = layout
            self._roll[road_id] = roll
            if self.hd.enabled and not all(eid in loops for eid in ca.chain.edge_ids):
                self.signals += self._road_signs(graph, ca, road_id, layout, junctions)
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
        corners: list[CornerRoad] = []
        self.junction_records, self.movement_control, self.priorities, self.conflicts = [], {}, {}, {}
        self._signal_junctions: list[tuple] = []
        self._at_junction = {(e.road_id, e.contact) for es in ends.values() for e in es}
        self.signal_plans: list[dict] = []
        lengths = {road_id: ca.length for road_id, ca in enumerate(na.chains, 1)}
        layouts = self._layouts
        for node_id in na.junctions:
            junction_id = next_id
            plane = planes[node_id] if planes is not None else None
            paths: Optional[dict] = {} if self.hd.enabled else None
            junction, roads, moves = build_junction(junction_id, ends[node_id], junction_id + 1, plane,
                                                    self.junction_params, graph.nodes[node_id].kind == "ramp",
                                                    paths)
            if self.hd.enabled:
                self._control(graph, node_id, junction_id, ends[node_id], moves, paths, lengths, layouts)
            for end in ends[node_id]:
                road = odr.roads[str(end.road_id)]
                link = road.add_successor if end.contact == "end" else road.add_predecessor
                link(xodr.ElementType.junction, junction_id)
            corner, corner_conns, corner_recs = corner_roads(junction_id, ends[node_id],
                                                             junction_id + 1 + len(roads), plane, tuple(moves))
            for conn in corner_conns:
                junction.add_connection(conn)
            roads += corner
            for road in roads:
                odr.add_road(road)
            odr.add_junction(junction)
            node_to_junction[node_id] = junction_id
            movements += moves
            corners += corner_recs
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
        self.corner_roads = corners
        self.bridges = bridges
        self.tunnels = tunnels
        cs = self.hd.active_cross_section
        self.cross_sections = {} if cs is None else {
            eid: edge_cross_section(e, cs, eid in loops).profile for eid, e in sorted(graph.edges.items())}
        if self.hd.enabled:
            self._priority_road_signs(ends)
        self._add_furniture(odr, len(na.chains))
        return odr

    def _control(self, graph: RoadGraph, node_id: int, junction_id: int, ends: list[RoadEnd],
                 moves: list[Movement], paths: dict, lengths: dict[int, float],
                 layouts: dict[int, LaneLayout]) -> None:
        """Control, right of way and furniture of one junction (HD maps on)."""
        approaches = [approach_from(e, graph) for e in ends]
        control: Control = junction_control(node_id, approaches, graph, self.hd.control, self.hd.seed)
        by_key = {a.key: a for a in approaches}
        approach_of = {}
        for m in moves:
            end = next(e for e in ends if e.road_id == m.from_road and m.from_lane in e.in_lanes)
            approach_of[m.connecting_road] = by_key[(end.road_id, end.contact)]
        pairs = conflicts(moves, paths) if control.kind != "none" else []
        prio = priority_pairs(moves, pairs, control, approach_of)
        crosswalks = {}
        if control.kind != "none":
            for e in ends:
                length = lengths[e.road_id]
                other = (e.road_id, "start" if e.contact == "end" else "end")
                room = length / 2 if other in self._at_junction else length
                cw = crosswalk_at(e, length, graph.edges[e.edge_id].road_class, room)
                if cw is not None:
                    crosswalks[(e.road_id, e.contact)] = cw
        clash: set = set()
        if control.kind == "signal":
            permissive = {frozenset(p) for p in prio}
            clash = {frozenset((approach_of[a].key, approach_of[b].key)) for a, b in pairs
                     if frozenset((a, b)) not in permissive}
            groups = phase_groups([a for a, e in zip(approaches, ends) if e.in_lanes], clash)
            if len(groups) == 1 and not crosswalks:
                # One phase group and nobody crossing: nothing to switch between (B7, prompt 9).
                major = tuple(groups[0])
                control = Control("priority", {a.key: "major" if a.key in major else "yield" for a in approaches},
                                  major)
                prio = priority_pairs(moves, pairs, control, approach_of)
        self.priorities[junction_id] = prio
        self.conflicts[junction_id] = pairs
        yields = yields_to(prio)
        for m in moves:
            key = approach_of[m.connecting_road].key
            self.movement_control[m.connecting_road] = {
                "control": control.role(key), "priority": key in control.major,
                "yields_to": yields.get(m.connecting_road, [])}
        self.junction_records.append({
            "junction": junction_id, "node": node_id, "control": control.kind,
            "priority_roads": [list(k) for k in control.major],
            "approaches": [{"road": e.road_id, "contact": e.contact, "role": control.role((e.road_id, e.contact))}
                           for e in ends],
            "conflicts": len(pairs), "priorities": len(prio), "crosswalks": len(crosswalks)})
        for e in ends:
            key = (e.road_id, e.contact)
            lanes_at = layouts[e.road_id].lanes_at
            cw = crosswalks.get(key)
            if cw is not None:
                sigs, objs = crosswalk_furniture(
                    junction_id, cw, control.kind == "signal", _directions(lanes_at, cw.s),
                    lambda side, lanes_at=lanes_at: carriageway_edge(lanes_at, side),
                    lambda d, lanes_at=lanes_at, s=cw.s: _validity(lanes_at, s, d))
                self.signals += sigs
                self.objects += objs
            geom = end_geometry(e, lengths[e.road_id], lanes_at, cw.setback if cw else LINE_SETBACK)
            if geom is None:
                continue
            sigs, objs = approach_furniture(junction_id, control, geom, control.kind == "roundabout",
                                            self.hd.lights.overhead_min_lanes)
            self.signals += sigs
            self.objects += objs
        if control.kind == "signal":
            lanes = {(e.road_id, e.contact): len(e.in_lanes) for e in ends}
            crossing = max((cw.length for cw in crosswalks.values()), default=0.0)
            self._signal_junctions.append((junction_id, node_id, approaches, lanes, pairs, prio, clash, crossing))

    def _road_signs(self, graph: RoadGraph, ca: ChainAlignment, road_id: int, layout: LaneLayout,
                    junctions: set[int]) -> list[SignalRecord]:
        """Speed signs and curve warnings along a road, per direction of travel."""
        edges = [graph.edges[eid] for eid in ca.chain.edge_ids]
        ranges = [ca.edge_range(e.id) for e in edges]

        def limit(s: float) -> float:
            return next((e.speed_limit for e, (a, b) in zip(edges, ranges) if a - 1e-6 <= s <= b + 1e-6),
                        edges[-1].speed_limit)

        corners = [(c.s_start, c.s_end, c.deflection, c.advisory_speed) for c in ca.corners
                   if c.advisory_speed is not None]
        profile = speed_profile(graph, ca)
        out = []
        for d in sorted(_directions(layout.lanes_at, ca.length / 2)):
            entry = ca.chain.start if d > 0 else ca.chain.end
            side = RoadSide(road_id, d, ca.length, layout.lanes_at, entry in junctions)
            out += speed_signs(side, profile, limit)
            out += curve_warnings(side, corners, limit, ADVISORY_DECEL)
        return out

    def _priority_road_signs(self, ends: dict[int, list[RoadEnd]]) -> None:
        """306 after the first junction of a priority road through two or more priority
        junctions, 307 before the junction where it ends, and no 301 at the junctions in
        between (the 306 holds there)."""
        majors = {r["junction"]: tuple(tuple(k) for k in r["priority_roads"]) for r in self.junction_records
                  if r["control"] == "priority" and len(r["priority_roads"]) == 2}
        node_to_jid = {r["node"]: r["junction"] for r in self.junction_records}
        road_ends = {(e.road_id, e.contact): node_to_jid[n] for n, es in ends.items() for e in es}
        directions = {road: _directions(layout.lanes_at, layout.length / 2) for road, layout in self._layouts.items()}
        drop = set()
        added = []
        runs = priority_runs(majors, road_ends, directions)
        self.priority_runs = len(runs)
        for roads, jids in runs:
            for (road, d), jid in zip(roads[1:-1], jids[1:]):  # the 306 holds at junctions 2..m
                drop.add((road, "+" if d > 0 else "-", jid))
            first, last = roads[1], roads[-1]
            layout = self._layouts[first[0]]
            side = RoadSide(first[0], first[1], layout.length, layout.lanes_at, True)
            added.append(side.place("306", SPEED_AFTER_JUNCTION if first[1] > 0 else layout.length
                                    - SPEED_AFTER_JUNCTION, 2.0 + STACK))
            arrive = (last[0], "end" if last[1] > 0 else "start")
            if arrive in road_ends:  # the priority road ends at the junction ahead
                layout = self._layouts[last[0]]
                side = RoadSide(last[0], last[1], layout.length, layout.lanes_at, True)
                back = min(PRIORITY_SIGN_DISTANCE, layout.length / 2)
                added.append(side.place("307", layout.length - back if last[1] > 0 else back, 2.0 + STACK))
        self.signals = [s for s in self.signals if not (s.type == "301" and (s.road, s.orientation, s.junction)
                                                        in drop)] + added

    def _signal_plans(self, light_ids: dict[tuple[int, int, str], list[str]]) -> None:
        """Fixed-time plans of the junctions with lights, once the lights are numbered;
        light_ids: (junction, road, contact) -> signal ids."""
        self.signal_plans = []
        for controller, (jid, node_id, approaches, lanes, pairs, prio, clash, crossing) in enumerate(
                self._signal_junctions, 1):
            lights = {a.key: light_ids[(jid, *a.key)] for a in approaches if (jid, *a.key) in light_ids}
            if not lights:
                continue
            plan = signal_plan(jid, node_id, approaches, lanes, lights, self.hd.lights, self.hd.seed, clash, crossing)
            self.signal_plans.append({
                "junction": jid, "node": node_id, "controller": controller, "cycle": plan.cycle,
                "offset": plan.offset, "min_green": self.hd.lights.min_green,
                "groups": [[list(k) for k in g] for g in plan.groups],
                "lights": {s: list(key) for key, ids in sorted(lights.items()) for s in ids},
                "phases": list(plan.phases),
                "conflicts": [list(p) for p in pairs], "permissive": [list(p) for p in prio]})

    def _add_furniture(self, odr: xodr.OpenDrive, n_roads: int) -> None:
        """Signals and line objects into their roads, numbered (see the module docstring);
        then the light plans."""
        light_ids: dict[tuple[int, int, str], list[str]] = {}
        # scenariogeneration keeps the ids it has seen per class for the whole process and
        # renumbers any repeat; every build numbers its own signals and objects from scratch.
        for cls in ("Signal", "Object"):
            xodr.Signal._usedIDs.pop(cls, None)
            xodr.Signal._IDCounter.pop(cls, None)
        for k, rec in enumerate(self.signals, 1):
            height, width = LIGHT_SIZE if rec.dynamic else (SIGN_SIZE, SIGN_SIZE)
            sig = xodr.Signal(round(rec.s, 6), round(rec.t, 6), rec.country, rec.type, subtype=rec.subtype,
                              id=str(k), name=f"{'light' if rec.dynamic else rec.type}_{k}", zOffset=rec.z,
                              value=rec.value, unit="km/h" if rec.value is not None else None,
                              dynamic=xodr.Dynamic.yes if rec.dynamic else xodr.Dynamic.no,
                              orientation=xodr.Orientation.positive if rec.orientation == "+"
                              else xodr.Orientation.negative, height=height, width=width)
            sig.add_validity(*rec.validity)
            odr.roads[str(rec.road)].add_signal(sig)
            if rec.dynamic:
                light_ids.setdefault((rec.junction, rec.road, rec.contact), []).append(str(k))
        self._signal_plans(light_ids)
        base = (n_roads + 1) * 1000
        for k, rec in enumerate(self.objects):
            kind = xodr.ObjectType.crosswalk if rec.type == "crosswalk" else xodr.ObjectType.roadMark
            obj = xodr.Object(round(rec.s, 6), round(rec.t, 6), kind, subtype="-1", id=str(base + k), name=rec.name)
            # esmini puts every corner at the object's height plus its z: each corner gets the
            # road surface under it (elevation, superelevation positive falling to the right)
            # relative to the surface under the object, and MARK_LIFT, so lines on a sloped or
            # banked road end lie on it instead of sinking partly into it (B5, B7).
            elevation, superelevation = self._roll.get(rec.road, ([], []))
            length = self._layouts[rec.road].length

            def surface(u: float, v: float, rec=rec, elevation=elevation, superelevation=superelevation,
                        length=length) -> float:
                s = min(max(rec.s + u, 0.0), length)
                return _cubic_at(elevation, s) + (rec.t + v) * math.sin(_cubic_at(superelevation, s))

            base = surface(0.0, 0.0)
            outline = xodr.Outline(closed=False)
            # Lines along u = 0 (stop lines, zebras); a crossing at lights along its two edges.
            rows = (-rec.depth / 2 + FURT_LINE / 2, rec.depth / 2 - FURT_LINE / 2) if rec.edges else (0.0,)
            for row, u in enumerate(rows):
                for j, v in enumerate((-rec.half_span, rec.half_span)):
                    outline.add_corner(xodr.CornerLocal(round(u, 6), v, round(surface(u, v) - base + MARK_LIFT, 6),
                                                        0.0, id=2 * row + j))
            obj.add_outline(outline)
            for row in range(len(rows)):
                if rec.edges:
                    marking = xodr.Marking(xodr.RoadMarkColor.white, 2 * rec.half_span, xodr.SideType.front, 0.0,
                                           0.0, 0.0, width=FURT_LINE, zOffset=0.01)
                else:
                    marking = xodr.Marking(xodr.RoadMarkColor.white, rec.line or 2 * rec.half_span,
                                           xodr.SideType.front, rec.space, 0.0, 0.0, width=rec.depth, zOffset=0.01)
                marking.add_cornerReference(2 * row)
                marking.add_cornerReference(2 * row + 1)
                obj.add_marking(marking)
            if rec.validity is not None:
                obj.add_validity(*rec.validity)
            odr.roads[str(rec.road)].add_object(obj)

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
        for sig in root.iter("signal"):  # scenariogeneration upper-cases the country code
            if sig.get("country") == "OPENDRIVE":
                sig.set("country", "OpenDRIVE")
            if sig.get("value") is not None:  # ... and writes 50.0, which misses esmini's de_274-50
                sig.set("value", str(int(float(sig.get("value")))))
        if self.hd.control.priorities:
            self._add_priorities(root)
        self._add_controllers(root)
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
                    **({"cross_sections": {str(e): p for e, p in self.cross_sections.items()}}
                       if self.cross_sections else {}),
                    **({"corner_roads": [asdict(c) for c in self.corner_roads]} if self.corner_roads else {}),
                    **({"signal_plans": self.signal_plans} if self.signal_plans else {}),
                    **({"junctions": self.junction_records,
                        "movement_control": {str(r): c for r, c in sorted(self.movement_control.items())}}
                       if self.junction_records else {}),
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

    def _add_controllers(self, root: ET.Element) -> None:
        """A top-level <controller> per junction with lights, controlling its lights, and a
        <junction><controller> pointing to it (scenariogeneration has neither). Controllers
        go after the last road, before the junctions (OpenDRIVE 1.6 element order)."""
        if not self.signal_plans:
            return
        last_road = max(i for i, el in enumerate(root) if el.tag == "road")
        junctions = {j.get("id"): j for j in root.findall("junction")}
        for k, plan in enumerate(self.signal_plans, 1):
            ctrl = ET.Element("controller", {"id": str(plan["controller"]), "name": f"lights_j{plan['junction']}",
                                             "sequence": "0"})
            for sid in sorted(plan["lights"], key=int):
                ET.SubElement(ctrl, "control", {"signalId": sid, "type": "0"})
            root.insert(last_road + k, ctrl)
            ET.SubElement(junctions[str(plan["junction"])], "controller",
                          {"id": str(plan["controller"]), "type": "0", "sequence": "0"})

    def _add_priorities(self, root: ET.Element) -> None:
        """<priority high low> per conflicting pair, after the junction's connections
        (scenariogeneration has none)."""
        for junction in root.findall("junction"):
            for high, low in self.priorities.get(int(junction.get("id")), []):
                ET.SubElement(junction, "priority", {"high": str(high), "low": str(low)})

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
        cross_section: Optional[CrossSectionParams] = None, loops: frozenset[int] = frozenset(),
        marks: Optional[RoadMarkParams] = None,
    ) -> tuple[xodr.Road, list[tuple[int, RoadEnd]], list[tuple[float, float]], LaneLayout, tuple[list, list]]:
        """The chain's road, its ends at junctions, its bridges as (s, length), its lane layout
        and its elevation and superelevation records (s, a, b, c, d)."""
        planview = xodr.PlanView()
        for piece in ca.alignment.pieces:
            planview.add_fixed_geometry(piece_geometry(piece), piece.start.x, piece.start.y, piece.start.hdg)
        ids = ca.chain.edge_ids
        name = f"edge_{ids[0]}" if len(ids) == 1 else "edges_" + "_".join(map(str, ids))
        layout = lane_layout(graph, ca, cross_section, frozenset(junctions), loops)
        rm = None if marks is None or cross_section is None else road_marks(
            graph, ca, frozenset(junctions), marks, cross_section.profile)
        road = xodr.Road(road_id, planview, make_lane_sections(layout, rm), name=name)
        for s, speed in speed_profile(graph, ca):
            road.add_type(ROAD_TYPE, s=round(s, 6), speed=speed, speed_unit="m/s")

        elevation = chain_elevation(graph, ca, planes) if planes is not None else []
        for rec in elevation:
            road.add_elevation(*rec)
        flat = {n: JunctionPlane(graph.nodes[n].x, graph.nodes[n].y, 0.0) for n in junctions}
        superelevation = chain_superelevation(graph, ca, planes if planes is not None else flat, design)
        for rec in superelevation:
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
            # Explicit driving lanes only where other lanes are present (stage B): plain
            # roads keep the count x width offsets, bit for bit.
            plain = all(kind == "driving" for side in ("right", "left") for kind in layout.kinds(side))
            at = 0.0 if contact == "start" else ca.length
            lane_t = () if plain else tuple(sorted(layout.driving_offsets(at).items()))
            cross = () if plain else tuple(layout.lanes_at(at))
            ends.append((node_id, RoadEnd(
                edge_id=edge.id, road_id=road_id, contact=contact, pose=pose,
                right_lanes=right, left_lanes=left, lane_width=edge.lane_width,
                speed_limit=edge.speed_limit, lane_t=lane_t, cross=cross,
            )))
        return road, ends, bridge_spans(graph, ca), layout, (elevation, superelevation)
