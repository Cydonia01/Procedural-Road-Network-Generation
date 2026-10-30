"""Checks on the HD-map content of an .xodr (stage B), run when HD maps are on.

Road marks (B4):
- no <roadMark> on connecting roads: junction interiors are unmarked;
- within a lane, the marks' sOffsets start inside the lane section and
  ascend, so every stretch of the lane has exactly one mark.

Signals, objects and junction control (B5):
- every signal and roadMark object lies within its road's length and outer
  edge (signals up to SIGN_MARGIN beyond it), and its validity lanes exist
  at its station;
- signal ids are unique, and so are object ids (bridges and tunnels included);
- with the sidecar: every stop, yield or signal approach that has incoming
  lanes has a stop or yield line within APPROACH_REACH of the road end, and
  every stop and yield approach a facing 206 / 205 sign there;
- every <priority> record names two connecting roads of its own junction.
"""

from __future__ import annotations

import json
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from roadgen.validate.continuity import RoadEval, _piecewise, _poly, _width

PathLike = Union[str, Path]
EPS = 1e-6  # [m]
EDGE_TOL = 0.01  # objects may reach this far past the outer edge [m]
SIGN_MARGIN = 2.0  # signs may stand this far outside the outer edge [m]
APPROACH_REACH = 5.0  # stop / yield lines and their signs within this of the road end [m]
LINE_NAMES = ("stopLine", "yieldLine")
SIGNS = {"stop": "206", "yield": "205"}


def check_hdmap(xodr_path: PathLike) -> list[str]:
    """Checks of the .xodr, and of its control against the sidecar next to it if present."""
    path = Path(xodr_path)
    sidecar = path.with_suffix(".roadmap.json")
    return check_hdmap_tree(etree.parse(str(path)), json.loads(sidecar.read_text()) if sidecar.exists() else None)


def check_hdmap_tree(tree: etree._ElementTree, sidecar: Optional[dict] = None) -> list[str]:
    """Return a list of errors; empty means the HD-map content is consistent."""
    errors: list[str] = []
    root = tree.getroot()
    errors += _check_furniture(root)
    if sidecar is not None:
        errors += _check_approaches(root, sidecar)
    errors += _check_priorities(root)
    for road in root.findall("road"):
        rid = road.get("id")
        if road.get("junction", "-1") != "-1":
            n = len(road.findall(".//roadMark"))
            if n:
                errors.append(f"road {rid}: connecting road has {n} road marks (junctions are unmarked)")
            continue
        errors += _check_mark_offsets(road)
    return errors


def _check_mark_offsets(road: etree._Element) -> list[str]:
    sections = road.findall("lanes/laneSection")
    bounds = [float(sec.get("s")) for sec in sections] + [float(road.get("length"))]
    errors = []
    for k, sec in enumerate(sections):
        length = bounds[k + 1] - bounds[k]
        for lane in sec.iter("lane"):
            offsets = [float(m.get("sOffset", "0")) for m in lane.findall("roadMark")]
            where = f"road {road.get('id')} section {k} lane {lane.get('id')}"
            if any(s < -EPS or s > length + EPS for s in offsets):
                errors.append(f"{where}: road mark sOffset outside the lane section (length {length:.3f} m)")
            elif any(b <= a + EPS for a, b in pairwise(offsets)):
                errors.append(f"{where}: road mark sOffsets not ascending: {offsets}")
    return errors


def _outer_edges(ev: RoadEval, s: float) -> tuple[float, float]:
    """(right, left) t of the road's outer edges at s [m]."""
    rec = _piecewise(ev.offsets, s, "s")
    offset = _poly(rec, s - float(rec.get("s"))) if rec is not None else 0.0
    sec = ev._section(s)
    ds = s - float(sec.get("s"))
    right = sum(_width(lane, ds) for lane in sec.findall("right/lane"))
    left = sum(_width(lane, ds) for lane in sec.findall("left/lane"))
    return offset - right, offset + left


def _check_furniture(root: etree._Element) -> list[str]:
    errors = []
    signal_ids: Counter = Counter()
    object_ids: Counter = Counter()
    for road in root.findall("road"):
        rid = road.get("id")
        ev = RoadEval(road)
        items = [(sig, SIGN_MARGIN, 0.0) for sig in road.findall("signals/signal")]
        for obj in road.findall("objects/object"):
            corners = [float(c.get("v")) for c in obj.findall("outlines/outline/cornerLocal")]
            items.append((obj, EDGE_TOL, max((abs(v) for v in corners), default=0.0)))
        for tag in ("bridge", "tunnel"):
            object_ids.update(el.get("id") for el in road.findall(f"objects/{tag}"))
        for el, margin, reach in items:
            (signal_ids if el.tag == "signal" else object_ids)[el.get("id")] += 1
            s, t = float(el.get("s")), float(el.get("t"))
            where = f"road {rid} {el.tag} {el.get('id')} ({el.get('name')})"
            if not -EPS <= s <= ev.length + EPS:
                errors.append(f"{where}: s={s:.3f} outside the road (length {ev.length:.3f})")
                continue
            right, left = _outer_edges(ev, s)
            if t - reach < right - margin or t + reach > left + margin:
                errors.append(f"{where}: t={t:.3f} reaches outside the road ({right:.3f} .. {left:.3f})")
            lanes = ev.lane_ids(s)
            for v in el.findall("validity"):
                missing = {int(v.get("fromLane")), int(v.get("toLane"))} - lanes
                if missing:
                    errors.append(f"{where}: validity lanes {sorted(missing)} do not exist at s={s:.3f}")
    errors += [f"signal id {i} used {n} times" for i, n in sorted(signal_ids.items()) if n > 1]
    errors += [f"object id {i} used {n} times" for i, n in sorted(object_ids.items()) if n > 1]
    return errors


def _incoming(road: etree._Element, contact: str) -> list[int]:
    """Driving lanes that carry traffic into the junction at a road end."""
    sections = road.findall("lanes/laneSection")
    sec, side = (sections[-1], "right") if contact == "end" else (sections[0], "left")
    return [int(lane.get("id")) for lane in sec.findall(f"{side}/lane") if lane.get("type") == "driving"]


def _check_approaches(root: etree._Element, sidecar: dict) -> list[str]:
    roads = {r.get("id"): r for r in root.findall("road")}
    errors = []
    for j in sidecar.get("junctions", []):
        for a in j["approaches"]:
            role, road = a["role"], roads.get(str(a["road"]))
            if role not in ("stop", "yield", "signal") or road is None or not _incoming(road, a["contact"]):
                continue
            length = float(RoadEval(road).length)
            s_end = length if a["contact"] == "end" else 0.0

            def near(el: etree._Element, s_end: float = s_end) -> bool:
                return abs(float(el.get("s")) - s_end) <= APPROACH_REACH + EPS

            where = f"junction {j['junction']}: {role} approach road {a['road']} ({a['contact']})"
            if not any(near(o) for o in road.findall("objects/object") if o.get("name") in LINE_NAMES):
                errors.append(f"{where}: no stop or yield line within {APPROACH_REACH:g} m of the road end")
            if role in SIGNS:
                facing = "+" if a["contact"] == "end" else "-"
                if not any(near(s) and s.get("type") == SIGNS[role] and s.get("orientation") == facing
                           for s in road.findall("signals/signal")):
                    errors.append(f"{where}: no facing {SIGNS[role]} sign within {APPROACH_REACH:g} m of the road end")
    return errors


def _check_priorities(root: etree._Element) -> list[str]:
    errors = []
    for junction in root.findall("junction"):
        own = {c.get("connectingRoad") for c in junction.findall("connection")}
        for p in junction.findall("priority"):
            bad = {p.get("high"), p.get("low")} - own
            if bad:
                errors.append(f"junction {junction.get('id')}: priority record names roads {sorted(bad)} "
                              "that are not its connecting roads")
    return errors
