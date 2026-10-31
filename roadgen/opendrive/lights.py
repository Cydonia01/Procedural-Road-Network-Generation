"""Traffic lights: phase groups and fixed-time signal plans (stage B6).

At a junction with lights (control kind "signal", roadgen.opendrive.control)
every approach with incoming lanes gets a light (furniture). Its plan:

- Protected lefts (B8): where approaches of a group have left-turn pockets,
  their pocket lights get a phase of their own before the group's through
  phase (both with yellow and all-red), so left turns never run against
  oncoming traffic; the pockets' lanes weigh their green.
- Groups: approaches arriving from roughly opposite directions (heading
  difference within 180 +- OPPOSITE_TOL) share a phase, if their movements
  are compatible (every conflict between them is a permissive left turn;
  at skewed and Y-shaped junctions two left turns or two straight movements
  of "opposite" arms can cross); the highest-ranked approaches pair first,
  leftover arms get a phase of their own. Left turns
  are permissive: they yield to the oncoming traffic of their own phase (the
  junction's <priority> records), until turn pockets give them their own
  phase (B8).
- Each phase: green, then yellow (ITE: reaction_time + v / (2 deceleration),
  at least min_yellow, v the group's fastest approach), then all_red with
  every light red.
- Cycle: CYCLE_BASE for two phases plus CYCLE_STEP per further phase,
  within cycle; the green time left after yellow and all-red is split by
  the groups' incoming lane counts (to 0.1 s, the largest group taking the
  rounding rest), each at least min_green (which may stretch the cycle past
  its upper bound).
- Pedestrian phase (B7): a junction whose approaches all fit in one group
  but that has crosswalks gets a second phase with every vehicle light red,
  long enough to cross the longest crosswalk at walk_speed (at least
  min_green), so pedestrians get their turn. (Without crosswalks such a
  junction is demoted to priority control by the builder.)
- Offset: seeded per junction (np.random.default_rng([seed, SALT, node])),
  a whole second in [0, cycle), so neighbouring junctions do not switch in
  step.

States are esmini's light strings red / yellow / green, one lamp each.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.geometry.planar import wrap_angle
from roadgen.opendrive.control import Approach, EndKey

_D = section(DEFAULTS, "traffic_lights")

RED, YELLOW, GREEN = "on;off;off", "off;on;off", "off;off;on"
OPPOSITE_TOL = math.radians(30.0)  # approaches this close to opposite share a phase
CYCLE_BASE, CYCLE_STEP = 60.0, 15.0  # cycle of a two-phase plan, and per further phase [s]
SALT = 6  # random stream of the offsets


@dataclass(frozen=True)
class TrafficLightParams:
    min_green: float = _D["min_green"]  # [s]
    min_yellow: float = _D["min_yellow"]  # [s]
    reaction_time: float = _D["reaction_time"]  # yellow = reaction_time + v / (2 deceleration) [s]
    deceleration: float = _D["deceleration"]  # [m/s^2]
    all_red: float = _D["all_red"]  # [s]
    cycle: tuple[float, float] = _D["cycle"]  # shortest and longest cycle [s]
    overhead_min_lanes: int = _D["overhead_min_lanes"]  # approaches this wide get a light over the lanes too
    walk_speed: float = _D["walk_speed"]  # pedestrians crossing in a pedestrian phase [m/s]

    def __post_init__(self) -> None:
        if min(self.min_green, self.min_yellow, self.reaction_time, self.deceleration) <= 0 or self.all_red < 0:
            raise ValueError("traffic_lights needs min_green, min_yellow, reaction_time, deceleration > 0 "
                             "and all_red >= 0")
        if len(self.cycle) != 2 or not 0 < self.cycle[0] <= self.cycle[1]:
            raise ValueError("traffic_lights.cycle needs 0 < shortest <= longest")
        if self.overhead_min_lanes < 1:
            raise ValueError("traffic_lights.overhead_min_lanes must be >= 1")
        if self.walk_speed <= 0:
            raise ValueError("traffic_lights.walk_speed must be positive")

    def yellow(self, speed: float) -> float:
        """Yellow time [s] for an approach speed [m/s], to 0.1 s."""
        return round(max(self.min_yellow, self.reaction_time + speed / (2 * self.deceleration)), 1)


@dataclass(frozen=True)
class SignalPlan:
    junction: int
    cycle: float  # [s]
    offset: float  # [s] into the cycle at time 0
    groups: tuple[tuple[EndKey, ...], ...]
    phases: tuple[dict[str, Any], ...]  # {"group", "kind" (green, yellow, all_red), "duration", "states"}


def phase_groups(approaches: list[Approach], clash: Optional[set[frozenset]] = None) -> list[tuple[EndKey, ...]]:
    """Approaches by phase: roughly opposite pairs, highest-ranked first; the rest alone.
    clash: pairs of approach keys whose movements conflict other than permissively, which
    never share a phase."""
    clash = clash or set()
    left = sorted(approaches, key=lambda a: (a.rank, -a.key[0]), reverse=True)
    groups = []
    while left:
        a = left.pop(0)
        opposite = [b for b in left if abs(abs(wrap_angle(b.heading_in - a.heading_in)) - math.pi) <= OPPOSITE_TOL
                    and frozenset((a.key, b.key)) not in clash]
        if opposite:
            b = min(opposite, key=lambda b: (abs(abs(wrap_angle(b.heading_in - a.heading_in)) - math.pi), b.key))
            left.remove(b)
            groups.append(tuple(sorted((a.key, b.key))))
        else:
            groups.append((a.key,))
    return groups


def signal_plan(junction_id: int, node_id: int, approaches: list[Approach], lanes: dict[EndKey, int],
                lights: dict[EndKey, list[str]], params: TrafficLightParams, seed: int,
                clash: Optional[set[frozenset]] = None, crossing: float = 0.0,
                left_lights: frozenset[str] = frozenset()) -> SignalPlan:
    """The fixed-time plan of one junction; lanes: incoming lanes per approach, lights:
    light signal ids per approach, clash: see phase_groups; crossing: the longest
    crosswalk [m], for a pedestrian phase where all approaches share one group (0: none);
    left_lights: the lights of left-turn pockets, which get protected phases."""
    by_key = {a.key: a for a in approaches}
    groups = phase_groups([a for a in approaches if a.key in lights], clash)
    # Phases: per group its protected left phase (pocket lights) if any, then the rest.
    stages = []  # (group index, turn, lights, weight)
    for k, g in enumerate(groups):
        mine = [s for key in g for s in lights[key]]
        left = [s for s in mine if s in left_lights]
        if left:
            stages.append((k, "left", left, len(left)))
        rest = [s for s in mine if s not in left_lights]
        if rest:
            stages.append((k, "through", rest, sum(lanes.get(key, 1) for key in g) - len(left)))
    n = len(stages)
    yellows = [params.yellow(max(by_key[key].speed for key in groups[k])) for k, *_ in stages]
    lo, hi = params.cycle
    cycle = min(max(CYCLE_BASE + CYCLE_STEP * max(n - 2, 0), lo), hi)
    pedestrian = 0.0
    if len(groups) == 1 and n == 1 and crossing > 0:
        pedestrian = max(params.min_green, float(math.ceil(crossing / params.walk_speed)))
    lost = sum(yellows) + n * params.all_red + pedestrian
    weights = [max(w, 1) for *_, w in stages]
    available = max(cycle - lost, 0.0)
    greens = [max(params.min_green, round(available * w / sum(weights), 1)) for w in weights]
    biggest = weights.index(max(weights))  # takes what rounding left over, so the cycle holds
    greens[biggest] = max(params.min_green, round(greens[biggest] + available - sum(greens), 1))
    cycle = round(sum(greens) + lost, 1)
    every = sorted(s for ids in lights.values() for s in ids)
    phases = []
    for j, (k, turn, mine, _) in enumerate(stages):
        for kind, duration, lamp in (("green", greens[j], GREEN), ("yellow", yellows[j], YELLOW),
                                     ("all_red", params.all_red, RED)):
            if duration <= 0:
                continue
            phases.append({"group": k, "turn": turn, "kind": kind, "duration": float(duration),
                           "states": {s: (lamp if s in mine else RED) for s in every}})
    if pedestrian:
        phases.append({"group": len(groups), "turn": "", "kind": "pedestrian", "duration": pedestrian,
                       "states": {s: RED for s in every}})
    rng = np.random.default_rng([seed, SALT, node_id])
    offset = float(np.floor(rng.random() * cycle))
    return SignalPlan(junction_id, cycle, offset, tuple(groups), tuple(phases))


def state_changes(plan: SignalPlan, duration: float) -> list[tuple[float, dict[str, str]]]:
    """(time [s], {signal id: state}) wherever lights change within [0, duration], the
    first at 0 with every light; the cycle starts at -offset."""
    starts, t = [], 0.0
    for p in plan.phases:
        starts.append(t)
        t += p["duration"]
    # the phase in force at time 0
    pos = plan.offset % plan.cycle
    k0 = max(i for i, s in enumerate(starts) if s <= pos + 1e-9)
    out = [(0.0, dict(plan.phases[k0]["states"]))]
    current = dict(plan.phases[k0]["states"])
    cycle_start = -pos
    k = k0
    while True:
        k += 1
        if k == len(plan.phases):
            k = 0
            cycle_start += plan.cycle
        at = round(cycle_start + starts[k], 3)
        if at > duration:
            return out
        changed = {s: st for s, st in plan.phases[k]["states"].items() if current[s] != st}
        current.update(changed)
        if changed:
            out.append((at, changed))
