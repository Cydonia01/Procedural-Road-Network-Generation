"""Stage B7: single-phase lights, 306 / 307, speed signs, curve warnings, lines on a grade."""

from __future__ import annotations

import dataclasses
import json
import math

import pytest
from lxml import etree

from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.control import priority_runs
from roadgen.opendrive.furniture import (
    RoadSide,
    curve_warnings,
    kmh,
    speed_signs,
)
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.continuity import RoadEval
from roadgen.validate.hdmap import check_hdmap

HD = HdMapParams(enabled=True)


def _build(graph: RoadGraph, tmp_path, hd: HdMapParams = HD):
    path = tmp_path / "net.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot(), json.loads(path.with_suffix(".roadmap.json").read_text()), b


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


def test_single_phase_lights_become_a_priority_road(tmp_path):
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
    lanes = lambda s: [(-1, "driving", 0.0, -3.5), (1, "driving", 0.0, 3.5)]
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
    lanes = lambda s: [(-1, "driving", 0.0, -3.5), (1, "driving", 0.0, 3.5)]
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


def test_stop_lines_follow_the_grade(tmp_path):
    # two avenues crossing (lights) on a 4 % slope: the stop lines' corners lie on the
    # road surface (elevation and superelevation), lifted 2 cm
    g = RoadGraph()
    for nid, (x, y, z) in {1: (0.0, 0.0, 0.0), 2: (-200.0, 0.0, -8.0), 3: (200.0, 0.0, 8.0),
                           4: (0.0, 200.0, 0.0), 5: (0.0, -200.0, 0.0)}.items():
        g.add_node(Node(nid, x, y, z=z))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, "avenue", 1, 1, speed_limit=13.9))
    path, root, _, _ = _build(g, tmp_path)
    assert check_hdmap(path) == []
    checked = 0
    for line in (o for o in root.iter("object") if o.get("name") == "stopLine"):
        ev = RoadEval(line.getparent().getparent())
        s = float(line.get("s"))
        t = float(line.get("t"))

        def surface(u, v, ev=ev, s=s, t=t):
            return ev.elevation(s + u)[0] + (t + v) * math.sin(ev.superelevation(s + u))

        for c in line.findall("outlines/outline/cornerLocal"):
            u, v = float(c.get("u")), float(c.get("v"))
            assert float(c.get("z")) == pytest.approx(surface(u, v) - surface(0.0, 0.0) + 0.02, abs=1e-4)
        checked += abs(ev.elevation(s)[1]) > 0.01
    assert checked >= 2  # the climbing arms
