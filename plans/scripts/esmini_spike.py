"""Stage B, B0 spike: what esmini 3.9 does with HD-map content.

Builds small fixtures in out/spike/ and runs esmini on them:

- junction.xodr: tests/fixtures/4way_avenue_x_street.json built by our
  builder (junction_margin 12 m for room), then edited by hand:
  - parking, curb and sidewalk lanes (curb and sidewalk with <height> 0.15 m) on both sides of every road;
  - a solid_solid centre line on the avenue and a curb mark on curb lanes;
  - on every approach: a stop line (DE 294 signal + roadMark object) and a crosswalk object (outline + zebra markings);
  - lights (OpenDRIVE 1000001, dynamic) on the avenue approaches, stop (206) and yield (205) signs on the street;
  - 274 speed signs with subtype "50" and with value 50, 306, 215 and 350-10 along the avenue;
  - a <controller> for the lights, a junction <controller> and a junction <priority> record;
  - four sidewalk-only connecting roads around the corners.
- lanes.xodr: two straight roads, one with biking, parking, curb and
  sidewalk, one highway-like with a median lane at -1/+1 (driving lanes
  -2, -3), shoulders and borders.
- traffic: our scenario (random lanes) on junction.xodr, run headless;
  every logged position must be on a driving lane, never on a corner road.
- lights: two .xosc: a RoadNetwork TrafficSignalController with phases,
  and storyboard TrafficSignalStateActions; screenshots of the light at
  two times show which one switches.
- drive-check (movements) and the lane drive (esmini vs our evaluator) on
  both fixtures, our validators, and screenshots of every fixture.

Prints a JSON summary; findings are written into
plans/2026-10-29-stage-b-implementation.md (B0 result).

Usage (dev container): python plans/scripts/esmini_spike.py
"""
import csv
import io
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from lxml import etree
from scenariogeneration import xodr

from roadgen.geometry.planar import Pose
from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.junctions import turn_path
from roadgen.sim.drive_check import drive_lanes, drive_movements
from roadgen.sim.esmini import esmini_binary
from roadgen.sim.screenshot import screenshot, still_xosc
from roadgen.validate.continuity import RoadEval, check_continuity
from roadgen.validate.topology import check_topology

OUT = Path("out/spike")
SIDES = [("parking", 2.2, 0.0), ("curb", 0.15, 0.15), ("sidewalk", 2.0, 0.15)]
LIGHT_RED, LIGHT_YELLOW, LIGHT_GREEN = "on;off;off", "off;on;off", "off;off;on"


def lx(element) -> etree._Element:
    """A scenariogeneration (ElementTree) element as an lxml element."""
    return etree.fromstring(ET.tostring(element))


def lane_xml(lane_id: int, kind: str, width: float, height: float, mark: str = "none") -> etree._Element:
    lane = etree.Element("lane", id=str(lane_id), type=kind, level="false")
    etree.SubElement(lane, "link")
    etree.SubElement(lane, "width", sOffset="0", a=str(width), b="0", c="0", d="0")
    etree.SubElement(lane, "roadMark", sOffset="0", type=mark, weight="standard", color="standard",
                     width="0.15" if mark != "none" else "0")
    if height:
        etree.SubElement(lane, "height", sOffset="0", inner=str(height), outer=str(height))
    return lane


def add_side_lanes(road: etree._Element, sides) -> dict[str, int]:
    """Append side lanes outside the driving lanes; returns driving lane counts per side."""
    counts = {}
    for sec in road.findall("lanes/laneSection"):
        for side, sign in (("right", -1), ("left", 1)):
            group = sec.find(side)
            if group is None:
                group = etree.SubElement(sec, side)
            n = len(group.findall("lane"))
            counts[side] = n
            for k, (kind, width, height) in enumerate(sides, 1):
                mark = "curb" if kind == "curb" else "none"
                new = lane_xml(sign * (n + k), kind, width, height, mark)
                if side == "right":
                    group.append(new)
                else:
                    group.insert(0, new)
    return counts


def ensure(road: etree._Element, tag: str) -> etree._Element:
    el = road.find(tag)
    if el is None:
        el = etree.Element(tag)
        road.insert(list(road).index(road.find("lanes")) + 1, el)
    return el


def road_ends(root: etree._Element) -> list[dict]:
    """Every normal road end at a junction: road, contact, s, outward heading."""
    ends = []
    for road in root.findall("road"):
        if road.get("junction") != "-1":
            continue
        ev = RoadEval(road)
        for kind, contact in (("predecessor", "start"), ("successor", "end")):
            link = road.find(f"link/{kind}")
            if link is None or link.get("elementType") != "junction":
                continue
            s = 0.0 if contact == "start" else ev.length
            hdg = ev.reference(s)[2] + (0.0 if contact == "start" else math.pi)
            ends.append({"road": road, "ev": ev, "contact": contact, "s": s, "out": hdg % (2 * math.pi),
                         "junction": link.get("elementId")})
    return ends


def width_of(road: etree._Element, side: str, kinds=("driving",)) -> float:
    sec = road.find("lanes/laneSection")
    return sum(float(l.find("width").get("a")) for l in sec.findall(f"{side}/lane") if l.get("type") in kinds)


def marked_object(s, t, kind, oid, name, right, left, depth, stripe) -> xodr.Object:
    """An object across the road from t - right to t + left, drawn as one marking of
    width depth [m] along its centre: solid (stripe 0), or stripes of length stripe [m]."""
    obj = xodr.Object(s, t, getattr(xodr.ObjectType, kind), subtype="-1", id=str(oid), name=name)
    outline = xodr.Outline(closed=False)
    outline.add_corner(xodr.CornerLocal(0.0, -right, 0.0, 0.0, id=0))
    outline.add_corner(xodr.CornerLocal(0.0, left, 0.0, 0.0, id=1))
    obj.add_outline(outline)
    marking = xodr.Marking(xodr.RoadMarkColor.white, stripe or right + left, xodr.SideType.front,
                           stripe, 0.0, 0.0, width=depth, zOffset=0.01)
    marking.add_cornerReference(0)
    marking.add_cornerReference(1)
    obj.add_marking(marking)
    return obj


def furnish_junction(path: Path) -> dict:
    root = etree.parse(str(path)).getroot()
    signals, lights = [], []
    next_id = 100
    for road in root.findall("road"):
        if road.get("junction") == "-1":
            add_side_lanes(road, SIDES)
    avenue_roads = set()
    for end in road_ends(root):
        road, contact = end["road"], end["contact"]
        avenue = width_of(road, "right") >= 7.0
        sign = 1 if contact == "end" else -1  # +s runs into the junction at an "end" contact
        side = "right" if contact == "end" else "left"
        lanes = [int(l.get("id")) for l in road.find("lanes/laneSection").findall(f"{side}/lane")
                 if l.get("type") == "driving"]
        carriage = width_of(road, side, ("driving", "parking"))
        drive = width_of(road, side)
        roadside = -sign * (carriage + 0.15 + 1.0)  # on the sidewalk, right of the traffic
        s_stop = end["s"] - sign * 5.0
        s_cross = end["s"] - sign * 2.5
        orient = xodr.Orientation.positive if sign > 0 else xodr.Orientation.negative
        sigs = ensure(road, "signals")
        objs = ensure(road, "objects")

        def add_signal(s, t, country, typ, subtype="-1", value=None, unit=None, dynamic=False, z=2.0, name="",
                       orient=orient, lanes=lanes, sigs=sigs, road=road):
            nonlocal next_id
            sig = xodr.Signal(s, t, country, typ, subtype=subtype, id=str(next_id), name=name or f"{typ}_{next_id}",
                              dynamic=xodr.Dynamic.yes if dynamic else xodr.Dynamic.no, value=value, unit=unit,
                              zOffset=z, orientation=orient, height=0.8 if dynamic else 0.6, width=0.6)
            sig.add_validity(min(lanes, key=abs), max(lanes, key=abs))
            sigs.append(lx(sig.get_element()))
            signals.append({"id": next_id, "road": road.get("id"), "type": f"{country}:{typ}-{subtype}"})
            next_id += 1
            return next_id - 1

        add_signal(s_stop, -sign * drive / 2, "DE", "294", z=0.0, name="stopline")
        if avenue:
            avenue_roads.add(road.get("id"))
            lights.append((add_signal(s_stop, roadside, "OpenDRIVE", "1000001", dynamic=True, z=3.0, name="light"),
                           road.get("id")))
        else:
            add_signal(s_stop, roadside, "DE", "206" if contact == "end" else "205")
        # Stop line and crosswalk as objects whose markings follow two outline corners
        # (esmini draws "side" markings only along a length / width bounding box).
        objs.append(lx(marked_object(s_stop, -sign * drive / 2, "roadMark", next_id, "stopLine",
                                     drive / 2, drive / 2, 0.5, 0.0).get_element()))
        next_id += 1
        half_l, half_r = width_of(road, "left", ("driving", "parking")), width_of(road, "right", ("driving", "parking"))
        objs.append(lx(marked_object(s_cross, 0.0, "crosswalk", next_id, "crosswalk", half_r, half_l,
                                     3.0, 0.5).get_element()))
        next_id += 1
    # Along one avenue road: 274 with subtype "50" and with value 50, 306, 215, 350-10.
    ave = next(r for r in root.findall("road") if r.get("id") in avenue_roads)
    sigs = ave.find("signals")
    t = -(width_of(ave, "right", ("driving", "parking")) + 1.2)
    for k, (typ, sub, val) in enumerate([("274", "50", None), ("274", "-1", 50.0), ("306", "-1", None),
                                         ("215", "-1", None), ("350", "10", None)]):
        sig = xodr.Signal(20.0 + 6 * k, t, "DE", typ, subtype=sub, id=str(next_id), value=val,
                          unit="km/h" if val else None, zOffset=2.0, height=0.6, width=0.6)
        sigs.append(lx(sig.get_element()))
        signals.append({"id": next_id, "road": ave.get("id"), "type": f"DE:{typ}-{sub}", "value": val})
        next_id += 1
    # Centre line solid_solid on the avenue.
    for road in root.findall("road"):
        if road.get("id") in avenue_roads:
            mark = road.find("lanes/laneSection/center/lane/roadMark")
            mark.set("type", "solid solid")
    junction = root.find("junction")
    # Controller, junction controller, a priority record.
    last_road = root.findall("road")[-1]
    ctrl = etree.Element("controller", id="1", name="ctrl_j", sequence="0")
    for sid, _ in lights:
        etree.SubElement(ctrl, "control", signalId=str(sid), type="0")
    last_road.addnext(ctrl)
    conns = junction.findall("connection")
    etree.SubElement(junction, "priority", high=conns[0].get("connectingRoad"), low=conns[1].get("connectingRoad"))
    etree.SubElement(junction, "controller", id="1", type="0", sequence="0")
    corners = add_sidewalk_corners(root, junction)
    etree.ElementTree(root).write(str(path), pretty_print=True, xml_declaration=True, encoding="utf-8")
    return {"signals": signals, "lights": lights, "corner_roads": corners}


def add_sidewalk_corners(root: etree._Element, junction: etree._Element) -> list[int]:
    """Sidewalk-only connecting roads between neighbouring arms (right turns along the corner)."""
    ends = sorted(road_ends(root), key=lambda e: e["out"])
    next_road = max(int(r.get("id")) for r in root.findall("road")) + 1
    next_conn = max(int(c.get("id")) for c in junction.findall("connection")) + 1
    made = []
    sw = len(SIDES)
    for i, a in enumerate(ends):
        b = ends[(i + 1) % len(ends)]
        na = len([l for l in a["road"].find("lanes/laneSection").findall(
            ("left" if a["contact"] == "start" else "right") + "/lane")])
        nb = len([l for l in b["road"].find("lanes/laneSection").findall(
            ("right" if b["contact"] == "start" else "left") + "/lane")])
        lane_a = na if a["contact"] == "start" else -na  # sidewalk left of a's outward direction
        lane_b = -nb if b["contact"] == "start" else nb  # sidewalk right of b's outward direction
        xa, ya = a["ev"].lane_center(lane_a, a["s"])
        xb, yb = b["ev"].lane_center(lane_b, b["s"])
        path = turn_path(Pose(xa, ya, a["out"] + math.pi), Pose(xb, yb, b["out"]))
        pv = xodr.PlanView()
        for geom, pose in path.geometries:
            pv.add_fixed_geometry(geom, pose.x, pose.y, pose.hdg)
        lane = xodr.Lane(xodr.LaneType.sidewalk, a=SIDES[-1][1])
        lane.add_link("predecessor", lane_a)
        lane.add_link("successor", lane_b)
        lane.add_height(0.15, 0.15)
        sec = xodr.LaneSection(0, xodr.Lane())
        sec.add_right_lane(lane)
        lanes = xodr.Lanes()
        lanes.add_lanesection(sec)
        lanes.add_laneoffset(xodr.LaneOffset(0, SIDES[-1][1] / 2))
        road = xodr.Road(next_road, pv, lanes, road_type=int(junction.get("id")), name=f"sidewalk_corner_{i}")
        road.add_predecessor(xodr.ElementType.road, int(a["road"].get("id")),
                             xodr.ContactPoint.start if a["contact"] == "start" else xodr.ContactPoint.end)
        road.add_successor(xodr.ElementType.road, int(b["road"].get("id")),
                           xodr.ContactPoint.start if b["contact"] == "start" else xodr.ContactPoint.end)
        root.findall("road")[-1].addnext(lx(road.get_element()))
        conn = etree.Element("connection", id=str(next_conn), incomingRoad=a["road"].get("id"),
                             connectingRoad=str(next_road), contactPoint="start")
        etree.SubElement(conn, "laneLink", **{"from": str(lane_a), "to": "-1"})
        junction.findall("connection")[-1].addnext(conn)
        made.append(next_road)
        next_road += 1
        next_conn += 1
    assert sw  # silence unused
    return made


def lanes_fixture(path: Path) -> None:
    """Two straight roads: urban side lanes, and a median highway."""
    def road(rid, y, right, left):
        r = etree.Element("road", id=str(rid), length="200", junction="-1", name=f"r{rid}")
        etree.SubElement(r, "link")
        t = etree.SubElement(r, "type", s="0", type="town")
        etree.SubElement(t, "speed", max="13.9", unit="m/s")
        pv = etree.SubElement(r, "planView")
        g = etree.SubElement(pv, "geometry", s="0", x="0", y=str(y), hdg="0", length="200")
        etree.SubElement(g, "line")
        lanes = etree.SubElement(r, "lanes")
        sec = etree.SubElement(lanes, "laneSection", s="0")
        lg, cg, rg = (etree.SubElement(sec, k) for k in ("left", "center", "right"))
        c = etree.SubElement(cg, "lane", id="0", type="none", level="false")
        etree.SubElement(c, "roadMark", sOffset="0", type="solid", weight="standard", color="standard", width="0.15")
        for k, (kind, w, h, m) in enumerate(right, 1):
            rg.append(lane_xml(-k, kind, w, h, m))
        for k, (kind, w, h, m) in reversed(list(enumerate(left, 1))):
            lg.append(lane_xml(k, kind, w, h, m))
        return r

    urban = [("driving", 3.5, 0, "solid"), ("biking", 1.5, 0, "solid"), ("parking", 2.2, 0, "broken"),
             ("curb", 0.15, 0.15, "curb"), ("sidewalk", 2.0, 0.15, "none")]
    rural = [("driving", 3.5, 0, "solid"), ("shoulder", 2.5, 0, "none"), ("border", 0.5, 0, "none")]
    highway = [("median", 1.0, 0, "solid"), ("driving", 3.5, 0, "broken"), ("driving", 3.5, 0, "solid"),
               ("shoulder", 3.0, 0, "none"), ("border", 0.5, 0, "none")]
    root = etree.Element("OpenDRIVE")
    etree.SubElement(root, "header", revMajor="1", revMinor="6", name="lanes", date="1970-01-01T00:00:00")
    root.append(road(1, 0.0, urban, rural))
    root.append(road(2, 40.0, highway, highway))
    etree.ElementTree(root).write(str(path), pretty_print=True, xml_declaration=True, encoding="utf-8")


SIGN_VARIANTS = [  # (label, country, type, subtype, value): which encodings esmini types in OSI and draws
    ("274 sub -1 value 50", "DE", "274", "-1", "50"),
    ("274 sub '' value 50", "DE", "274", "", "50"),
    ("274 lowercase de", "de", "274", "-1", "50"),
    ("350 sub 10", "DE", "350", "10", ""),
    ("350 sub '' value 10", "DE", "350", "", "10"),
    ("206", "DE", "206", "-1", ""),
    ("205", "DE", "205", "-1", ""),
    ("1000001", "OpenDRIVE", "1000001", "-1", ""),
]


def signs_fixture(path: Path) -> None:
    """A straight road with one sign per encoding variant, 12 m apart, facing +s traffic."""
    lanes_fixture(path)
    root = etree.parse(str(path)).getroot()
    road = root.find("road")
    sigs = etree.Element("signals")
    road.insert(list(road).index(road.find("lanes")) + 1, sigs)
    for k, (label, country, typ, sub, val) in enumerate(SIGN_VARIANTS):
        attrs = {"s": str(20 + 12 * k), "t": "-8.5", "id": str(500 + k), "name": label, "dynamic": "no",
                 "orientation": "+", "zOffset": "2.0", "country": country, "type": typ, "subtype": sub,
                 "height": "0.8", "width": "0.8"}
        if val:
            attrs.update(value=val, unit="km/h" if typ == "274" else "")
        etree.SubElement(sigs, "signal", **attrs)
    etree.ElementTree(root).write(str(path), pretty_print=True, xml_declaration=True, encoding="utf-8")


def sign_log(path: Path) -> list[str]:
    """esmini's OSI type messages for the sign fixture (no message = typed)."""
    xosc = still_xosc(path, path.with_suffix(".xosc"), 0.3)
    done = run([esmini_binary("esmini"), "--osc", xosc.resolve(), "--headless", "--fixed_timestep", "0.1",
                "--log_level", "debug"])
    text = done.stdout + done.stderr
    return [line for line in text.splitlines() if "Signal Type" in line or "osgb" in line or "3D model" in line]


def run(cmd, cwd=None, timeout=600) -> subprocess.CompletedProcess:
    return subprocess.run(list(map(str, cmd)), check=False, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def load_log(xodr_path: Path) -> list[str]:
    """esmini warnings and errors while loading the network (odrviewer, headless, 1 s)."""
    done = run([esmini_binary("odrviewer"), "--odr", xodr_path, "--headless", "--duration", "1", "--density", "1"])
    return [line for line in (done.stdout + done.stderr).splitlines()
            if "warn" in line.lower() or "error" in line.lower()]


def traffic(xodr_path: Path, corner_roads: list[int]) -> dict:
    """Our random-lanes scenario on the fixture; are all logged positions on driving lanes?"""
    xosc = xodr_path.with_name("traffic.xosc")
    done = run(["python", "-m", "cli", "--no-history", "scenario", xodr_path, "--vehicles", "30",
                "--duration", "40", "-o", xosc])
    assert done.returncode == 0, done.stderr[-2000:]
    dat = xodr_path.with_name("traffic.dat")
    run([esmini_binary("esmini"), "--osc", xosc.resolve(), "--headless", "--fixed_timestep", "0.05",
         "--record", dat.resolve()])
    run([esmini_binary("dat2csv"), "--file", dat.resolve(), "--extended"])
    text = dat.with_suffix(".csv").read_text()
    root = etree.parse(str(xodr_path)).getroot()
    types = {}
    for road in root.findall("road"):
        for lane in road.iter("lane"):
            types[(int(road.get("id")), int(lane.get("id")))] = lane.get("type")
    rows = list(csv.reader(io.StringIO(text[text.index("time"):] if "time" in text else text)))
    header = [h.strip() for h in rows[0]]
    ri = next(i for i, h in enumerate(header) if h.lower().startswith("road_id") or h.lower() == "roadid")
    li = next(i for i, h in enumerate(header) if h.lower().startswith("lane_id") or h.lower() == "laneid")
    seen, off = 0, []
    for row in rows[1:]:
        if len(row) <= max(ri, li) or not row[ri].strip():
            continue
        road, lane = int(float(row[ri])), int(float(row[li]))
        seen += 1
        kind = types.get((road, lane))
        if kind != "driving" or road in corner_roads:
            off.append((road, lane, kind))
    return {"positions": seen, "off_driving": len(off), "examples": off[:5], "header": header[:12]}


def lights(xodr_path: Path, light_ids: list[tuple[int, str]]) -> dict:
    """Two .xosc: a RoadNetwork controller, and storyboard state actions; screenshots at 1.5 s and 4.5 s."""
    sid, road_id = light_ids[0]
    root = etree.parse(str(xodr_path)).getroot()
    road = next(r for r in root.findall("road") if r.get("id") == road_id)
    sig = next(s for s in road.findall("signals/signal") if s.get("id") == str(sid))
    ev = RoadEval(road)
    x, y, hdg = ev.reference(float(sig.get("s")))
    t = float(sig.get("t"))
    lx_, ly = x - t * math.sin(hdg), y + t * math.cos(hdg)
    facing = hdg + (math.pi if sig.get("orientation") == "-" else 0.0)
    cam = (lx_ - 9 * math.cos(facing), ly - 9 * math.sin(facing), 3.5, facing, 0.05)
    header = ('<?xml version="1.0" encoding="utf-8"?><OpenSCENARIO><FileHeader revMajor="1" revMinor="2" '
              'date="1970-01-01T00:00:00" description="lights" author="roadgen"/><ParameterDeclarations/>'
              '<CatalogLocations/>')
    stop = ('<StopTrigger><ConditionGroup><Condition name="end" delay="0" conditionEdge="none"><ByValueCondition>'
            '<SimulationTimeCondition value="6" rule="greaterThan"/></ByValueCondition></Condition>'
            '</ConditionGroup></StopTrigger>')
    net = f'<RoadNetwork><LogicFile filepath="{xodr_path.resolve()}"/>'
    controller = (net + '<TrafficSignals><TrafficSignalController name="c1">'
                  f'<Phase name="red" duration="3"><TrafficSignalState trafficSignalId="{sid}" state="{LIGHT_RED}"/></Phase>'
                  f'<Phase name="green" duration="3"><TrafficSignalState trafficSignalId="{sid}" state="{LIGHT_GREEN}"/></Phase>'
                  '</TrafficSignalController></TrafficSignals></RoadNetwork>')
    a = OUT / "lights_controller.xosc"
    a.write_text(header + controller + '<Entities/><Storyboard><Init><Actions/></Init>' + stop + '</Storyboard></OpenSCENARIO>')

    def event(name, at, state):
        return (f'<Event name="{name}" priority="parallel"><Action name="{name}"><GlobalAction><InfrastructureAction>'
                f'<TrafficSignalAction><TrafficSignalStateAction name="{sid}" state="{state}"/></TrafficSignalAction>'
                f'</InfrastructureAction></GlobalAction></Action><StartTrigger><ConditionGroup>'
                f'<Condition name="{name}_t" delay="0" conditionEdge="none"><ByValueCondition>'
                f'<SimulationTimeCondition value="{at}" rule="greaterThan"/></ByValueCondition></Condition>'
                f'</ConditionGroup></StartTrigger></Event>')
    story = ('<Story name="s"><Act name="a"><ManeuverGroup name="mg" maximumExecutionCount="1"><Actors '
             'selectTriggeringEntities="false"/><Maneuver name="m">' + event("red", 0.5, LIGHT_RED)
             + event("green", 3.0, LIGHT_GREEN) + '</Maneuver></ManeuverGroup><StartTrigger><ConditionGroup>'
             '<Condition name="go" delay="0" conditionEdge="none"><ByValueCondition><SimulationTimeCondition '
             'value="0" rule="greaterOrEqual"/></ByValueCondition></Condition></ConditionGroup></StartTrigger>'
             '</Act></Story>')
    b = OUT / "lights_storyboard.xosc"
    b.write_text(header + net + '</RoadNetwork><Entities/><Storyboard><Init><Actions/></Init>' + story + stop
                 + '</Storyboard></OpenSCENARIO>')
    out = {"camera": [round(v, 2) for v in cam], "signal": sid}
    for name, xosc in (("controller", a), ("storyboard", b)):
        log = run([esmini_binary("esmini"), "--osc", xosc.resolve(), "--headless", "--fixed_timestep", "0.1"])
        out[name + "_log"] = [l for l in (log.stdout + log.stderr).splitlines() if "warn" in l.lower() or "error" in l.lower()
                              or "signal" in l.lower()][:8]
        for at in (1.5, 4.5):
            screenshot(OUT / f"light_{name}_{at}.png", xosc=xosc, at=at, camera=cam, size=(640, 480))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    junction = OUT / "junction.xodr"
    builder = OpenDriveBuilder(name="spike", junction_margin=12.0)
    builder.build(load_json("tests/fixtures/4way_avenue_x_street.json"))
    builder.write(junction)
    furniture = furnish_junction(junction)
    lanes_xodr = OUT / "lanes.xodr"
    lanes_fixture(lanes_xodr)

    report = {"furniture": {k: v for k, v in furniture.items() if k != "signals"},
              "signals": furniture["signals"]}
    for name, path in (("junction", junction), ("lanes", lanes_xodr)):
        report[name] = {
            "esmini_load": load_log(path),
            "topology": check_topology(path),
            "continuity": check_continuity(path)[:8],
            "lane_drive": [r.describe() for r in drive_lanes(path) if not r.ok][:8],
        }
    report["junction"]["drive_check"] = [r.movement["connecting_road"] for r in drive_movements(junction) if not r.ok]
    signs_xodr = OUT / "signs.xodr"
    signs_fixture(signs_xodr)
    report["signs"] = {"variants": SIGN_VARIANTS, "esmini_log": sign_log(signs_xodr)}
    screenshot(OUT / "signs.png", xodr=signs_xodr, camera=(0.0, -6.0, 2.2, 0.0, 0.0), size=(1280, 500))
    report["traffic"] = traffic(junction, furniture["corner_roads"])
    report["lights"] = lights(junction, furniture["lights"])
    cx, cy = 0.0, 0.0
    shots = {
        "junction_top": {"xodr": junction, "camera": (cx, cy, 70.0, 0.0, math.pi / 2)},
        "junction_oblique": {"xodr": junction, "camera": (cx - 30, cy - 30, 12.0, math.pi / 4, 0.35)},
        "curb_closeup": {"xodr": junction, "camera": (-40.0, -22.0, 3.0, math.pi / 2, 0.25)},
        "signs_avenue": {"xodr": junction, "camera": (15.0, -20.0, 2.5, 0.0, 0.05)},
        "lanes_top": {"xodr": lanes_xodr, "camera": (100.0, 20.0, 110.0, 0.0, math.pi / 2)},
        "lanes_oblique": {"xodr": lanes_xodr, "camera": (60.0, -25.0, 8.0, math.pi / 2.6, 0.3)},
    }
    for name, kw in shots.items():
        screenshot(OUT / f"{name}.png", **kw)
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"wrote {OUT / 'report.json'}")


if __name__ == "__main__":
    main()
