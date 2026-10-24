"""OpenSCENARIO generation: many vehicles on a generated road network.

Vehicles spawn at random lane positions on normal (non-junction) roads,
weighted by lane length, keeping min_spawn_gap to any other vehicle in the
same lane. Each gets a constant target speed.

- random_lanes: no route; esmini's default controller follows the lane and
  picks a random connection at every junction.
- explicit_routes: a shortest path (by road length) to a random reachable
  destination, planned on the lane-level movement graph so every step exists
  for the lane the vehicle is actually in (esmini's default controller never
  changes lanes on a road). Each road on the path becomes a waypoint.

The simulation stops at sim_duration. Output is deterministic for a seed.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import xml.etree.ElementTree as ET
from bisect import bisect_left, insort
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Union

import networkx as nx
import numpy as np
from lxml import etree
from scenariogeneration import xosc

from roadgen.config import DEFAULTS, section
from roadgen.graph.model import RoadGraph
from roadgen.opendrive.builder import sidecar_path

log = logging.getLogger(__name__)

SpawnMode = Literal["random_lanes", "explicit_routes"]
SPAWN_MODES = ("random_lanes", "explicit_routes")
PathLike = Union[str, Path]

END_MARGIN = 5.0  # keep spawns this far from road ends (junction mouths) [m]
MAX_TRIES_PER_VEHICLE = 200
FIXED_DATE = dt.datetime(1970, 1, 1)  # reproducible FileHeader

# A generic passenger car (esmini shows a bounding box when no model is given).
_BBOX = xosc.BoundingBox(1.8, 4.5, 1.5, 1.4, 0.0, 0.75)
_FRONT_AXLE = xosc.Axle(0.5, 0.6, 1.6, 2.8, 0.3)
_REAR_AXLE = xosc.Axle(0.0, 0.6, 1.6, 0.0, 0.3)

_D = section(DEFAULTS, "scenario")


@dataclass(frozen=True)
class ScenarioParams:
    n_vehicles: int
    seed: int = DEFAULTS["seed"]
    sim_duration: float = _D["sim_duration"]
    spawn_mode: SpawnMode = _D["spawn_mode"]
    speed_range: tuple[float, float] = _D["speed_range"]  # [m/s]
    min_spawn_gap: float = _D["min_spawn_gap"]  # [m] along the lane

    def __post_init__(self) -> None:
        if self.n_vehicles < 0:
            raise ValueError("n_vehicles must be non-negative")
        if self.sim_duration <= 0:
            raise ValueError("sim_duration must be positive")
        if self.spawn_mode not in SPAWN_MODES:
            raise ValueError(f"unknown spawn_mode {self.spawn_mode!r}")
        lo, hi = self.speed_range
        if not 0 < lo <= hi:
            raise ValueError(f"invalid speed_range {self.speed_range}")
        if self.min_spawn_gap < 0:
            raise ValueError("min_spawn_gap must be non-negative")


@dataclass(frozen=True)
class LaneSlot:
    """A stretch [s0, s1] of a lane where it exists at full, constant width."""

    road: int
    lane: int
    length: float  # of the road [m]
    s0: float
    s1: float

    @property
    def span(self) -> float:
        return self.s1 - self.s0

    @property
    def entry(self) -> float:
        """Station where traffic in this lane enters the road (right lanes drive along +s)."""
        return 0.0 if self.lane < 0 else self.length

    @property
    def exit(self) -> float:
        return self.length if self.lane < 0 else 0.0


@dataclass
class VehiclePlan:
    name: str
    road: int
    lane: int
    s: float
    speed: float
    route: list[tuple[int, int]] = field(default_factory=list)  # (road, lane) incl. start


class ScenarioBuilder:
    """Builds an OpenSCENARIO file for a network written by OpenDriveBuilder."""

    def __init__(
        self,
        xodr_path: PathLike,
        graph: Optional[RoadGraph],
        edge_to_road_map: dict[int, int],
        movements: Optional[list[dict]] = None,
    ) -> None:
        self.xodr_path = Path(xodr_path)
        self.graph = graph
        self.edge_to_road = {int(k): int(v) for k, v in edge_to_road_map.items()}
        if graph is not None:
            missing = set(graph.edges) - set(self.edge_to_road)
            if missing:
                raise ValueError(f"edges without a road id: {sorted(missing)[:10]}")
        self.movements = movements or []
        self.slots = self._lane_slots()
        self.lane_graph = self._lane_graph()
        self.plans: list[VehiclePlan] = []

    @classmethod
    def from_files(cls, xodr_path: PathLike, graph: Optional[RoadGraph] = None) -> "ScenarioBuilder":
        """Load the edge->road map and movements from the .roadmap.json sidecar."""
        sidecar = json.loads(sidecar_path(xodr_path).read_text())
        return cls(xodr_path, graph, sidecar["edge_to_road"], sidecar.get("movements", []))

    # --- network --------------------------------------------------------------------

    def _lane_slots(self) -> list[LaneSlot]:
        """Full-width stretches of the driving lanes on normal roads, in road/lane/s order.

        Lane sections where a lane widens or narrows (tapers) are left out, so a
        stretch ends where its lane starts to be dropped.
        """
        root = etree.parse(str(self.xodr_path)).getroot()
        normal = set(self.edge_to_road.values())
        slots = []
        for road in root.findall("road"):
            rid = int(road.get("id"))
            if rid not in normal:
                continue
            length = float(road.get("length"))
            sections = road.findall("lanes/laneSection")
            bounds = [float(sec.get("s")) for sec in sections] + [length]
            open_at: dict[int, float] = {}
            for k, sec in enumerate(sections):
                full = {int(lane.get("id")) for lane in sec.iterfind("*/lane")
                        if lane.get("id") != "0" and lane.get("type") == "driving" and _full_width(lane)}
                for lid in sorted(set(open_at) - full):
                    slots.append(LaneSlot(rid, lid, length, open_at.pop(lid), bounds[k]))
                for lid in sorted(full - set(open_at)):
                    open_at[lid] = bounds[k]
            for lid, s0 in sorted(open_at.items()):
                slots.append(LaneSlot(rid, lid, length, s0, length))
        slots.sort(key=lambda sl: (sl.road, sl.lane, sl.s0))
        return slots

    def _lane_graph(self) -> nx.DiGraph:
        """(road, lane) -> (road, lane) through junction movements, weighted by length.

        Only lanes that run the whole road at full width are nodes: a vehicle
        following its lane (esmini's default controller never changes lanes)
        can only drive those end to end, and esmini's route planner reports
        errors for routes through lanes missing in some lane section.
        """
        lengths = {sl.road: sl.length for sl in self.slots}
        whole = {(sl.road, sl.lane) for sl in self.slots if sl.s0 < 1e-6 and sl.s1 > sl.length - 1e-6}
        g = nx.DiGraph()
        g.add_nodes_from(sorted(whole))
        for m in self.movements:
            src, dst = (m["from_road"], m["from_lane"]), (m["to_road"], m["to_lane"])
            if src in whole and dst in whole:
                g.add_edge(src, dst, weight=lengths.get(m["to_road"], 1.0))
        return g

    # --- planning ---------------------------------------------------------------------

    def plan(self, params: ScenarioParams) -> list[VehiclePlan]:
        rng = np.random.default_rng(params.seed)
        if params.spawn_mode == "explicit_routes" and not self.movements:
            raise ValueError("explicit_routes needs junction movements (the .roadmap.json)")
        spawns = self._spawn(params, rng)
        lo, hi = params.speed_range
        speeds = rng.uniform(lo, hi, size=len(spawns))
        plans = []
        for k, ((road, lane, s), speed) in enumerate(zip(spawns, speeds)):
            plan = VehiclePlan(f"car{k}", road, lane, round(s, 3), round(float(speed), 3))
            if params.spawn_mode == "explicit_routes":
                plan.route = self._route(plan, rng)
            plans.append(plan)
        self.plans = plans
        return plans

    def spawn_slots(self, routed: bool = False) -> list[LaneSlot]:
        """Slots a vehicle can spawn on: long enough, and running on to the road's
        end in the lane's travel direction (not into a dropped lane); with routed,
        only route-graph lanes (whole-road lanes) with a way on."""
        return [sl for sl in self.slots if sl.span > 2 * END_MARGIN
                and abs((sl.s1 if sl.lane < 0 else sl.s0) - sl.exit) < 1e-6
                and (not routed or ((sl.road, sl.lane) in self.lane_graph
                                    and self.lane_graph.out_degree((sl.road, sl.lane)) > 0))]

    def _spawn(self, params: ScenarioParams, rng: np.random.Generator) -> list[tuple[int, int, float]]:
        usable = self.spawn_slots(routed=params.spawn_mode == "explicit_routes")
        if params.n_vehicles and not usable:
            raise ValueError("no lane is long enough to spawn on")
        weights = np.array([sl.span - 2 * END_MARGIN for sl in usable])
        weights = weights / weights.sum() if len(usable) else weights
        taken: dict[tuple[int, int], list[float]] = {}
        spawns: list[tuple[int, int, float]] = []
        tries = 0
        while len(spawns) < params.n_vehicles:
            tries += 1
            if tries > MAX_TRIES_PER_VEHICLE * max(params.n_vehicles, 1):
                raise ValueError(
                    f"could only place {len(spawns)} of {params.n_vehicles} vehicles with "
                    f"min_spawn_gap={params.min_spawn_gap} m; use fewer vehicles or a smaller gap"
                )
            sl = usable[int(rng.choice(len(usable), p=weights))]
            s = float(rng.uniform(sl.s0 + END_MARGIN, sl.s1 - END_MARGIN))
            others = taken.setdefault((sl.road, sl.lane), [])
            i = bisect_left(others, s)
            near = [others[j] for j in (i - 1, i) if 0 <= j < len(others)]
            if any(abs(s - o) < params.min_spawn_gap for o in near):
                continue
            insort(others, s)
            spawns.append((sl.road, sl.lane, s))
        return spawns

    def _route(self, plan: VehiclePlan, rng: np.random.Generator) -> list[tuple[int, int]]:
        start = (plan.road, plan.lane)
        _, paths = nx.single_source_dijkstra(self.lane_graph, start, weight="weight")
        targets = sorted(v for v in paths if v[0] != plan.road)
        if not targets:
            log.warning("%s: no destination reachable from road %d lane %d", plan.name, *start)
            return [start]
        return paths[targets[int(rng.integers(len(targets)))]]

    def _waypoint_s(self, road: int, lane: int) -> float:
        """Middle of the lane's stretch where traffic enters the road (where a route arrives)."""
        stretches = [sl for sl in self.slots if sl.road == road and sl.lane == lane]
        if not stretches:
            raise ValueError(f"road {road} has no lane {lane}")
        entry = [sl for sl in stretches if sl.s0 - 1e-6 <= sl.entry <= sl.s1 + 1e-6]
        sl = entry[0] if entry else max(stretches, key=lambda x: x.span)
        return 0.5 * (sl.s0 + sl.s1)

    # --- output -------------------------------------------------------------------------

    def build(
        self, params: ScenarioParams, xosc_path: PathLike, scenegraph: Optional[PathLike] = None
    ) -> xosc.Scenario:
        plans = self.plan(params)
        xosc_dir = Path(xosc_path).resolve().parent
        roadfile = os.path.relpath(self.xodr_path.resolve(), xosc_dir)
        scenefile = os.path.relpath(Path(scenegraph).resolve(), xosc_dir) if scenegraph else None
        entities, init = xosc.Entities(), xosc.Init()
        for p in plans:
            vehicle = xosc.Vehicle(
                p.name, xosc.VehicleCategory.car, _BBOX, _FRONT_AXLE, _REAR_AXLE, 70.0, 4.0, 9.0
            )
            entities.add_scenario_object(p.name, vehicle)
            init.add_init_action(p.name, xosc.TeleportAction(xosc.LanePosition(p.s, 0, p.lane, p.road)))
            init.add_init_action(
                p.name,
                xosc.AbsoluteSpeedAction(
                    p.speed,
                    xosc.TransitionDynamics(xosc.DynamicsShapes.step, xosc.DynamicsDimension.time, 0),
                ),
            )
            if len(p.route) > 1:
                route = xosc.Route(f"{p.name}_route")
                route.add_waypoint(xosc.LanePosition(p.s, 0, p.lane, p.road), xosc.RouteStrategy.shortest)
                for road, lane in p.route[1:]:
                    route.add_waypoint(
                        xosc.LanePosition(self._waypoint_s(road, lane), 0, lane, road),
                        xosc.RouteStrategy.shortest,
                    )
                init.add_init_action(p.name, xosc.AssignRouteAction(route))

        stop = xosc.ValueTrigger(
            "stop_at_duration", 0, xosc.ConditionEdge.rising,
            xosc.SimulationTimeCondition(params.sim_duration, xosc.Rule.greaterThan), "stop",
        )
        return xosc.Scenario(
            f"{self.xodr_path.stem}_{params.spawn_mode}_s{params.seed}",
            "roadgen",
            xosc.ParameterDeclarations(),
            entities,
            xosc.StoryBoard(init, stop),
            xosc.RoadNetwork(roadfile=roadfile, scenegraph=scenefile),
            xosc.Catalog(),
            creation_date=FIXED_DATE,
        )

    def write(
        self, params: ScenarioParams, xosc_path: PathLike, scenegraph: Optional[PathLike] = None
    ) -> Path:
        """Write the .xosc; scenegraph (e.g. buildings .obj) becomes SceneGraphFile."""
        xosc_path = Path(xosc_path)
        xosc_path.parent.mkdir(parents=True, exist_ok=True)
        scenario = self.build(params, xosc_path, scenegraph)
        root = scenario.get_element()
        # Same fast, deterministic writer as the .xodr (scenariogeneration's
        # prettyprint goes through minidom).
        ET.indent(root, space="    ")
        ET.ElementTree(root).write(xosc_path, encoding="utf-8", xml_declaration=True)
        log.info(
            "wrote %s: %d vehicles, %s, %.0f s", xosc_path, len(self.plans), params.spawn_mode,
            params.sim_duration,
        )
        return xosc_path


def _full_width(lane: etree._Element) -> bool:
    """A lane whose width is a positive constant over its section (no taper)."""
    widths = lane.findall("width")
    return bool(widths) and all(
        float(w.get("a", 0)) > 0 and all(abs(float(w.get(k, 0))) < 1e-12 for k in "bcd") for w in widths)
