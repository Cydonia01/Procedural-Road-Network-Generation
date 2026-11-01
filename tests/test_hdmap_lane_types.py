"""Stage B9: the hdmap validator's lane-type checks on broken fixtures."""

from __future__ import annotations

import json
from typing import Optional

from lxml import etree

from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.hdmap import check_hdmap_tree

HD = HdMapParams(enabled=True)


def _build(tmp_path, graph: Optional[RoadGraph] = None):
    path = tmp_path / "net.xodr"
    b = OpenDriveBuilder(hd=HD)
    b.build(graph if graph is not None else load_json("tests/fixtures/4way_avenue_x_street.json"))
    b.write(path)
    return path, json.loads(path.with_suffix(".roadmap.json").read_text())


def _errors(path, sidecar, edit) -> list[str]:
    tree = etree.parse(str(path))
    edit(tree.getroot(), sidecar)
    return check_hdmap_tree(tree, sidecar)


def _roads(root):
    return {r.get("id"): r for r in root.findall("road")}


def test_built_lane_types_pass(tmp_path):
    path, sidecar = _build(tmp_path)
    assert check_hdmap_tree(etree.parse(str(path)), sidecar) == []


def test_sidewalk_linked_to_a_driving_lane_is_caught(tmp_path):
    """A corner road's sidewalk turned into a driving lane: its junction laneLink now
    joins a sidewalk of the incoming road to a driving lane."""
    path, sidecar = _build(tmp_path)
    corner = str(sidecar["corner_roads"][0]["road"])

    def edit(root, _):
        road = _roads(root)[corner]
        for lane in road.iter("lane"):
            if lane.get("type") == "sidewalk":
                lane.set("type", "driving")

    errors = _errors(path, sidecar, edit)
    assert any(f"connecting road {corner}" in e and "(sidewalk)" in e and "(driving)" in e for e in errors)


def _avenue_cross() -> RoadGraph:
    """Two 2+2 avenues crossing, 200 m arms: the turn pockets give the arms several
    lane sections."""
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-200.0, 0.0), 3: (200.0, 0.0), 4: (0.0, 200.0), 5: (0.0, -200.0)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, "avenue", 2, 2, speed_limit=13.9))
    return g


def test_lane_type_change_between_lane_sections_is_caught(tmp_path):
    path, sidecar = _build(tmp_path, _avenue_cross())
    edited = []

    def edit(root, _):
        for road in root.findall("road"):
            secs = road.findall("lanes/laneSection")
            if road.get("junction") == "-1" and len(secs) > 1:
                lane = next(x for x in secs[-1].iter("lane") if x.get("type") == "sidewalk")
                lane.set("type", "parking")
                edited.append(road.get("id"))
                return

    errors = _errors(path, sidecar, edit)
    assert edited and any(f"road {edited[0]} section" in e and "(parking)" in e and "of section" in e
                          for e in errors)


def test_lane_type_change_across_a_road_link_is_caught(tmp_path):
    """A corner road's successor is the outgoing road itself (a road-to-road link)."""
    path, sidecar = _build(tmp_path)
    corner = str(sidecar["corner_roads"][0]["road"])

    def edit(root, _):
        next(x for x in _roads(root)[corner].iter("lane") if x.get("type") == "sidewalk").set("type", "parking")

    assert any(f"road {corner} section" in e and "(parking)" in e and "of road" in e
               for e in _errors(path, sidecar, edit))


def test_movement_on_a_non_driving_connecting_road_is_caught(tmp_path):
    path, sidecar = _build(tmp_path)
    m = sidecar["movements"][0]

    def edit(root, _):
        road = _roads(root)[str(m["connecting_road"])]
        next(x for x in road.iter("lane") if x.get("id") != "0").set("type", "shoulder")

    errors = _errors(path, sidecar, edit)
    assert any(f"connecting road {m['connecting_road']} of a movement" in e for e in errors)
