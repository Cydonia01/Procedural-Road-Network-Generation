"""Geometry metrics of an .xodr: how drivable and how smooth the roads are.

Works on the OpenDRIVE file alone, so it measures any network the same
way, whichever generator and builder made it:

- lateral acceleration a = v^2 |kappa| needed to drive each road's lanes
  at its posted speed (<type><speed>), using the peak lane-centre
  curvature, grouped into normal roads, connecting roads of two-road
  junctions (bends and lane-count changes) and connecting roads of real
  junctions (turns);
- the share of junctions joining exactly two roads;
- curvature jumps in the travel direction across lane links between roads,
  and across geometry boundaries inside a road.

Used for the stage A baseline (plans/2026-10-23-stage-a-geometry-implementation.md).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from lxml import etree

from roadgen.config import DEFAULTS, section
from roadgen.validate.continuity import RoadEval

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "metrics")
SPEED_UNITS = {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704}  # -> [m/s]
GROUPS = ("road", "bend", "turn")
PERCENTILES = (50, 90, 99)

PathLike = Union[str, Path]


@dataclass(frozen=True)
class GeometryMetricsParams:
    sample_step: float = _D["sample_step"]  # spacing of curvature samples along a road [m]
    comfort_lat_acc: float = _D["comfort_lat_acc"]  # comfortable lateral acceleration [m/s^2]
    curvature_jump_tol: float = _D["curvature_jump_tol"]  # jump counted above this [1/m]

    def __post_init__(self) -> None:
        if self.sample_step <= 0 or self.comfort_lat_acc <= 0 or self.curvature_jump_tol <= 0:
            raise ValueError("sample_step, comfort_lat_acc and curvature_jump_tol must be positive")


def speed_records(road: etree._Element) -> list[tuple[float, float]]:
    """(s, max speed [m/s]) from the road's <type> records, in s order."""
    out = []
    for t in road.findall("type"):
        sp = t.find("speed")
        if sp is None or sp.get("max") in (None, "no limit", "undefined"):
            continue
        out.append((float(t.get("s", 0)), float(sp.get("max")) * SPEED_UNITS[sp.get("unit", "m/s")]))
    return sorted(out)


def _speed_at(records: list[tuple[float, float]], s: float) -> Optional[float]:
    v = None
    for s0, speed in records:
        if s0 <= s + 1e-9:
            v = speed
    return v if v is not None else (records[0][1] if records else None)


def _driving_lanes(road: RoadEval, s: float) -> list[int]:
    section_el = road._section(s)
    return sorted(int(lane.get("id")) for lane in section_el.iter("lane")
                  if lane.get("id") != "0" and lane.get("type") == "driving")


def road_peak(road: RoadEval, step: float) -> tuple[float, float]:
    """(peak |lane-centre curvature| [1/m], peak lateral acceleration [m/s^2]) of a road."""
    speeds = speed_records(road.road)
    n = max(2, math.ceil(road.length / step) + 1)
    k_max = a_max = 0.0
    for s in np.linspace(0.0, road.length, n):
        s = float(s)
        v = _speed_at(speeds, s)
        for lane in _driving_lanes(road, s):
            k = abs(road.lane_curvature(lane, s))
            k_max = max(k_max, k)
            if v is not None:
                a_max = max(a_max, v * v * k)
    return k_max, a_max


def junction_road_counts(roads: dict[str, RoadEval]) -> dict[str, int]:
    """Junction id -> number of normal roads linked to it."""
    counts: dict[str, int] = {}
    for road in roads.values():
        if road.road.get("junction", "-1") != "-1":
            continue
        for kind in ("predecessor", "successor"):
            link = road.road.find(f"link/{kind}")
            if link is not None and link.get("elementType") == "junction":
                jid = link.get("elementId")
                counts[jid] = counts.get(jid, 0) + 1
    return counts


def _jump_at_joins(road: RoadEval) -> list[float]:
    """Curvature jumps of the reference line at geometry boundaries inside the road."""
    geoms = road.geometries
    jumps = []
    for prev, nxt in pairwise(geoms):
        s = float(nxt.get("s"))
        jumps.append(abs(road.curvature(s, prev) - road.curvature(s, nxt)))
    return jumps


def _jumps_at_links(roads: dict[str, RoadEval]) -> list[float]:
    """Travel-direction lane-centre curvature jumps across lane links between roads."""
    jumps = []
    for road in roads.values():
        for kind in ("predecessor", "successor"):
            link = road.road.find(f"link/{kind}")
            if link is None or link.get("elementType") != "road":
                continue
            other = roads.get(link.get("elementId"))
            if other is None:
                continue
            s_here = 0.0 if kind == "predecessor" else road.length
            s_there = 0.0 if link.get("contactPoint") == "start" else other.length
            for lane in road._section(s_here).iter("lane"):
                lane_link = lane.find(f"link/{kind}")
                if lane.get("id") == "0" or lane_link is None:
                    continue
                a, b = int(lane.get("id")), int(lane_link.get("id"))
                try:
                    jumps.append(abs(road.lane_curvature(a, s_here) - other.lane_curvature(b, s_there)))
                except KeyError:
                    continue  # missing lane: reported by the continuity check
    return jumps


def _distribution(values: list[float], limit: Optional[float] = None) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(values)}
    if values:
        arr = np.asarray(values)
        out.update({f"p{p}": round(float(np.percentile(arr, p)), 4) for p in PERCENTILES})
        out["max"] = round(float(arr.max()), 4)
        if limit is not None:
            out["over_limit"] = int((arr > limit).sum())
            out["over_limit_share"] = round(float((arr > limit).mean()), 4)
    return out


def geometry_metrics(xodr_path: PathLike,
                     params: GeometryMetricsParams = GeometryMetricsParams()) -> dict[str, Any]:
    """All geometry metrics of one .xodr as a JSON-ready dict."""
    tree = etree.parse(str(xodr_path))
    roads = {r.get("id"): RoadEval(r) for r in tree.getroot().findall("road")}
    junction_roads = junction_road_counts(roads)

    acc: dict[str, list[float]] = {g: [] for g in GROUPS}
    radius: dict[str, list[float]] = {g: [] for g in GROUPS}
    joins: list[float] = []
    length = 0.0
    for road in roads.values():
        jid = road.road.get("junction", "-1")
        if jid == "-1":
            group = "road"
            length += road.length
        else:
            group = "bend" if junction_roads.get(jid, 0) == 2 else "turn"
        k, a = road_peak(road, params.sample_step)
        acc[group].append(a)
        if k > 1e-9:
            radius[group].append(1.0 / k)
        joins += _jump_at_joins(road)
    links = _jumps_at_links(roads)

    n_junctions = len(tree.getroot().findall("junction"))
    two_road = sum(1 for n in junction_roads.values() if n == 2)
    tol = params.curvature_jump_tol
    return {
        "xodr": Path(xodr_path).name,
        "params": {"sample_step": params.sample_step, "comfort_lat_acc": params.comfort_lat_acc,
                   "curvature_jump_tol": tol},
        "counts": {
            "roads": sum(1 for r in roads.values() if r.road.get("junction", "-1") == "-1"),
            "connecting_roads": sum(1 for r in roads.values() if r.road.get("junction", "-1") != "-1"),
            "junctions": n_junctions,
            "two_road_junctions": two_road,
            "two_road_junction_share": round(two_road / n_junctions, 4) if n_junctions else 0.0,
            "road_length_m": round(length, 1),
        },
        "lateral_acceleration": {g: _distribution(acc[g], params.comfort_lat_acc) for g in GROUPS},
        "min_radius": {g: _distribution(radius[g]) for g in GROUPS},
        "curvature_jumps": {"links": _distribution(links, tol), "joins": _distribution(joins, tol)},
    }


def summary_lines(m: dict[str, Any]) -> list[str]:
    """Human-readable summary of geometry_metrics() output."""
    c = m["counts"]
    lines = [(
        f"{m['xodr']}: {c['roads']} roads, {c['junctions']} junctions "
        f"({c['two_road_junctions']} with two roads, {100 * c['two_road_junction_share']:.0f} %), "
        f"{c['connecting_roads']} connecting roads, {c['road_length_m'] / 1000:.1f} km"
    )]
    for g in GROUPS:
        d = m["lateral_acceleration"][g]
        if d["n"]:
            lines.append(
                f"  lateral acceleration, {g} ({d['n']}): median {d['p50']:.1f}, p90 {d['p90']:.1f}, "
                f"max {d['max']:.1f} m/s^2; {100 * d['over_limit_share']:.0f} % over "
                f"{m['params']['comfort_lat_acc']} m/s^2"
            )
    for where in ("links", "joins"):
        d = m["curvature_jumps"][where]
        if d["n"]:
            lines.append(f"  curvature jumps at {where} ({d['n']}): {d['over_limit']} over "
                         f"{m['params']['curvature_jump_tol']} 1/m, max {d['max']:.4f} 1/m")
    return lines
