"""HD-map metrics of an .xodr: what besides driving lanes the map holds.

Works on the OpenDRIVE file alone (plus its .roadmap.json sidecar, where
there is one), so it counts any network the same way:

- lane length per lane type, as km of lane per km of normal road (stretches
  where a lane is 0 m wide left out);
- road marks on normal roads by type and colour, and marks on connecting
  roads (a junction interior should have none);
- signals by country and type(-subtype), dynamic ones (lights) apart, and
  objects by type (bridge and tunnel records are not objects), per km of
  road and per junction;
- controllers, their controls, junction controllers and junction priority
  records;
- from the sidecar, if it has them: the junction control mix, signal plans
  and their phases, lane-use records, banned movements.

Used for the stage B baseline (plans/2026-10-29-stage-b-implementation.md).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Union

from lxml import etree

log = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _road_length(road: etree._Element) -> float:
    return float(road.get("length", 0.0))


def _section_spans(road: etree._Element) -> list[tuple[etree._Element, float]]:
    """(laneSection, length [m]) of a road's lane sections."""
    sections = road.findall("lanes/laneSection")
    starts = [float(s.get("s", 0.0)) for s in sections] + [_road_length(road)]
    return [(sec, max(starts[k + 1] - starts[k], 0.0)) for k, sec in enumerate(sections)]


def _wide_length(lane: etree._Element, section_length: float) -> float:
    """Length [m] of a lane section over which the lane is not 0 m wide: its <width>
    records that are not all zero (medians and pockets run the whole road, 0 m wide
    where they are absent)."""
    widths = sorted(lane.findall("width"), key=lambda w: float(w.get("sOffset", 0)))
    if not widths:
        return section_length
    starts = [float(w.get("sOffset", 0)) for w in widths] + [section_length]
    return sum(max(starts[k + 1] - starts[k], 0.0) for k, w in enumerate(widths)
               if any(abs(float(w.get(c, 0))) > 1e-9 for c in "abcd"))


def _sign_key(sig: etree._Element) -> str:
    subtype = sig.get("subtype", "-1")
    key = f"{sig.get('country', '?')}:{sig.get('type', '?')}"
    return key if subtype in ("-1", "", None) else f"{key}-{subtype}"


def _per(n: float, d: float) -> float:
    return round(n / d, 4) if d > 0 else 0.0


def hdmap_metrics(xodr_path: PathLike) -> dict[str, Any]:
    """Counts of lane types, marks, signals, objects and controls of one .xodr."""
    path = Path(xodr_path)
    root = etree.parse(str(path)).getroot()
    roads = root.findall("road")
    normal = [r for r in roads if r.get("junction", "-1") == "-1"]
    connecting = [r for r in roads if r.get("junction", "-1") != "-1"]
    junctions = root.findall("junction")
    length_km = sum(_road_length(r) for r in normal) / 1000.0

    lane_km: Counter = Counter()
    marks: Counter = Counter()
    for road in normal:
        for sec, length in _section_spans(road):
            for lane in sec.iter("lane"):
                if lane.get("id") != "0":
                    lane_km[lane.get("type", "none")] += _wide_length(lane, length) / 1000.0
                for mark in lane.findall("roadMark"):
                    marks[f"{mark.get('type', '?')}/{mark.get('color', 'standard')}"] += 1
    connecting_marks = sum(len(r.findall(".//roadMark")) for r in connecting)

    signals: Counter = Counter()
    dynamic = 0
    objects: Counter = Counter()
    for road in roads:
        for sig in road.findall("signals/signal"):
            signals[_sign_key(sig)] += 1
            dynamic += sig.get("dynamic") == "yes"
        for obj in road.findall("objects/object"):
            objects[obj.get("type", "none")] += 1
    n_signals, n_objects = sum(signals.values()), sum(objects.values())
    n_junctions = len(junctions)

    m: dict[str, Any] = {
        "xodr": path.name,
        "counts": {"roads": len(normal), "connecting_roads": len(connecting), "junctions": n_junctions,
                   "road_length_km": round(length_km, 3)},
        "lane_km_per_road_km": {k: _per(v, length_km) for k, v in sorted(lane_km.items())},
        "road_marks": dict(sorted(marks.items())),
        "connecting_road_marks": connecting_marks,
        "signals": {"total": n_signals, "dynamic": dynamic, "per_km": _per(n_signals, length_km),
                    "per_junction": _per(n_signals, n_junctions), "by_type": dict(sorted(signals.items()))},
        "objects": {"total": n_objects, "per_km": _per(n_objects, length_km),
                    "per_junction": _per(n_objects, n_junctions), "by_type": dict(sorted(objects.items()))},
        "controllers": len(root.findall("controller")),
        "controls": len(root.findall("controller/control")),
        "junction_controllers": len(root.findall("junction/controller")),
        "junction_priorities": len(root.findall("junction/priority")),
    }
    m.update(_sidecar_metrics(path.with_suffix(".roadmap.json"), n_junctions))
    return m


def _sidecar_metrics(sidecar: Path, n_junctions: int) -> dict[str, Any]:
    """Junction control mix, signal plans, lane use and bans from the sidecar (empty if absent)."""
    if not sidecar.exists():
        return {}
    data = json.loads(sidecar.read_text())
    out: dict[str, Any] = {}
    if "junctions" in data:
        mix = Counter(j.get("control", "none") for j in data["junctions"])
        out["control_mix"] = {k: _per(v, n_junctions) for k, v in sorted(mix.items())}
    if "signal_plans" in data:
        plans = data["signal_plans"]
        out["signal_plans"] = {"junctions": len(plans),
                               "phases": sum(len(p.get("phases", [])) for p in plans)}
    if "lane_use" in data:
        out["lane_use_records"] = len(data["lane_use"])
    if "banned" in data:
        out["banned_movements"] = len(data["banned"])
    return out


def summary_lines(m: dict[str, Any]) -> list[str]:
    """Human-readable summary of hdmap_metrics() output."""
    c = m["counts"]
    lanes = ", ".join(f"{k} {v:.2f}" for k, v in m["lane_km_per_road_km"].items())
    lines = [f"{m['xodr']}: {c['road_length_km']:.1f} km of road; lane km per road km: {lanes}"]
    marks = ", ".join(f"{k} {v}" for k, v in m["road_marks"].items())
    lines.append(f"  road marks: {marks or 'none'}; {m['connecting_road_marks']} on connecting roads")
    s, o = m["signals"], m["objects"]
    lines.append(f"  signals: {s['total']} ({s['dynamic']} dynamic, {s['per_km']:.2f} per km); "
                 f"objects: {o['total']} ({o['per_km']:.2f} per km); controllers {m['controllers']}, "
                 f"junction priorities {m['junction_priorities']}")
    if "control_mix" in m:
        lines.append("  junction control: " + ", ".join(f"{k} {100 * v:.0f} %" for k, v in m["control_mix"].items()))
    return lines
