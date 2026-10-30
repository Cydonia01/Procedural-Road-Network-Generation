"""Junction control and right of way (stage B5, German rules).

Each junction of three or more roads gets a control kind and a role per
approach (road end), from the graph alone; the first matching rule wins:

| Junction | Control |
|---|---|
| two-road junction, ramp node | none |
| roundabout entry (graph.metadata["roundabouts"]) | the arm yields, the ring has priority |
| diamond terminal (graph.metadata["interchanges"][*]["terminals"]) | lights if the crossing road is an avenue or more, else the ramps stop |
| Node.control set | as set (signal, stop, yield, right_before_left, none) |
| the crossing road an avenue or more, or 5+ legs with an avenue | lights (signal) |
| only streets, right_before_left on | right before left |
| otherwise | priority road: the minor roads yield, or stop where the major speed is at least stop_speed, a minor arm is skewed more than stop_skew_deg, the junction has 5+ legs, or by the seeded stop_share |

The priority road is the highest-ranked arm (class, then lanes, then speed)
and the highest-ranked other arm, preferring the one most nearly opposite.

Right of way: two movements of a junction conflict where their paths cross
or end in the same lane (conflicts). For each conflicting pair from
different approaches one has priority (priority_pairs): the major road's
over a minor road's; between equal approaches the oncoming straight or right
turn over a left turn, and otherwise (right before left) the one coming from
the right. At lights only the permissive left turns get records (the phase
plans separate the rest, B6). The builder writes them as <junction>
<priority high low> records.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from shapely.geometry import LineString

from roadgen.config import DEFAULTS, section
from roadgen.geometry.cross_section import CLASSES
from roadgen.geometry.planar import wrap_angle
from roadgen.graph.model import RoadGraph

_D = section(DEFAULTS, "junction_control")

CONTROL_KINDS = ("none", "right_before_left", "yield", "stop", "signal")  # Node.control values
RANK = {c: k for k, c in enumerate(CLASSES)}  # street < avenue < highway
SALT = 5  # random stream of the stop share (np.random.default_rng([seed, SALT, node]))
OPPOSITE_TOL = math.radians(45.0)  # arms this close to opposite count as oncoming
EndKey = tuple[int, str]  # (road id, contact) of a road end


@dataclass(frozen=True)
class JunctionControlParams:
    signal_density: float = _D["signal_density"]  # `annotate`: lights from this density [people/km^2]
    stop_density: float = _D["stop_density"]  # `annotate`: stop instead of yield below this density [people/km^2]
    stop_speed: float = _D["stop_speed"]  # minor roads stop where the major road is this fast [m/s]
    stop_skew_deg: float = _D["stop_skew_deg"]  # ... or a minor arm is this far off 90 deg [deg]
    stop_share: float = _D["stop_share"]  # ... or at this random share of priority junctions
    right_before_left: bool = _D["right_before_left"]  # street-only junctions without signs
    priorities: bool = _D["priorities"]  # write <priority> records (else only the sidecar has them)

    def __post_init__(self) -> None:
        if min(self.signal_density, self.stop_density, self.stop_speed) < 0:
            raise ValueError("junction_control densities and stop_speed must be >= 0")
        if not 0 <= self.stop_share <= 1 or not 0 <= self.stop_skew_deg <= 90:
            raise ValueError("junction_control needs 0 <= stop_share <= 1 and 0 <= stop_skew_deg <= 90")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> JunctionControlParams:
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    "default": {},
    "dense": {"signal_density": 4000.0, "stop_share": 0.05},  # more lights, fewer stops
    "rural": {"right_before_left": False, "stop_share": 0.3, "stop_speed": 13.9},  # signed priority everywhere
}
JunctionControlParams.PRESETS = PRESETS  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Approach:
    """What the control rules need of a road end at a junction."""

    key: EndKey
    edge_id: int
    road_class: str
    lanes: int  # driving lanes of the road (both ways)
    speed: float  # [m/s]
    heading_in: float  # travel heading of arriving traffic [rad]

    @property
    def rank(self) -> tuple[int, int, float]:
        return RANK[self.road_class], self.lanes, self.speed


@dataclass(frozen=True)
class Control:
    kind: str  # none | right_before_left | priority | roundabout | signal
    roles: dict[EndKey, str] = field(default_factory=dict)  # major | yield | stop | signal | right_before_left | none
    major: tuple[EndKey, ...] = ()  # the priority road's arms

    def role(self, key: EndKey) -> str:
        return self.roles.get(key, "none")


def junction_control(node_id: int, approaches: list[Approach], graph: RoadGraph,
                     params: JunctionControlParams, seed: int) -> Control:
    """The control of one junction (see the module docstring for the rules)."""
    node = graph.nodes[node_id]
    keys = [a.key for a in approaches]
    if len(approaches) < 3 or node.kind == "ramp":
        return Control("none", {k: "none" for k in keys})
    ring = _roundabout_ring(graph, node_id)
    if ring is not None:
        major = tuple(a.key for a in approaches if a.edge_id in ring)
        return Control("roundabout", {k: "major" if k in major else "yield" for k in keys}, major)
    terminal = _diamond_ramps(graph, node_id)
    if terminal is not None:
        major = tuple(a.key for a in approaches if a.edge_id not in terminal)
        if any(a.rank[0] >= RANK["avenue"] for a in approaches if a.key in major):
            return Control("signal", {k: "signal" for k in keys}, major)
        return Control("priority", {k: "major" if k in major else "stop" for k in keys}, major)
    major = _priority_road(approaches)
    minors = [a for a in approaches if a.key not in major]
    if node.control is not None:
        if node.control not in CONTROL_KINDS:
            raise ValueError(f"node {node_id}: unknown control {node.control!r}; available: {CONTROL_KINDS}")
        return _as_set(node.control, keys, major)
    best_minor = max((a.rank[0] for a in minors), default=0)
    if best_minor >= RANK["avenue"] or (len(approaches) >= 5 and any(a.rank[0] >= RANK["avenue"]
                                                                     for a in approaches)):
        return Control("signal", {k: "signal" for k in keys}, major)
    if params.right_before_left and all(a.road_class == "street" for a in approaches):
        return Control("right_before_left", {k: "right_before_left" for k in keys})
    by_key = {a.key: a for a in approaches}
    major_speed = max(by_key[k].speed for k in major)
    axis = by_key[major[0]].heading_in
    skewed = any(abs(abs(wrap_angle(a.heading_in - axis)) - math.pi / 2) > math.radians(params.stop_skew_deg)
                 for a in minors)
    rng = np.random.default_rng([seed, SALT, node_id])
    stop = (major_speed >= params.stop_speed - 1e-9 or skewed or len(approaches) >= 5
            or rng.random() < params.stop_share)
    minor_role = "stop" if stop else "yield"
    return Control("priority", {k: "major" if k in major else minor_role for k in keys}, major)


def _as_set(kind: str, keys: list[EndKey], major: tuple[EndKey, ...]) -> Control:
    if kind in ("none", "right_before_left"):
        return Control(kind, {k: kind for k in keys})
    if kind == "signal":
        return Control("signal", {k: "signal" for k in keys}, major)
    return Control("priority", {k: "major" if k in major else kind for k in keys}, major)


def _priority_road(approaches: list[Approach]) -> tuple[EndKey, ...]:
    """The two arms of the priority road: the highest-ranked arm, and the highest-ranked
    other arm, the most nearly opposite one among equals."""
    first = max(approaches, key=lambda a: (a.rank, -a.key[0]))
    others = [a for a in approaches if a.key != first.key]
    second = max(others, key=lambda a: (a.rank, abs(wrap_angle(a.heading_in - first.heading_in)), -a.key[0]))
    return first.key, second.key


def _roundabout_ring(graph: RoadGraph, node_id: int) -> Optional[set[int]]:
    """Ring edges at node_id if it is a roundabout entry, else None."""
    for r in graph.metadata.get("roundabouts", []):
        if node_id in r.get("entries", []):
            ring = set(r["ring"])
            return {e.id for e in graph.incident_edges(node_id) if e.from_node in ring and e.to_node in ring}
    return None


def _diamond_ramps(graph: RoadGraph, node_id: int) -> Optional[set[int]]:
    """Ramp edges at node_id if it is a diamond interchange terminal, else None."""
    for ic in graph.metadata.get("interchanges", []):
        if node_id in ic.get("terminals", []):
            ramps = set(ic.get("ramps", []))
            return {e.id for e in graph.incident_edges(node_id) if e.id in ramps}
    return None


def approach_from(end, graph: RoadGraph) -> Approach:
    """The Approach of a builder RoadEnd (roadgen.opendrive.junctions)."""
    edge = graph.edges[end.edge_id]
    return Approach((end.road_id, end.contact), end.edge_id, edge.road_class, edge.total_lanes, edge.speed_limit,
                    end.heading_in)


# --- right of way ------------------------------------------------------------------


def conflicts(movements, paths: dict[int, np.ndarray]) -> list[tuple[int, int]]:
    """Pairs (a, b), a < b, of connecting roads of movements from different approaches
    whose paths cross or end in the same lane; paths: connecting road -> (n, 2) points."""
    lines = {m.connecting_road: LineString(paths[m.connecting_road]) for m in movements}
    out = []
    ms = sorted(movements, key=lambda m: m.connecting_road)
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            if a.from_road == b.from_road and _same_side(a, b):  # one approach
                continue
            same_exit = (a.to_road, a.to_lane) == (b.to_road, b.to_lane)
            if same_exit or lines[a.connecting_road].intersects(lines[b.connecting_road]):
                out.append((a.connecting_road, b.connecting_road))
    return out


def _same_side(a, b) -> bool:
    """Both movements arrive on the same side of the same road (one approach)."""
    return (a.from_lane < 0) == (b.from_lane < 0)


def priority_pairs(movements, pairs: list[tuple[int, int]], control: Control,
                   approach_of: dict[int, Approach]) -> list[tuple[int, int]]:
    """(high, low) connecting roads of each conflicting pair where one has priority;
    approach_of: connecting road -> its movement's approach."""
    by_road = {m.connecting_road: m for m in movements}
    out = []
    for a, b in pairs:
        ma, mb = by_road[a], by_road[b]
        pa, pb = approach_of[a], approach_of[b]
        ra, rb = control.role(pa.key), control.role(pb.key)
        if control.kind == "none":
            continue
        if control.kind == "signal":
            winner = _left_turn_rule(ma, mb, pa, pb)
        elif (ra == "major") != (rb == "major"):
            winner = a if ra == "major" else b
        else:
            winner = _left_turn_rule(ma, mb, pa, pb)
            if winner is None and control.kind != "signal":
                winner = _from_right(a, b, pa, pb)
        if winner is not None:
            out.append((winner, b if winner == a else a))
    return out


def _oncoming(pa: Approach, pb: Approach) -> bool:
    return abs(abs(wrap_angle(pa.heading_in - pb.heading_in)) - math.pi) < OPPOSITE_TOL


def _left_turn_rule(ma, mb, pa: Approach, pb: Approach) -> Optional[int]:
    """Between oncoming approaches, a straight or right turn has priority over a left turn
    (or a U-turn)."""
    if not _oncoming(pa, pb):
        return None
    turning = {"left", "uturn"}
    if ma.kind in turning and mb.kind not in turning:
        return mb.connecting_road
    if mb.kind in turning and ma.kind not in turning:
        return ma.connecting_road
    return None


def _from_right(a: int, b: int, pa: Approach, pb: Approach) -> Optional[int]:
    """Right before left: the movement whose approach comes from the other's right."""
    d = wrap_angle(pb.heading_in - pa.heading_in)  # b arrives turned counter-clockwise: from a's right
    if abs(d) < 1e-6 or _oncoming(pa, pb):
        return None
    return b if d > 0 else a


def yields_to(pairs: list[tuple[int, int]]) -> dict[int, list[int]]:
    """Connecting road -> the connecting roads it yields to, from (high, low) pairs."""
    out: dict[int, list[int]] = {}
    for high, low in pairs:
        out.setdefault(low, []).append(high)
    return {k: sorted(v) for k, v in out.items()}


# --- priority roads through several junctions -----------------------------------------

Step = tuple[int, int]  # (road, direction): +1 along s, -1 against it


def priority_runs(majors: dict[int, tuple[EndKey, EndKey]], road_ends: dict[EndKey, int],
                  directions: dict[int, set[int]]) -> list[tuple[list[Step], list[int]]]:
    """Runs of a priority road through consecutive priority junctions, per direction of
    travel: (roads [(road, direction)], junctions between them), with roads[k] arriving at
    junctions[k] and roads[k + 1] leaving it. majors: junction -> its priority road's two
    arms; road_ends: (road, contact) -> the junction there; directions: road -> the
    directions it has traffic in. A run that closes into a loop is cut where it closes,
    starting from its lowest (road, direction). Runs through one junction only are left out."""
    def arrive(step: Step) -> EndKey:
        return step[0], "end" if step[1] > 0 else "start"

    succ: dict[Step, tuple[int, Step]] = {}
    for jid, (a, b) in majors.items():
        for here, there in ((a, b), (b, a)):
            road, contact = here
            step = (road, 1 if contact == "end" else -1)  # arriving at junction jid on this arm
            nxt = (there[0], 1 if there[1] == "start" else -1)  # leaving on the other arm
            if road_ends.get(here) == jid and step[1] in directions.get(road, set()) \
                    and nxt[1] in directions.get(nxt[0], set()):
                succ[step] = (jid, nxt)
    has_pred = {nxt for _, nxt in succ.values()}
    starts = sorted(s for s in succ if s not in has_pred)
    seen: set[Step] = set()
    loops = [s for s in sorted(succ) if s in has_pred]
    runs = []
    for start in starts + loops:
        if start in seen:
            continue
        roads, junctions = [start], []
        step = start
        seen.add(step)
        while step in succ:
            jid, nxt = succ[step]
            if nxt in seen:  # a loop: cut where it closes
                break
            junctions.append(jid)
            roads.append(nxt)
            seen.add(nxt)
            step = nxt
        if len(junctions) >= 2:
            runs.append((roads, junctions))
    return runs
