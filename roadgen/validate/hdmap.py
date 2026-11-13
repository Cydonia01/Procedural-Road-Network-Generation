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

Traffic lights (B6):
- every light (dynamic signal) is in exactly one <controller>, every
  controlled signal exists, and every <junction><controller> names an
  existing controller;
- with the sidecar: every signal approach with incoming lanes has a facing
  light within APPROACH_REACH of the road end; every plan's junction has a
  controller holding exactly the plan's lights; every phase sets every light
  of its plan, every light turns green once per cycle, every green lasts at
  least the plan's min_green; and no two conflicting movements are green in
  one phase, unless one yields to the other (a permissive left turn).

Lane types (B9):
- a lane linked to the next lane section, or across a road-to-road link, keeps
  its type;
- every junction <laneLink> joins two lanes of the same type, so no driving
  lane leads into a non-driving lane or the other way round;
- with the sidecar: every lane of a movement's connecting road is a driving lane.

Lane use and banned turns (B8), with the sidecar:
- every lane_use record matches the movements of its approach;
- left-turn pockets feed only left turns and U-turns;
- no movement makes a banned turn, every approach with a banned turn has a
  facing 209 / 214 sign at its stop line, and the banned turn's exit road is
  still reachable from its approach through other movements.
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
APPROACH_REACH = 8.0  # stop / yield lines and their signs within this of the road end [m]
LINE_NAMES = ("stopLine", "yieldLine")
LIGHT_TYPE = "1000001"
GREEN = "off;off;on"
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
    errors += _check_lane_types(root, sidecar)
    if sidecar is not None:
        errors += _check_approaches(root, sidecar)
    errors += _check_priorities(root)
    errors += _check_controllers(root)
    if sidecar is not None:
        errors += _check_plans(root, sidecar)
        errors += _check_lane_use(sidecar)
        errors += _check_bans(root, sidecar)
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
            if obj.get("type") == "patch":  # junction-surface fill: in the junction, not on its host road
                object_ids[obj.get("id")] += 1
                continue
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


def _lane_types(road: etree._Element) -> list[dict[int, str]]:
    """Per lane section of a road: lane id -> type (centre lane left out)."""
    return [{int(lane.get("id")): lane.get("type") for lane in sec.iter("lane") if lane.get("id") != "0"}
            for sec in road.findall("lanes/laneSection")]


def _check_lane_types(root: etree._Element, sidecar: Optional[dict] = None) -> list[str]:
    errors = []
    roads = {r.get("id"): r for r in root.findall("road")}
    types = {rid: _lane_types(r) for rid, r in roads.items()}

    def lane_at(rid: str, contact: str) -> dict[int, str]:
        secs = types.get(rid) or [{}]
        return secs[0] if contact == "start" else secs[-1]

    for rid, road in roads.items():
        secs = road.findall("lanes/laneSection")
        for k, sec in enumerate(secs):
            for lane in sec.iter("lane"):
                lid = int(lane.get("id"))
                if lid == 0:
                    continue
                kind = lane.get("type")
                for tag, step in (("successor", 1), ("predecessor", -1)):
                    link = lane.find(f"link/{tag}")
                    if link is None:
                        continue
                    target = int(link.get("id"))
                    if 0 <= k + step < len(secs):  # the neighbouring section of the same road
                        other, where = types[rid][k + step].get(target), f"section {k + step}"
                    else:  # across the road's own link (a junction link has no lane links here)
                        rlink = road.find(f"link/{tag}")
                        if rlink is None or rlink.get("elementType") != "road":
                            continue
                        to = rlink.get("elementId")
                        other = lane_at(to, rlink.get("contactPoint", "start")).get(target)
                        where = f"road {to}"
                    if other is not None and other != kind:
                        errors.append(f"road {rid} section {k} lane {lid} ({kind}) links to lane {target} "
                                      f"({other}) of {where}")
    for junction in root.findall("junction"):
        for c in junction.findall("connection"):
            inc, con = c.get("incomingRoad"), c.get("connectingRoad")
            if inc not in roads or con not in roads:
                continue  # topology reports it
            # The incoming road's end at this junction: the end its link names the junction.
            jid = junction.get("id")
            end = "end" if any(lk.get("elementType") == "junction" and lk.get("elementId") == jid
                               for lk in roads[inc].findall("link/successor")) else "start"
            from_types = lane_at(inc, end)
            to_types = lane_at(con, c.get("contactPoint", "start"))
            for ll in c.findall("laneLink"):
                a, b = from_types.get(int(ll.get("from"))), to_types.get(int(ll.get("to")))
                if a is not None and b is not None and a != b:
                    errors.append(f"junction {jid} connection {c.get('id')}: lane {ll.get('from')} ({a}) of road "
                                  f"{inc} links to lane {ll.get('to')} ({b}) of connecting road {con}")
    if sidecar is not None:
        for rid in sorted({str(m["connecting_road"]) for m in sidecar.get("movements", [])}, key=int):
            other = {t for sec in types.get(rid, []) for t in sec.values()} - {"driving"}
            if other:
                errors.append(f"connecting road {rid} of a movement has non-driving lanes: {sorted(other)}")
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
            if role == "signal" and not any(near(s) and s.get("type") == LIGHT_TYPE
                                            and s.get("orientation") == ("+" if a["contact"] == "end" else "-")
                                            for s in road.findall("signals/signal")):
                errors.append(f"{where}: no facing light within {APPROACH_REACH:g} m of the road end")
            if role in SIGNS:
                facing = "+" if a["contact"] == "end" else "-"
                if not any(near(s) and s.get("type") == SIGNS[role] and s.get("orientation") == facing
                           for s in road.findall("signals/signal")):
                    errors.append(f"{where}: no facing {SIGNS[role]} sign within {APPROACH_REACH:g} m of the road end")
    return errors


def _check_controllers(root: etree._Element) -> list[str]:
    errors = []
    lights = {s.get("id") for s in root.iter("signal") if s.get("dynamic") == "yes"}
    signals = {s.get("id") for s in root.iter("signal")}
    controlled: Counter = Counter()
    controllers = {c.get("id") for c in root.findall("controller")}
    for c in root.findall("controller"):
        for ctl in c.findall("control"):
            controlled[ctl.get("signalId")] += 1
            if ctl.get("signalId") not in signals:
                errors.append(f"controller {c.get('id')}: controls missing signal {ctl.get('signalId')}")
    for sid in sorted(lights, key=int):
        if controlled[sid] != 1:
            errors.append(f"light {sid} is in {controlled[sid]} controllers (needs exactly one)")
    for j in root.findall("junction"):
        for c in j.findall("controller"):
            if c.get("id") not in controllers:
                errors.append(f"junction {j.get('id')}: names missing controller {c.get('id')}")
    return errors


def _check_plans(root: etree._Element, sidecar: dict) -> list[str]:
    errors = []
    junctions = {j.get("id"): j for j in root.findall("junction")}
    controls = {c.get("id"): {ctl.get("signalId") for ctl in c.findall("control")} for c in root.findall("controller")}
    movements = [m for m in sidecar.get("movements", [])]
    for plan in sidecar.get("signal_plans", []):
        where = f"signal plan of junction {plan['junction']}"
        lights = set(plan["lights"])
        junction = junctions.get(str(plan["junction"]))
        ids = [c.get("id") for c in junction.findall("controller")] if junction is not None else []
        if not ids or controls.get(ids[0]) != lights:
            errors.append(f"{where}: its junction has no controller holding exactly its lights")
        green_once = set()
        approach_of = {sid: tuple(key) for sid, key in plan["lights"].items()}
        light_lanes = plan.get("light_lanes")  # B8: a light releases only its lanes
        permissive = {tuple(sorted(p)) for p in plan.get("permissive", [])}
        conflicts = [tuple(p) for p in plan.get("conflicts", [])]
        moves = {m["connecting_road"]: m for m in movements if m["junction_id"] == plan["junction"]}
        for k, phase in enumerate(plan["phases"]):
            if set(phase["states"]) != lights:
                errors.append(f"{where}: phase {k} does not set exactly the plan's lights")
            green = {sid for sid, st in phase["states"].items() if st == GREEN}
            green_once |= green
            if green and phase["duration"] < plan.get("min_green", 0.0) - EPS:
                errors.append(f"{where}: phase {k} green for {phase['duration']} s, below min_green")
            open_ = {(*approach_of[s], lane) for s in green for lane in (light_lanes or {}).get(s, [None])}

            def released(m: dict, light_lanes=light_lanes, open_=open_) -> bool:
                return (*_approach(m), m["from_lane"] if light_lanes else None) in open_

            for a, b in conflicts:
                ma, mb = moves.get(a), moves.get(b)
                if ma is None or mb is None:
                    continue
                if released(ma) and released(mb) and tuple(sorted((a, b))) not in permissive:
                    errors.append(f"{where}: phase {k} gives green to conflicting movements {a} and {b}")
        missing = lights - green_once
        if missing:
            errors.append(f"{where}: lights {sorted(missing, key=int)} never turn green")
    return errors


def _approach(m: dict) -> tuple[int, str]:
    """(road, contact) of the approach a movement arrives on: right lanes arrive at the road's end."""
    return m["from_road"], "end" if m["from_lane"] < 0 else "start"


def _check_lane_use(sidecar: dict) -> list[str]:
    errors = []
    uses: dict[tuple, dict[int, set]] = {}
    for m in sidecar.get("movements", []):
        key = (m["junction_id"], m["from_road"], "end" if m["from_lane"] < 0 else "start")
        uses.setdefault(key, {}).setdefault(m["from_lane"], set()).add(m["kind"])
    for rec in sidecar.get("lane_use", []):
        key = (rec["junction"], rec["road"], rec["contact"])
        where = f"junction {rec['junction']}: approach road {rec['road']} ({rec['contact']})"
        lanes = {int(k): set(v) for k, v in rec["lanes"].items()}
        if lanes != uses.get(key, {}):
            errors.append(f"{where}: lane_use does not match its movements")
        for lane in rec.get("pockets", []):
            if not lanes.get(lane, set()) <= {"left", "uturn"}:
                errors.append(f"{where}: pocket lane {lane} feeds {sorted(lanes.get(lane, set()))}, not only left")
    return errors


def _check_bans(root: etree._Element, sidecar: dict) -> list[str]:
    import networkx as nx

    bans = sidecar.get("banned", [])
    if not bans:
        return []
    errors = []
    moves = sidecar.get("movements", [])
    g = nx.DiGraph()
    for m in moves:  # road traversals (+1 along s), linked by the movements
        g.add_edge((m["from_road"], 1 if m["from_lane"] < 0 else -1), (m["to_road"], 1 if m["to_lane"] < 0 else -1))
    roads = {r.get("id"): r for r in root.findall("road")}
    for b in bans:
        (fr, fc), (tr, tc) = b["from"], b["to"]
        where = f"junction {b['junction']}: banned turn road {fr} ({fc}) -> road {tr} ({tc})"
        made = [m for m in moves if m["junction_id"] == b["junction"] and m["from_road"] == fr
                and (m["from_lane"] < 0) == (fc == "end") and m["to_road"] == tr and (m["to_lane"] < 0) == (tc == "start")]
        if made:
            errors.append(f"{where}: still made by {len(made)} movements")
        source, target = (fr, 1 if fc == "end" else -1), (tr, 1 if tc == "start" else -1)
        if source in g and target in g and not nx.has_path(g, source, target):
            errors.append(f"{where}: the exit road is no longer reachable from the approach")
        road = roads.get(str(fr))
        if road is not None:
            s_end = float(road.get("length")) if fc == "end" else 0.0
            facing = "+" if fc == "end" else "-"
            if not any(s.get("type") in ("209", "214") and s.get("orientation") == facing
                       and abs(float(s.get("s")) - s_end) <= APPROACH_REACH + EPS for s in road.findall("signals/signal")):
                errors.append(f"{where}: no facing 209 / 214 sign at the approach")
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
