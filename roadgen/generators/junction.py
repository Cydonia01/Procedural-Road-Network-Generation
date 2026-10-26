"""One junction per seed (stage C6), after Muktadir et al., "JunctionArt" (2023).

For long-tail datasets: a city has almost only 3- and 4-leg junctions near
90 deg; this generator makes any shape on demand. Legs leave a centre node
at angles spread evenly (or given by angles_deg), each turned by a normal
skew of skew_deg, at least min_angle_deg apart. Every leg draws its lanes
per side from lane_probs (JunctionArt's P(0, 1, 2, 3 lanes) = 0.15, 0.6,
0.2, 0.05), never 0 on both sides; legs with 4 or more lanes are avenues.
With pair, two T junctions pair_offset apart on a through road form an
offset crossing (stems on opposite sides).

A drawn junction must be buildable: its arms far enough apart for the
junction trims (roadgen.geometry.junction_space) and every leg longer than
its trim plus a road; otherwise it is drawn again on the next random
sub-stream (up to max_tries), so the result is deterministic for a seed.
Leg ends get cul-de-sac bulbs where they are two-way (culdesacs), so traffic
can turn around and scenarios have no traps. With roundabout the junction
becomes a roundabout (roadgen.generators.transforms.roundabout), where it fits.

With interchange "diamond" or "cloverleaf" the generator makes a highway
interchange instead (stage C9): a highway crossing an avenue (diamond) or
another highway (cloverleaf) at right angles, interchange_length each way,
built with roadgen.generators.transforms.interchange; heights come from
grade separation in the pipeline.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import RoadNetworkGenerator, assign_node_kinds, register
from roadgen.generators.repair import MIN_ROAD_LENGTH
from roadgen.generators.transforms.culdesac import add_culdesacs
from roadgen.generators.transforms.interchange import add_cloverleaf, add_diamond
from roadgen.generators.transforms.roundabout import add_roundabouts
from roadgen.geometry.junction_space import node_trims
from roadgen.graph.model import Edge, Node, RoadGraph

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "generators", "junction")
LEG_COUNTS = (3, 4, 5, 6, 7)
INTERCHANGES = ("none", "diamond", "cloverleaf")
HIGHWAY_SPEED = 22.2  # [m/s] (80 km/h) of an interchange's highways
AVENUE_LANES = 4  # legs with at least this many lanes in all are avenues
SALT_CULDESAC = 0x6375


@dataclass(frozen=True)
class JunctionLayoutParams:
    legs: int = _D["legs"]  # number of legs; 0 = drawn by leg_weights
    leg_weights: tuple[float, ...] = _D["leg_weights"]  # P(3, 4, 5, 6, 7 legs)
    angles_deg: tuple[float, ...] = _D["angles_deg"]  # leg headings [deg]; empty = evenly spread
    skew_deg: float = _D["skew_deg"]  # std dev of each leg's turn from its heading [deg]
    min_angle_deg: float = _D["min_angle_deg"]  # least angle between neighbouring legs [deg]
    leg_length: float = _D["leg_length"]  # [m]
    lane_probs: tuple[float, ...] = _D["lane_probs"]  # P(0, 1, 2, 3 lanes) on each side of a leg
    lane_width: float = _D["lane_width"]  # [m]
    street_speed: float = _D["street_speed"]  # [m/s]
    avenue_speed: float = _D["avenue_speed"]  # [m/s]
    pair: bool = _D["pair"]  # two T junctions on a through road, stems on opposite sides
    pair_offset: float = _D["pair_offset"]  # [m] between the two T junctions
    culdesacs: bool = _D["culdesacs"]  # turn-around bulbs at two-way leg ends
    culdesac_radius: float = _D["culdesac_radius"]  # [m]
    culdesac_speed: float = _D["culdesac_speed"]  # [m/s]
    max_tries: int = _D["max_tries"]
    roundabout: bool = _D["roundabout"]  # build the junction as a roundabout, where it fits
    roundabout_radius: float = _D["roundabout_radius"]  # [m] single-lane ring
    roundabout_radius_avenue: float = _D["roundabout_radius_avenue"]  # [m] two-lane ring
    roundabout_speed: float = _D["roundabout_speed"]  # [m/s]
    interchange: str = _D["interchange"]  # none | diamond | cloverleaf: a highway interchange instead
    interchange_length: float = _D["interchange_length"]  # [m] of each road from the crossing

    def __post_init__(self) -> None:
        if self.interchange not in INTERCHANGES:
            raise ValueError(f"unknown interchange {self.interchange!r}; available: {INTERCHANGES}")
        if self.interchange_length < 800.0:
            raise ValueError("interchange_length must be at least 800 m (the templates' reach)")
        if self.legs and self.legs not in LEG_COUNTS:
            raise ValueError(f"legs must be 0 (drawn) or one of {LEG_COUNTS}")
        if len(self.leg_weights) != len(LEG_COUNTS) or min(self.leg_weights) < 0 or not sum(self.leg_weights):
            raise ValueError(f"leg_weights needs {len(LEG_COUNTS)} weights >= 0, not all 0")
        if self.angles_deg and not 3 <= len(self.angles_deg) <= 7:
            raise ValueError("angles_deg needs 3 to 7 headings")
        if len(self.lane_probs) != 4 or min(self.lane_probs) < 0 or not sum(self.lane_probs[1:]):
            raise ValueError("lane_probs needs P(0, 1, 2, 3 lanes) >= 0 with some lanes")
        if min(self.skew_deg, self.min_angle_deg) < 0 or self.min_angle_deg >= 120:
            raise ValueError("need skew_deg >= 0 and min_angle_deg in [0, 120)")
        if min(self.leg_length, self.lane_width, self.street_speed, self.avenue_speed, self.pair_offset,
               self.culdesac_radius, self.culdesac_speed) <= 0 or self.max_tries < 1:
            raise ValueError("lengths, widths, speeds and max_tries must be positive")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "JunctionLayoutParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    "t": {"angles_deg": (0.0, 90.0, 180.0), "skew_deg": 0.0},
    "y": {"angles_deg": (90.0, 210.0, 330.0), "skew_deg": 0.0},
    "skewed_cross": {"angles_deg": (0.0, 60.0, 180.0, 240.0), "skew_deg": 0.0},
    # A through road with two diagonals leaving one side.
    "k": {"angles_deg": (90.0, 270.0, 30.0, 330.0), "skew_deg": 0.0},
    "five_way": {"legs": 5, "skew_deg": 8.0},
    "six_way": {"legs": 6, "skew_deg": 5.0},
    "offset_t": {"pair": True, "skew_deg": 10.0},
    "roundabout": {"legs": 4, "skew_deg": 10.0, "min_angle_deg": 55.0, "roundabout": True},
    "roundabout_5": {"legs": 5, "skew_deg": 5.0, "min_angle_deg": 55.0, "roundabout": True},
    "diamond": {"interchange": "diamond"},
    "cloverleaf": {"interchange": "cloverleaf"},
    "random": {},
}
JunctionLayoutParams.PRESETS = PRESETS  # type: ignore[attr-defined]


@register("junction")
class JunctionGenerator(RoadNetworkGenerator[JunctionLayoutParams]):
    params_type = JunctionLayoutParams
    description = "one junction per seed (3-7 legs, skew, offset, lanes), JunctionArt-style"

    def generate(self, params: JunctionLayoutParams, seed: int) -> RoadGraph:
        p = params
        if p.interchange != "none":
            return _interchange(p, seed)
        for attempt in range(p.max_tries):
            rng = np.random.default_rng([seed, attempt])
            graph = _pair(p, rng) if p.pair else _star(p, rng)
            if _buildable(graph, p):
                break
        else:
            raise ValueError(f"no buildable junction in {p.max_tries} tries; loosen min_angle_deg, "
                             "skew_deg or leg_length")
        graph.metadata = {
            "name": f"junction_{len(graph.incident_edges(1)) if not p.pair else 'pair'}_s{seed}",
            "generator": self.name, "seed": seed, "params": asdict(p), "attempts": attempt + 1,
        }
        if p.roundabout:
            add_roundabouts(graph, np.random.default_rng(seed), lambda node: 1.0, p.roundabout_radius,
                            p.roundabout_radius_avenue, p.roundabout_speed)
        if p.culdesacs:
            ends = {n for n in graph.nodes if graph.degree(n) == 1}

            def two_way(node: Node) -> float:
                (e,) = graph.incident_edges(node.id)
                return 1.0 if node.id in ends and not e.is_one_way else 0.0

            add_culdesacs(graph, np.random.default_rng([seed, SALT_CULDESAC]), two_way, p.culdesac_radius,
                          p.culdesac_speed, road_classes=("street", "avenue"))
        assign_node_kinds(graph)
        log.debug("junction: %s after %d tries", graph.metadata["name"], attempt + 1)
        return graph


def _interchange(p: JunctionLayoutParams, seed: int) -> RoadGraph:
    """A highway along x crossing an avenue (diamond) or a highway (cloverleaf) along y."""
    graph = RoadGraph(metadata={"name": f"junction_{p.interchange}_s{seed}", "generator": "junction",
                                "seed": seed, "params": asdict(p)})
    n = p.interchange_length
    for nid, (x, y) in {1: (-n, 0.0), 2: (-1.0, 0.0), 3: (n, 0.0), 4: (0.0, -n), 5: (0.0, 1.0), 6: (0.0, n)}.items():
        graph.add_node(Node(nid, x, y))
    graph.add_edge(Edge(1, 1, 2, "highway", 2, 2, lane_width=p.lane_width, speed_limit=HIGHWAY_SPEED))
    graph.add_edge(Edge(2, 2, 3, "highway", 2, 2, lane_width=p.lane_width, speed_limit=HIGHWAY_SPEED))
    cross = ("highway", HIGHWAY_SPEED) if p.interchange == "cloverleaf" else ("avenue", p.avenue_speed)
    graph.add_edge(Edge(3, 4, 5, cross[0], 2, 2, lane_width=p.lane_width, speed_limit=cross[1]))
    graph.add_edge(Edge(4, 5, 6, cross[0], 2, 2, lane_width=p.lane_width, speed_limit=cross[1]))
    # Edges 2 and 3 cross at the origin without a node.
    built = (add_diamond if p.interchange == "diamond" else add_cloverleaf)(graph, 2, 3, (0.0, 0.0))
    if not built:
        raise ValueError(f"the {p.interchange} template does not fit; lengthen interchange_length")
    assign_node_kinds(graph)
    return graph


def _headings(p: JunctionLayoutParams, rng: np.random.Generator) -> list[float]:
    if p.angles_deg:
        base = list(p.angles_deg)
    else:
        n = p.legs or int(rng.choice(LEG_COUNTS, p=np.asarray(p.leg_weights) / sum(p.leg_weights)))
        start = rng.uniform(0.0, 360.0)
        base = [start + 360.0 * k / n for k in range(n)]
    return [math.radians(a + (rng.normal(0.0, p.skew_deg) if p.skew_deg else 0.0)) for a in base]


def _lanes(p: JunctionLayoutParams, rng: np.random.Generator) -> tuple[int, int]:
    probs = np.asarray(p.lane_probs) / sum(p.lane_probs)
    while True:
        fwd, bwd = (int(rng.choice(4, p=probs)) for _ in range(2))
        if fwd + bwd:
            return fwd, bwd


def _leg(graph: RoadGraph, centre: int, heading: float, lanes: tuple[int, int],
         p: JunctionLayoutParams) -> None:
    """A leg from centre outward; its forward lanes run towards the junction."""
    c = graph.nodes[centre]
    nid, eid = max(graph.nodes) + 1, max(graph.edges, default=0) + 1
    graph.add_node(Node(nid, c.x + p.leg_length * math.cos(heading), c.y + p.leg_length * math.sin(heading)))
    avenue = sum(lanes) >= AVENUE_LANES
    graph.add_edge(Edge(eid, nid, centre, "avenue" if avenue else "street", *lanes,
                        lane_width=p.lane_width, speed_limit=p.avenue_speed if avenue else p.street_speed))


def _star(p: JunctionLayoutParams, rng: np.random.Generator) -> RoadGraph:
    graph = RoadGraph()
    graph.add_node(Node(1, 0.0, 0.0))
    for h in _headings(p, rng):
        _leg(graph, 1, h, _lanes(p, rng), p)
    return graph


def _pair(p: JunctionLayoutParams, rng: np.random.Generator) -> RoadGraph:
    """Two T junctions on an east-west through road, stems north of the west one and
    south of the east one."""
    graph = RoadGraph()
    graph.add_node(Node(1, 0.0, 0.0))
    graph.add_node(Node(2, p.pair_offset, 0.0))
    through = _lanes(p, rng)
    avenue = sum(through) >= AVENUE_LANES
    graph.add_edge(Edge(1, 1, 2, "avenue" if avenue else "street", *through, lane_width=p.lane_width,
                        speed_limit=p.avenue_speed if avenue else p.street_speed))
    skew = [math.radians(rng.normal(0.0, p.skew_deg)) if p.skew_deg else 0.0 for _ in range(2)]
    _leg(graph, 1, math.pi, (through[1], through[0]), p)  # forward lanes towards node 1 = eastbound
    _leg(graph, 1, math.pi / 2 + skew[0], _lanes(p, rng), p)
    _leg(graph, 2, 0.0, through, p)
    _leg(graph, 2, -math.pi / 2 + skew[1], _lanes(p, rng), p)
    return graph


def _buildable(graph: RoadGraph, p: JunctionLayoutParams) -> bool:
    """Arms min_angle_deg apart, and every road longer than its trims plus MIN_ROAD_LENGTH."""
    trims: dict[int, float] = {}
    for n in graph.nodes:
        if graph.degree(n) < 2:
            continue
        node = graph.nodes[n]
        heads = sorted(math.atan2(graph.nodes[o].y - node.y, graph.nodes[o].x - node.x) % (2 * math.pi)
                       for o in graph.neighbors(n))
        gaps = [(heads[(i + 1) % len(heads)] - h) % (2 * math.pi) for i, h in enumerate(heads)]
        if min(gaps) < math.radians(p.min_angle_deg):
            return False
        try:
            for eid, t in node_trims(graph, n).items():
                trims[eid] = trims.get(eid, 0.0) + t
        except ValueError:
            return False
    return all(graph.edge_length(eid) - t >= MIN_ROAD_LENGTH for eid, t in trims.items())
