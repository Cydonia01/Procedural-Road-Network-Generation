"""Stage B7: crosswalks, pedestrian phases, 306 / 307, speed signs, curve warnings."""

from __future__ import annotations

import dataclasses
import json
import math

import pytest
from lxml import etree

from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.control import priority_runs
from roadgen.opendrive.furniture import (
    CROSSWALK_GAP,
    STOP_GAP,
    RoadSide,
    curve_warnings,
    kmh,
    speed_signs,
)
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.continuity import RoadEval, _width
from roadgen.validate.hdmap import check_hdmap

HD = HdMapParams(enabled=True)


def _build(graph: RoadGraph, tmp_path, hd: HdMapParams = HD):
    path = tmp_path / "net.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot(), json.loads(path.with_suffix(".roadmap.json").read_text()), b


def _carriageway(road: etree._Element, s: float) -> tuple[float, float]:
    """(right, left) t of the curb lines at s: the edges of the driving, parking, bike and
    shoulder lanes (no lane offset on these roads)."""
    sec = RoadEval(road)._section(s)
    ds = s - float(sec.get("s"))
    out = []
    for side, sign in (("right", -1), ("left", 1)):
        lanes = sorted(sec.findall(f"{side}/lane"), key=lambda lane: abs(int(lane.get("id"))))
        t = 0.0
        for lane in lanes:
            if lane.get("type") not in ("driving", "parking", "biking", "shoulder", "median"):
                break
            t += _width(lane, ds)
        out.append(sign * t)
    return out[0], out[1]


ALL_ZEBRAS = dataclasses.replace(HD, control=dataclasses.replace(HD.control, zebras="all"))


def test_crosswalks_span_the_carriageway_with_stop_lines_behind(tmp_path):
    path, root, sidecar, _ = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path, ALL_ZEBRAS)
    assert check_hdmap(path) == []
    walks = [o for o in root.iter("object") if o.get("type") == "crosswalk"]
    assert len(walks) == 4 and sidecar["junctions"][0]["crosswalks"] == 4
    for walk in walks:
        road = walk.getparent().getparent()
        s, t = float(walk.get("s")), float(walk.get("t"))
        vs = [float(c.get("v")) for c in walk.findall("outlines/outline/cornerLocal")]
        right, left = _carriageway(road, s)
        assert t + min(vs) == pytest.approx(right, abs=1e-6) and t + max(vs) == pytest.approx(left, abs=1e-6)
        marking = walk.find("markings/marking")  # a zebra: no lights here
        assert (float(marking.get("lineLength")), float(marking.get("spaceLength"))) == (0.5, 0.5)
        depth = float(marking.get("width"))
        assert depth in (3.0, 4.0)
        length = float(road.get("length"))
        at_end = abs(s - length) < abs(s)
        far_edge = abs((length if at_end else 0.0) - s) + depth / 2
        assert far_edge == pytest.approx(CROSSWALK_GAP + depth)
        for line in road.findall("objects/object"):
            if line.get("name") == "stopLine" and (abs(float(line.get("s")) - length) < 10) == at_end:
                gap = abs((length if at_end else 0.0) - float(line.get("s"))) - 0.25 - far_edge
                assert gap == pytest.approx(STOP_GAP)
    # a 350-10 per crosswalk and direction of travel
    zebra_signs = [s for s in root.iter("signal") if s.get("type") == "350"]
    assert len(zebra_signs) == 8 and {s.get("subtype") for s in zebra_signs} == {"10"}


@pytest.mark.parametrize("zebras, profile, expected", [("dense_avenues", "urban", 2), ("dense_avenues", "suburban", 0),
                                                      ("all", "urban", 4), ("none", "urban", 0)])
def test_zebras_only_on_dense_avenue_arms_by_default(tmp_path, zebras, profile, expected):
    hd = dataclasses.replace(HD, control=dataclasses.replace(HD.control, zebras=zebras),
                             cross_section=dataclasses.replace(HD.cross_section, profile=profile))
    path, root, sidecar, _ = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path, hd)
    assert check_hdmap(path) == []
    walks = [o for o in root.iter("object") if o.get("type") == "crosswalk"]
    assert len(walks) == expected
    if zebras == "dense_avenues":
        roads = {w.getparent().getparent() for w in walks}
        assert all(len([lane for lane in r.find("lanes/laneSection").findall("right/lane")
                        if lane.get("type") == "driving"]) == 2 for r in roads)  # the 2+2 avenue's arms
    assert len([s for s in root.iter("signal") if s.get("type") == "350"]) == 2 * expected


def test_no_zebras_at_street_junctions_and_crossings_at_every_lit_arm(tmp_path):
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    for e in g.edges.values():
        e.road_class = "street"
    _, root, _, _ = _build(g, tmp_path)
    assert not [o for o in root.iter("object") if o.get("type") == "crosswalk"]
    g.nodes[1].control = "signal"
    _, root, _, _ = _build(g, tmp_path)
    assert len([o for o in root.iter("object") if o.get("type") == "crosswalk"]) == 4


def test_crossings_at_lights_have_two_edge_lines_and_no_sign(tmp_path):
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    for e in g.edges.values():
        e.road_class, e.lanes_forward, e.lanes_backward, e.lane_width = "avenue", 2, 2, 3.5
    path, root, _, _ = _build(g, tmp_path)
    assert check_hdmap(path) == []
    walk = next(o for o in root.iter("object") if o.get("type") == "crosswalk")
    markings = walk.findall("markings/marking")
    assert len(markings) == 2 and {float(m.get("width")) for m in markings} == {0.3}
    assert {float(m.get("spaceLength")) for m in markings} == {0.0}
    us = sorted({float(c.get("u")) for c in walk.findall("outlines/outline/cornerLocal")})
    assert us[1] - us[0] == pytest.approx(4.0 - 0.3)  # along the 4 m crossing's two edges
    assert not [s for s in root.iter("signal") if s.get("type") == "350"]


def test_no_crosswalk_without_sidewalks_or_room(tmp_path):
    hd = dataclasses.replace(HD, cross_section=dataclasses.replace(HD.cross_section, profile="rural"))
    _, root, _, _ = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path, hd)
    assert not [o for o in root.iter("object") if o.get("type") == "crosswalk"]


def _t_one_way_stem(profile: str = "urban") -> RoadGraph:
    """Two-way avenue through a junction whose third arm is a one-way avenue leaving it:
    only one opposite pair arrives, so the lights have one phase group."""
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-200.0, 0.0), 3: (200.0, 0.0), 4: (0.0, 200.0)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 2, 1, "avenue", 1, 1, speed_limit=13.9, cross_section=profile))
    g.add_edge(Edge(2, 1, 3, "avenue", 1, 1, speed_limit=13.9, cross_section=profile))
    g.add_edge(Edge(3, 1, 4, "avenue", 2, 0, speed_limit=13.9, cross_section=profile))
    return g


def test_single_phase_lights_with_crosswalks_get_a_pedestrian_phase(tmp_path):
    path, _, sidecar, _ = _build(_t_one_way_stem(), tmp_path)
    assert check_hdmap(path) == []
    (plan,) = sidecar["signal_plans"]
    kinds = [p["kind"] for p in plan["phases"]]
    assert kinds == ["green", "yellow", "all_red", "pedestrian"]
    walk = plan["phases"][-1]
    assert set(walk["states"].values()) == {"on;off;off"} and walk["duration"] >= 10.0


def test_single_phase_lights_without_crosswalks_become_a_priority_road(tmp_path):
    path, root, sidecar, _ = _build(_t_one_way_stem("rural"), tmp_path)
    assert check_hdmap(path) == []
    assert "signal_plans" not in sidecar and not root.findall("controller")
    j = next(j for j in sidecar["junctions"] if j["approaches"] and len(j["approaches"]) == 3)
    assert j["control"] == "priority" and len(j["priority_roads"]) == 2


def test_priority_runs_through_consecutive_junctions():
    # roads 1 -> J10 -> 2 -> J20 -> 3 -> J30 -> 4, all priority, two-way
    majors = {10: ((1, "end"), (2, "start")), 20: ((2, "end"), (3, "start")), 30: ((3, "end"), (4, "start"))}
    road_ends = {(1, "end"): 10, (2, "start"): 10, (2, "end"): 20, (3, "start"): 20, (3, "end"): 30,
                 (4, "start"): 30}
    both = {r: {1, -1} for r in (1, 2, 3, 4)}
    runs = priority_runs(majors, road_ends, both)
    assert sorted(runs) == sorted([([(1, 1), (2, 1), (3, 1), (4, 1)], [10, 20, 30]),
                                   ([(4, -1), (3, -1), (2, -1), (1, -1)], [30, 20, 10])])
    one_way = {**both, 3: {1}}
    assert priority_runs(majors, road_ends, one_way) == [([(1, 1), (2, 1), (3, 1), (4, 1)], [10, 20, 30])]
    assert priority_runs({10: majors[10]}, road_ends, both) == []  # one junction: 301 is enough


def test_306_307_along_an_avenue_through_three_junctions(tmp_path):
    g = RoadGraph()
    xs = [0.0, 150.0, 300.0, 450.0, 600.0]
    for k, x in enumerate(xs):
        g.add_node(Node(k + 1, x, 0.0))
    for k in range(3):  # side streets at the three inner nodes
        g.add_node(Node(10 + k, xs[k + 1], 150.0))
        g.add_edge(Edge(10 + k, k + 2, 10 + k, "street", 1, 1))
    for k in range(4):
        g.add_edge(Edge(k + 1, k + 1, k + 2, "avenue", 1, 1, speed_limit=13.9))
    path, root, _, b = _build(g, tmp_path, dataclasses.replace(
        HD, control=dataclasses.replace(HD.control, stop_share=0.0)))
    assert check_hdmap(path) == []
    assert b.priority_runs == 2  # one per direction
    codes = [s.get("type") for s in root.iter("signal")]
    assert codes.count("306") == 2
    assert codes.count("301") == 2  # only before the first junction of each run
    assert codes.count("307") == 0  # the avenue ends in dead ends, not at a junction


def test_speed_signs_follow_the_posted_speed():
    lanes = lambda s: [(-1, "driving", 0.0, -3.5, 0.0), (1, "driving", 0.0, 3.5, 0.0)]
    limit = lambda s: 13.9 if s < 200 else 16.7
    profile = [(0.0, 13.9), (120.0, 9.0), (150.0, 13.9), (200.0, 16.7)]  # a 32 km/h corner at 120-150 m
    forward = speed_signs(RoadSide(1, 1, 400.0, lanes, True), profile, limit)
    assert [(round(r.s), r.value) for r in forward] == [(10, 50), (120, 30), (150, 50), (200, 60)]
    assert all(r.orientation == "+" and r.t < 0 and r.subtype == "" for r in forward)
    backward = speed_signs(RoadSide(1, -1, 400.0, lanes, True), profile, limit)
    assert [(round(r.s), r.value) for r in backward] == [(390, 60), (200, 50), (150, 30), (120, 50)]
    gentle = [(0.0, 13.9), (120.0, 12.0), (150.0, 13.9)]  # 43 km/h: less than 10 below 50, not posted
    assert [r.value for r in speed_signs(RoadSide(1, 1, 400.0, lanes, False), gentle, lambda s: 13.9)] == []
    assert kmh(22.2) == 80 and kmh(13.9) == 50 and kmh(9.0) == 30


def test_curve_warnings_precede_advisory_corners():
    lanes = lambda s: [(-1, "driving", 0.0, -3.5, 0.0), (1, "driving", 0.0, 3.5, 0.0)]
    corners = [(300.0, 340.0, math.radians(60), 8.0), (700.0, 740.0, -math.radians(60), 8.0),
               (800.0, 830.0, math.radians(40), 9.0)]
    fwd = curve_warnings(RoadSide(1, 1, 1000.0, lanes, False), corners, lambda s: 13.9, 1.5)
    lead = (13.9 ** 2 - 8.0 ** 2) / 3.0
    assert [(r.type, r.subtype) for r in fwd] == [("103", "10"), ("105", "20")]  # left; then right-left
    assert fwd[0].s == pytest.approx(300.0 - lead) and fwd[1].s == pytest.approx(700.0 - lead)
    back = curve_warnings(RoadSide(1, -1, 1000.0, lanes, False), corners, lambda s: 13.9, 1.5)
    # against s the order and the turn sides flip: 830-800 (right), then 740-700 (left) within 200 m
    assert [(r.type, r.subtype) for r in back] == [("105", "20"), ("103", "20")]
    assert all(r.orientation == "-" and r.t > 0 for r in back)


def test_built_roads_carry_speed_signs_matching_their_type_records(tmp_path):
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (300.0, 0.0), 3: (600.0, 0.0)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1, speed_limit=13.9))
    g.add_edge(Edge(2, 2, 3, "avenue", 1, 1, speed_limit=16.7))
    path, root, _, _ = _build(g, tmp_path)
    assert check_hdmap(path) == []
    road = root.find("road")
    types = [(float(t.get("s")), float(t.find("speed").get("max"))) for t in road.findall("type")]
    speeds = {(s.get("orientation"), int(s.get("value"))) for s in road.iter("signal") if s.get("type") == "274"}
    change = next(s for s, _ in types[1:])
    signs = [s for s in road.iter("signal") if s.get("type") == "274"]
    forward = [s for s in signs if s.get("orientation") == "+"]
    assert any(abs(float(s.get("s")) - change) < 1e-3 and s.get("value") == "60" for s in forward)
    assert ("-", 50) in speeds


def test_crossing_lines_follow_the_grade(tmp_path):
    # two avenues crossing (lights) on a 4 % slope: the crossings' two edge lines lie
    # 1.85 m either side of the object's centre, so their corners follow the grade
    g = RoadGraph()
    for nid, (x, y, z) in {1: (0.0, 0.0, 0.0), 2: (-200.0, 0.0, -8.0), 3: (200.0, 0.0, 8.0),
                           4: (0.0, 200.0, 0.0), 5: (0.0, -200.0, 0.0)}.items():
        g.add_node(Node(nid, x, y, z=z))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, "avenue", 1, 1, speed_limit=13.9))
    path, root, _, _ = _build(g, tmp_path)
    assert check_hdmap(path) == []
    checked = 0
    for walk in (o for o in root.iter("object") if o.get("type") == "crosswalk"):
        ev = RoadEval(walk.getparent().getparent())
        s = float(walk.get("s"))
        t = float(walk.get("t"))

        def surface(u, v, ev=ev, s=s, t=t):
            return ev.elevation(s + u)[0] + (t + v) * math.sin(ev.superelevation(s + u))

        for c in walk.findall("outlines/outline/cornerLocal"):
            u, v = float(c.get("u")), float(c.get("v"))
            assert float(c.get("z")) == pytest.approx(surface(u, v) - surface(0.0, 0.0) + 0.02, abs=1e-4)
        checked += abs(ev.elevation(s)[1]) > 0.01
    assert checked >= 2  # the climbing arms
