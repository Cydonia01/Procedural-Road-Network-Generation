"""Stage B4: HD road marks (German catalogue), the hdmap validator, marks in plot-xodr."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from lxml import etree

from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.marks import RoadMarkParams
from roadgen.opendrive.plot import mark_lines
from roadgen.validate.continuity import RoadEval, check_continuity
from roadgen.validate.hdmap import check_hdmap, check_hdmap_tree
from roadgen.validate.topology import check_topology

HD = HdMapParams(enabled=True)
MARKS = RoadMarkParams()
# Streets with a centre line in every profile, to test its pattern and zones.
STREET_LINES = dataclasses.replace(HD, marks=dataclasses.replace(MARKS, no_street_centre=()))


def _build(graph: RoadGraph, tmp_path, hd: HdMapParams = HD):
    path = tmp_path / "net.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot()


def _road(root: etree._Element, name: str) -> etree._Element:
    return next(r for r in root.findall("road") if r.get("name") == name)


def _lanes_at(road: etree._Element, s: float) -> dict[int, tuple[str, etree._Element]]:
    """Lane id -> (lane type, the road mark record in force at station s)."""
    ev = RoadEval(road)
    sec = ev._section(s)
    ds = s - float(sec.get("s"))
    out = {}
    for lane in sec.iter("lane"):
        marks = [m for m in lane.findall("roadMark") if float(m.get("sOffset")) <= ds + 1e-9]
        out[int(lane.get("id"))] = (lane.get("type", "none"), marks[-1] if marks else None)
    return out


def _kind(mark) -> str:
    return "unmarked" if mark is None else mark.get("type")


def _t_street(length: float = 200.0, cls: str = "street", lanes: tuple[int, int] = (1, 1),
              speed: float = 13.9) -> RoadGraph:
    """A road of a class between two T junctions, length [m] apart."""
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (length, 0.0), 3: (-150.0, 0.0), 4: (0.0, 150.0), 5: (length, -150.0),
           6: (length + 150.0, 0.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 2, cls, *lanes, speed_limit=speed))
    g.add_edge(Edge(2, 3, 1, cls, *lanes, speed_limit=speed))
    g.add_edge(Edge(3, 1, 4, "street", 1, 1))
    g.add_edge(Edge(4, 2, 5, "street", 1, 1))
    g.add_edge(Edge(5, 2, 6, cls, *lanes, speed_limit=speed))
    return g


def _check_zones(road: etree._Element, zone: float) -> None:
    """Lane lines (driving lanes inside the outermost) and a broken-or-solid centre line are
    solid with laneChange none within zone [m] of a junction end in their travel direction,
    broken elsewhere; sampled every metre, whatever the lane sections."""
    length = RoadEval(road).length
    end = road.find("link/successor[@elementType='junction']") is not None
    start = road.find("link/predecessor[@elementType='junction']") is not None
    for s in np.arange(0.5, length, 1.0):
        lanes = _lanes_at(road, s)
        for lane_id, (kind, mark) in lanes.items():
            if kind != "driving":
                continue
            outer = lanes.get(lane_id + (1 if lane_id > 0 else -1), ("none",))[0] != "driving"
            if outer:
                assert _kind(mark) == "solid", (lane_id, s)
                continue
            in_zone = (end and s >= length - zone) if lane_id < 0 else (start and s <= zone)
            assert _kind(mark) == ("solid" if in_zone else "broken"), (lane_id, s, length)
            assert mark.get("laneChange") == ("none" if in_zone else "both")


def test_street_centre_line_broken_with_solid_approaches(tmp_path):
    path, root = _build(_t_street(), tmp_path, STREET_LINES)
    assert check_topology(path) == [] and check_continuity(path) == [] and check_hdmap(path) == []
    road = _road(root, "edge_1")
    length = RoadEval(road).length
    for s, expected in ((5.0, "solid"), (19.5, "solid"), (20.5, "broken"), (length / 2, "broken"),
                        (length - 19.5, "solid"), (length - 1.0, "solid")):
        mark = _lanes_at(road, s)[0][1]
        assert _kind(mark) == expected, s
        assert mark.get("color") == "white" and float(mark.get("width")) == pytest.approx(0.12)
    broken = next(m for m in road.iter("roadMark") if m.get("type") == "broken")
    line = broken.find("type/line")
    assert (float(line.get("length")), float(line.get("space"))) == (3.0, 6.0)  # Z 340 in towns
    # edge line solid on the outer driving lane; side lanes (curbs included) unmarked
    lanes = _lanes_at(road, length / 2)
    assert _kind(lanes[-1][1]) == "solid" and _kind(lanes[1][1]) == "solid"
    assert all(mark is None for kind, mark in lanes.values() if kind not in ("driving", "none"))


def test_avenue_2_2_solid_centre_and_lane_line_zones(tmp_path):
    path, root = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    assert check_hdmap(path) == [] and check_continuity(path) == []
    for road in root.findall("road"):
        if road.get("junction") != "-1" or not road.get("name").startswith("edge_"):
            continue
        rights = [lane for lane in road.find("lanes/laneSection").findall("right/lane")
                  if lane.get("type") == "driving"]
        if len(rights) == 2:  # the avenue: Z 295 centre line all along
            length = RoadEval(road).length
            assert all(_kind(_lanes_at(road, s)[0][1]) == "solid" for s in np.arange(0.5, length, 1.0))
            _check_zones(road, 30.0)
        else:
            _check_zones(road, 20.0)


def test_zones_across_short_lane_sections(tmp_path):
    # a 2+2 avenue that gains a lane each way 45 m before the junction: the zone covers
    # the taper's section and the one after it
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (400.0, 0.0), 3: (-150.0, 0.0), 4: (0.0, 150.0), 5: (400.0, -150.0),
           6: (550.0, 0.0), 7: (355.0, 0.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 7, "avenue", 2, 2, speed_limit=16.7))
    g.add_edge(Edge(2, 3, 1, "avenue", 2, 2, speed_limit=16.7))
    g.add_edge(Edge(3, 1, 4, "street", 1, 1))
    g.add_edge(Edge(4, 2, 5, "street", 1, 1))
    g.add_edge(Edge(5, 2, 6, "avenue", 3, 3, speed_limit=16.7))
    g.add_edge(Edge(6, 7, 2, "avenue", 3, 3, speed_limit=16.7))
    path, root = _build(g, tmp_path)
    assert check_hdmap(path) == [] and check_continuity(path) == []
    road = next(r for r in root.findall("road") if r.get("name") == "edges_1_6")
    assert len(road.findall("lanes/laneSection")) >= 3
    _check_zones(road, 30.0)


def test_short_road_is_solid_all_along(tmp_path):
    _, root = _build(_t_street(50.0), tmp_path, STREET_LINES)
    road = _road(root, "edge_1")
    assert RoadEval(road).length < 40.0
    marks = [m.get("type") for m in road.findall("lanes/laneSection/center/lane/roadMark")]
    assert marks == ["solid"]


def test_highway_median_edge_lines_and_double_line_at_junctions(tmp_path):
    path, root = _build(_t_street(400.0, "highway", (2, 2), 22.2), tmp_path)
    assert check_hdmap(path) == [] and check_continuity(path) == []
    road = _road(root, "edge_1")
    length = RoadEval(road).length
    mid = _lanes_at(road, length / 2)
    assert _kind(mid[0][1]) == "none"  # the median's own edge lines replace the centre line
    assert mid[-1][0] == "median" and _kind(mid[-1][1]) == "solid" and _kind(mid[1][1]) == "solid"
    assert float(mid[-1][1].get("width")) == pytest.approx(0.15)
    lane_line = mid[-2][1]
    assert _kind(lane_line) == "broken"
    assert float(lane_line.find("type/line").get("length")) == 6.0  # 6 / 12 m on highways
    for s in (0.5, length - 0.5):  # the median is 0 m wide at the junctions
        at = _lanes_at(road, s)
        assert _kind(at[0][1]) == "solid solid" and _kind(at[-1][1]) == "none"
    _check_zones(road, 50.0)


def test_one_way_centre_lane_is_an_edge_line(tmp_path):
    path, root = _build(load_json("tests/fixtures/4way_oneway.json"), tmp_path)
    assert check_hdmap(path) == []
    for road in root.findall("road"):
        if road.get("junction") != "-1":
            continue
        left = [lane for lane in road.iter("lane") if int(lane.get("id")) > 0 and lane.get("type") == "driving"]
        if not left:
            assert {m.get("type") for m in road.findall("lanes/laneSection/center/lane/roadMark")} == {"solid"}


def test_no_marks_inside_junctions_and_validator_catches_them(tmp_path):
    path, root = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    connecting = [r for r in root.findall("road") if r.get("junction") != "-1"]
    assert connecting and all(r.find(".//roadMark") is None for r in connecting)
    tree = etree.parse(str(path))
    lane = next(r for r in tree.getroot().findall("road") if r.get("junction") != "-1").find(".//lane")
    etree.SubElement(lane, "roadMark", sOffset="0", type="solid")
    assert any("connecting road" in e for e in check_hdmap_tree(tree))


def test_validator_catches_unordered_mark_offsets(tmp_path):
    path, _ = _build(_t_street(), tmp_path, STREET_LINES)
    tree = etree.parse(str(path))
    centre = tree.getroot().find("road/lanes/laneSection/center/lane")
    marks = centre.findall("roadMark")
    assert len(marks) >= 2
    marks[1].set("sOffset", "0")
    assert any("not ascending" in e for e in check_hdmap_tree(tree))


def test_marks_off_keep_the_plain_marks(tmp_path):
    hd = dataclasses.replace(HD, marks=dataclasses.replace(MARKS, enabled=False))
    _, root = _build(_t_street(), tmp_path, hd)
    marks = list(_road(root, "edge_1").iter("roadMark"))
    assert {m.get("color") for m in marks} == {"standard"}
    assert {m.get("type") for m in marks} == {"solid"}  # 1+1: centre and edge lines; none on curbs


@pytest.mark.parametrize("kw", [{"width": (0.12, 0.0, 0.15)}, {"broken_town": (3.0,)},
                                {"no_change_length": (20.0, -1.0, 50.0)}, {"solid_centre_lanes": 0},
                                {"no_street_centre": ("town",)}])
def test_mark_params_are_checked(kw):
    with pytest.raises(ValueError):
        RoadMarkParams(**kw)


def test_plot_draws_broken_marks_dash_by_dash(tmp_path):
    _, root = _build(_t_street(), tmp_path, STREET_LINES)
    lines = mark_lines(_road(root, "edge_1"))
    dashes = [p for kind, _, p in lines if kind == "broken"]
    assert dashes and all(np.hypot(*(p[-1] - p[0])) <= 3.0 + 1e-6 for p in dashes)
    assert not any(kind == "curb" for kind, _, _ in lines)


def _centre_kinds(root: etree._Element, name: str = "edge_1") -> set[str]:
    return {m.get("type") for m in _road(root, name).findall("lanes/laneSection/center/lane/roadMark")}


@pytest.mark.parametrize("profile, expected", [("urban", {"none"}), ("suburban", {"none"}),
                                               ("rural", {"solid", "broken"}), ("none", {"solid", "broken"})])
def test_streets_have_no_centre_line_in_urban_and_suburban_profiles(tmp_path, profile, expected):
    hd = dataclasses.replace(HD, cross_section=dataclasses.replace(HD.cross_section, profile=profile))
    path, root = _build(_t_street(), tmp_path, hd)
    assert check_hdmap(path) == []
    assert _centre_kinds(root) == expected


def test_street_centre_line_follows_the_edge_profile(tmp_path):
    g = _t_street()
    g.edges[1].cross_section = "rural"  # the rest stays urban
    _, root = _build(g, tmp_path)
    assert _centre_kinds(root) == {"solid", "broken"}
    assert _centre_kinds(root, "edge_3") == {"none"}


def test_avenues_keep_their_centre_line_in_urban_profile(tmp_path):
    _, root = _build(_t_street(400.0, "avenue", (1, 1), 16.7), tmp_path)
    assert _centre_kinds(root) == {"solid", "broken"}


def test_curb_lanes_carry_no_mark(tmp_path):
    _, root = _build(_t_street(), tmp_path)
    curbs = [lane for lane in root.iter("lane") if lane.get("type") == "curb"]
    assert curbs and all(lane.find("roadMark") is None for lane in curbs)
