"""Network metrics of a RoadGraph: how varied its layout and junctions are.

Works on the graph alone (any generator), so stage C's diversity can be
measured and compared with real-city statistics (stage D):

- junction legs: how many nodes have 1 (dead ends), 3, 4, 5, 6 or 7+ roads;
  nodes with two roads are bends or lane-count changes and counted apart;
- skew per junction: the largest angle between two neighbouring arms away
  from a right angle or a straight line (0 for T and + shapes, 30 deg for a
  Y with three equal angles, 18 deg for a regular five-way star);
- offset T pairs: two T junctions joined by a short edge, their stems on
  opposite sides of it (staggered crossroads);
- connected-node ratio CNR = intersections / (intersections + dead ends)
  (Dill 2004; Teng & Bidarra 2017 sec. 4.2), intersections and dead ends per
  km^2 and street density [km/km^2] over the convex hull of the nodes
  (Yang et al. 2013 sec. 7);
- orientation entropy of the length-weighted street bearings in 36 bins,
  both directions counted, and Boeing's orientation order
  phi = 1 - ((H - H_grid) / (H_max - H_grid))^2: 1 for a perfect grid,
  0 for uniformly spread bearings (Boeing 2019; TownSim sec. 4.2);
- tortuosity (length / chord) of every road through its bends, and the
  share of road length that is not straight;
- block areas: the bounded faces of the street lines (shapely polygonize);
- counts of stage C features listed in the graph metadata.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
from shapely.geometry import LineString, MultiPoint
from shapely.ops import polygonize, unary_union

from roadgen.config import DEFAULTS, section
from roadgen.geometry.chains import find_chains
from roadgen.graph.model import RoadGraph

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "network_metrics")
LEG_BINS = ("1", "3", "4", "5", "6", "7+")
FEATURES = ("roundabouts", "culdesacs", "overpasses", "tunnels", "interchanges", "bridges")
PERCENTILES = (10, 50, 90)


@dataclass(frozen=True)
class NetworkMetricsParams:
    orientation_bins: int = _D["orientation_bins"]  # bearing bins over 360 deg
    skew_threshold_deg: float = _D["skew_threshold_deg"]  # a junction is skewed above this [deg]
    offset_t_distance: float = _D["offset_t_distance"]  # max edge length of an offset T pair [m]
    straight_tortuosity: float = _D["straight_tortuosity"]  # roads below this count as straight

    def __post_init__(self) -> None:
        if self.orientation_bins < 4 or self.orientation_bins % 4:
            raise ValueError("orientation_bins must be a positive multiple of 4")
        if self.skew_threshold_deg < 0 or self.offset_t_distance <= 0 or self.straight_tortuosity < 1:
            raise ValueError("need skew_threshold_deg >= 0, offset_t_distance > 0, "
                             "straight_tortuosity >= 1")


def arm_headings(graph: RoadGraph, node_id: int) -> list[tuple[float, int]]:
    """(outward heading [rad] in [0, 2 pi), edge id) of every road at a node, sorted by heading."""
    n = graph.nodes[node_id]
    arms = []
    for e in graph.incident_edges(node_id):
        o = graph.nodes[e.to_node if e.from_node == node_id else e.from_node]
        arms.append((math.atan2(o.y - n.y, o.x - n.x) % (2 * math.pi), e.id))
    return sorted(arms)


def _gaps(headings: list[float]) -> list[float]:
    """Angles [rad] between neighbouring arms, counter-clockwise, summing to 2 pi."""
    return [((headings[(i + 1) % len(headings)] - h) % (2 * math.pi)) or 2 * math.pi
            for i, h in enumerate(headings)]


def junction_skew(graph: RoadGraph, node_id: int) -> float:
    """Largest distance [deg] of a gap between neighbouring arms from 90 or 180 deg."""
    gaps = _gaps([h for h, _ in arm_headings(graph, node_id)])
    return max(min(abs(math.degrees(g) - 90.0), abs(math.degrees(g) - 180.0)) for g in gaps)


def min_arm_angle(graph: RoadGraph, node_id: int) -> float:
    """Smallest angle [deg] between two neighbouring roads at a node."""
    return math.degrees(min(_gaps([h for h, _ in arm_headings(graph, node_id)])))


def _t_stem(graph: RoadGraph, node_id: int) -> tuple[int, float]:
    """(edge id, heading) of a T junction's stem: the arm opposite the gap closest to 180 deg."""
    arms = arm_headings(graph, node_id)
    gaps = _gaps([h for h, _ in arms])
    k = min(range(3), key=lambda i: abs(gaps[i] - math.pi))  # the through road spans arms k, k+1
    h, eid = arms[(k + 2) % 3]
    return eid, h


def offset_t_pairs(graph: RoadGraph, max_length: float) -> int:
    """Edges of at most max_length [m] joining two T junctions whose stems leave on
    opposite sides of the edge (staggered crossroads)."""
    count = 0
    for eid, e in graph.edges.items():
        a, b = e.from_node, e.to_node
        if graph.degree(a) != 3 or graph.degree(b) != 3 or graph.edge_length(eid) > max_length:
            continue
        (sa, ha), (sb, hb) = _t_stem(graph, a), _t_stem(graph, b)
        if eid in (sa, sb):
            continue  # the edge is a stem, not part of both through roads
        d = graph.edge_heading(eid)
        side_a = math.sin(ha - d)
        side_b = math.sin(hb - d)
        if side_a * side_b < 0:
            count += 1
    return count


def orientation(graph: RoadGraph, bins: int) -> tuple[float, float]:
    """(entropy [nats], orientation order phi) of length-weighted edge bearings, both directions."""
    weights = np.zeros(bins)
    width = 2 * math.pi / bins
    for eid in graph.edges:
        length = graph.edge_length(eid)
        h = graph.edge_heading(eid)
        for bearing in (h, h + math.pi):
            # Bins centred on the axes, so a grid along x / y fills exactly four.
            weights[int(((bearing + width / 2) % (2 * math.pi)) // width) % bins] += length
    total = weights.sum()
    if total <= 0:
        return 0.0, 0.0
    p = weights[weights > 0] / total
    entropy = float(-(p * np.log(p)).sum())
    h_max, h_grid = math.log(bins), math.log(4)
    phi = 1.0 - ((entropy - h_grid) / (h_max - h_grid)) ** 2
    return entropy, max(0.0, min(1.0, phi))


def tortuosities(graph: RoadGraph) -> list[tuple[float, float]]:
    """(tortuosity, length [m]) of every road through its bends; rings are left out."""
    out = []
    for chain in find_chains(graph):
        length = sum(graph.edge_length(eid) for eid in chain.edge_ids)
        a, b = graph.nodes[chain.start], graph.nodes[chain.end]
        chord = math.hypot(b.x - a.x, b.y - a.y)
        if chord > 1e-6:
            out.append((length / chord, length))
    return out


def block_areas(graph: RoadGraph) -> list[float]:
    """Areas [m^2] of the bounded faces enclosed by the street centre lines."""
    lines = [LineString([(graph.nodes[e.from_node].x, graph.nodes[e.from_node].y),
                         (graph.nodes[e.to_node].x, graph.nodes[e.to_node].y)])
             for e in graph.edges.values()]
    if len(lines) < 3:
        return []
    return sorted(p.area for p in polygonize(unary_union(lines)))


def _distribution(values: list[float]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(values)}
    if values:
        arr = np.asarray(values, dtype=float)
        out.update({f"p{p}": round(float(np.percentile(arr, p)), 4) for p in PERCENTILES})
        out["mean"] = round(float(arr.mean()), 4)
        out["max"] = round(float(arr.max()), 4)
    return out


def network_metrics(graph: RoadGraph, params: NetworkMetricsParams = NetworkMetricsParams()
                    ) -> dict[str, Any]:
    """All network metrics of a graph as a JSON-ready dict."""
    degrees = {n: graph.degree(n) for n in graph.nodes}
    legs = Counter(str(d) if d < 7 else "7+" for d in degrees.values() if d != 2 and d > 0)
    junctions = [n for n, d in degrees.items() if d >= 3]
    dead_ends = sum(1 for d in degrees.values() if d == 1)
    length_m = sum(graph.edge_length(e) for e in graph.edges)
    points = [(n.x, n.y) for n in graph.nodes.values()]
    area_km2 = MultiPoint(points).convex_hull.area / 1e6 if len(points) >= 3 else 0.0

    skews = [junction_skew(graph, n) for n in junctions]
    entropy, phi = orientation(graph, params.orientation_bins)
    roads = tortuosities(graph)
    roads_length = sum(length for _, length in roads)
    curved_length = sum(length for t, length in roads if t >= params.straight_tortuosity)
    blocks = block_areas(graph)
    meta = graph.metadata
    one_way = sum(graph.edge_length(e.id) for e in graph.edges.values() if e.is_one_way)
    by_class = Counter()
    for e in graph.edges.values():
        by_class[e.road_class] += graph.edge_length(e.id)

    def per_km2(n: int) -> float:
        return round(n / area_km2, 3) if area_km2 > 0 else 0.0

    return {
        "name": meta.get("name"),
        "params": {"orientation_bins": params.orientation_bins,
                   "skew_threshold_deg": params.skew_threshold_deg,
                   "offset_t_distance": params.offset_t_distance,
                   "straight_tortuosity": params.straight_tortuosity},
        "counts": {
            "nodes": len(graph.nodes), "edges": len(graph.edges),
            "junctions": len(junctions), "dead_ends": dead_ends,
            "two_road_nodes": sum(1 for d in degrees.values() if d == 2),
            "length_km": round(length_m / 1000, 3), "area_km2": round(area_km2, 4),
        },
        "legs": {k: legs.get(k, 0) for k in LEG_BINS},
        "cnr": round(len(junctions) / (len(junctions) + dead_ends), 4) if junctions or dead_ends else 0.0,
        "intersections_per_km2": per_km2(len(junctions)),
        "dead_ends_per_km2": per_km2(dead_ends),
        "street_density_km_per_km2": round(length_m / 1000 / area_km2, 3) if area_km2 > 0 else 0.0,
        "skew_deg": _distribution(skews),
        "skewed_share": round(sum(s >= params.skew_threshold_deg for s in skews) / len(skews), 4)
        if skews else 0.0,
        "min_arm_angle_deg": _distribution([min_arm_angle(graph, n) for n in junctions]),
        "offset_t_pairs": offset_t_pairs(graph, params.offset_t_distance),
        "orientation_entropy": round(entropy, 4),
        "orientation_order": round(phi, 4),
        "tortuosity": _distribution([t for t, _ in roads]),
        "curved_length_share": round(curved_length / roads_length, 4) if roads_length > 0 else 0.0,
        "block_area_m2": _distribution(blocks),
        "one_way_length_share": round(one_way / length_m, 4) if length_m > 0 else 0.0,
        "class_length_share": {k: round(v / length_m, 4) for k, v in sorted(by_class.items())}
        if length_m > 0 else {},
        "features": {k: len(meta.get(k) or []) for k in FEATURES},
    }


def summary_lines(m: dict[str, Any]) -> list[str]:
    """Human-readable summary of network_metrics() output."""
    c, legs = m["counts"], m["legs"]
    feats = ", ".join(f"{v} {k}" for k, v in m["features"].items() if v)
    return [
        (f"{m['name']}: {c['junctions']} junctions, {c['dead_ends']} dead ends, "
         f"{c['length_km']:.1f} km over {c['area_km2']:.2f} km^2; CNR {m['cnr']:.2f}"),
        ("  legs " + ", ".join(f"{k}: {v}" for k, v in legs.items())
         + f"; skewed {100 * m['skewed_share']:.0f} %, offset T pairs {m['offset_t_pairs']}"),
        (f"  orientation entropy {m['orientation_entropy']:.2f} (order {m['orientation_order']:.2f}), "
         f"curved length {100 * m['curved_length_share']:.0f} %, "
         f"blocks {m['block_area_m2']['n']}" + (f"; {feats}" if feats else "")),
    ]
