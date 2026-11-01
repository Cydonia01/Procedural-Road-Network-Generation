"""Stage B1: cross-sections, junction trims with curb returns, lane roles, repair."""

from __future__ import annotations

import math

import pytest

from cli import main
from roadgen.generators.repair import repair_short_edges, short_edges, stub_roads
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    edge_cross_section,
    loop_edges,
    outer_half_width,
    side_half_widths,
)
from roadgen.geometry.junction_space import (
    Arm,
    junction_corners,
    junction_trims,
    node_arms,
    node_trims,
)
from roadgen.geometry.planar import Pose
from roadgen.geometry.profiles import LaneLayout, WidthPiece
from roadgen.graph.io import graph_from_dict, graph_to_dict, load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.junctions import RoadEnd
from roadgen.opendrive.lanes import make_lane_sections
from tests.test_repair import two_junctions

CS = CrossSectionParams()


def test_profiles_by_class():
    street = edge_cross_section(Edge(1, 1, 2, "street", 1, 1), CS)
    assert [s.kind for s in street.right] == ["parking", "curb", "sidewalk"] == [s.kind for s in street.left]
    assert street.median == 0.0 and street.radius == 3.0
    highway = edge_cross_section(Edge(1, 1, 2, "highway", 2, 2), CS)
    assert [s.kind for s in highway.right] == ["shoulder"] and highway.median == 2.0 and highway.radius == 7.6
    assert edge_cross_section(Edge(1, 1, 2, "highway", 2, 0), CS).median == 0.0  # one-way: no median
    assert edge_cross_section(Edge(1, 1, 2, "highway", 1, 1), CS).median == 0.0  # 1 + 1: too narrow
    none = edge_cross_section(Edge(1, 1, 2, "avenue", 2, 2, cross_section="none"), CS)
    assert none.right == none.left == () and none.radius == 0.0
    rural = edge_cross_section(Edge(1, 1, 2, "street", 1, 1), CrossSectionParams(profile="rural"))
    assert [(s.kind, s.width) for s in rural.right] == [("shoulder", 1.0)]
    with pytest.raises(ValueError, match="unknown cross-section profile"):
        edge_cross_section(Edge(7, 1, 2, "street", 1, 1, cross_section="downtown"), CS)
    with pytest.raises(ValueError, match="unknown side lane kind"):
        CrossSectionParams(urban_street=(("kerb", 0.2, 0.15),))


def test_loop_edges_keep_curb_and_sidewalk_outside_and_curb_inside():
    loop = edge_cross_section(Edge(1, 1, 2, "avenue", 2, 0), CS, loop=True)
    assert [s.kind for s in loop.right] == ["curb", "sidewalk"]  # no bike or parking lane on a ring
    assert [s.kind for s in loop.left] == ["curb"]  # the island side
    g = RoadGraph(metadata={"roundabouts": [{"ring": [1, 2, 3]}], "culdesacs": [{"node": 9, "loop": [5, 6]}]})
    for nid in (1, 2, 3, 4, 5, 6):
        g.add_node(Node(nid, float(nid), 0.0))
    for eid, (a, b) in enumerate([(1, 2), (2, 3), (3, 4), (5, 6)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 0))
    assert loop_edges(g) == {1, 2, 4}


def test_side_widths_at_junctions():
    street = Edge(1, 1, 2, "street", 1, 1)
    # along the road: 3.5 driving + 2.2 parking to the curb, + 0.15 curb + 2.0 sidewalk outside
    assert side_half_widths(street, CS, 1, 1) == pytest.approx((5.7, 5.7, 7.85, 7.85))
    # at a junction parking has ended (curb extension)
    assert side_half_widths(street, CS, 1, 1, at_junction=True) == pytest.approx((3.5, 3.5, 5.65, 5.65))
    highway = Edge(1, 1, 2, "highway", 2, 2)
    assert side_half_widths(highway, CS, 2, 2) == pytest.approx((11.0, 11.0, 11.0, 11.0))  # 7 + 1 + 3
    assert side_half_widths(highway, CS, 2, 2, at_junction=True, ramp=True) == pytest.approx((7.0,) * 4)
    # urban and suburban shoulders end before every junction; rural ones go through the corner
    assert side_half_widths(highway, CS, 2, 2, at_junction=True) == pytest.approx((7.0,) * 4)
    rural = CrossSectionParams(profile="rural")
    assert side_half_widths(highway, rural, 2, 2, at_junction=True) == pytest.approx((10.0,) * 4)
    assert side_half_widths(highway, rural, 2, 2, at_junction=True, ramp=True) == pytest.approx((7.0,) * 4)
    avenue = Edge(1, 1, 2, "avenue", 2, 2)
    assert side_half_widths(avenue, CS, 2, 2, at_junction=True)[0] == pytest.approx(8.5)  # bike lane stays
    assert side_half_widths(avenue, CS, 2, 2, at_junction=True, at_loop=True)[0] == pytest.approx(7.0)
    one_way = Edge(1, 1, 2, "street", 2, 0)  # reference line on its left edge
    assert side_half_widths(one_way, CS, 2, 0) == pytest.approx((9.2, 2.2, 11.35, 4.35))
    assert outer_half_width(one_way, CS) == pytest.approx(11.35)


def _cross(theta_deg: float, width: float, outer: float, radius: float, length: float = 100.0) -> list[Arm]:
    t = math.radians(theta_deg)
    headings = [0.0, t, math.pi, math.pi + t]
    return [Arm(k, h, width, width, outer, outer, radius, length) for k, h in enumerate(headings, 1)]


def test_trims_with_curb_returns():
    # 90 deg: curb lines cross 3.5 m out, the 3 m return adds 3 / tan 45 = 3 m.
    trims = junction_trims(_cross(90, 3.5, 5.65, 3.0), 2.0, CS.max_corner_share)
    assert list(trims.values()) == pytest.approx([3.5 + 3.0 + 2.0] * 4)
    # 60 deg: the acute corners need (b + a cos 60) / sin 60 + r / tan 30
    trims = junction_trims(_cross(60, 3.5, 5.65, 3.0), 2.0, CS.max_corner_share)
    acute = (3.5 + 3.5 * 0.5) / math.sin(math.radians(60)) + 3.0 / math.tan(math.radians(30))
    assert max(trims.values()) == pytest.approx(acute + 2.0)
    # Without radii only the outer edges (sidewalks) must not overlap: 5.65 at 90 deg.
    assert list(junction_trims(_cross(90, 3.5, 5.65, 0.0), 2.0).values()) == pytest.approx([5.65 + 2.0] * 4)


def test_curb_returns_shrink_on_short_arms_and_skip_bends():
    corners = junction_corners(_cross(90, 3.5, 5.65, 3.0, length=5.0), 0.3)
    assert [c.radius for c in corners] == pytest.approx([1.5] * 4)  # 0.3 * 5 m * tan 45
    assert {c.wanted for c in corners} == {3.0}
    assert junction_corners(_cross(90, 3.5, 5.65, 3.0, length=100.0), 0.3)[0].radius == 3.0
    bend = [Arm(1, 0.0, 3.5, 3.5, 5.65, 5.65, 3.0), Arm(2, math.radians(100), 3.5, 3.5, 5.65, 5.65, 3.0)]
    assert junction_corners(bend) == []  # two roads meeting are one road continuing


def test_without_cross_section_trims_are_unchanged():
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    plain = node_trims(g, 1)
    arms = [Arm(a.edge_id, a.heading, a.left, a.right) for a in node_arms(g, 1)]
    assert plain == junction_trims(arms, 2.0)
    wide = node_trims(g, 1, cross_section=CS)
    assert all(wide[e] > plain[e] for e in plain)


def test_road_end_explicit_lanes():
    pose = Pose(0.0, 0.0, 0.0)
    plain = RoadEnd(1, 1, "end", pose, 2, 2, 3.5, 13.9)
    same = RoadEnd(1, 1, "end", pose, 2, 2, 3.5, 13.9, lane_t=((-2, -5.25), (-1, -1.75), (1, 1.75), (2, 5.25)))
    assert plain.in_lanes == same.in_lanes == [-1, -2] and plain.out_lanes == same.out_lanes == [1, 2]
    for lane in (-2, -1, 1, 2):
        assert plain.lane_pose(lane, True) == same.lane_pose(lane, True)
    # A 2 m median: driving lanes are -2, -3 / 2, 3 at the end, 1 m further out
    median = RoadEnd(1, 1, "start", pose, 2, 2, 3.5, 13.9,
                     lane_t=((-3, -6.25), (-2, -2.75), (2, 2.75), (3, 6.25)))
    assert median.in_lanes == [2, 3] and median.out_lanes == [-2, -3]
    assert median.lane_pose(2, True).y == pytest.approx(2.75)


def test_lane_roles_link_side_lanes_across_a_lane_drop():
    # right: two driving lanes, the outer one dropped at s = 60 (taper 40-60), and a sidewalk
    layout = LaneLayout(
        100.0,
        right=((WidthPiece(0, 100, 3.5, 3.5),),
               (WidthPiece(0, 40, 3.5, 3.5), WidthPiece(40, 60, 3.5, 0.0)),
               (WidthPiece(0, 100, 2.0, 2.0),)),
        left=((WidthPiece(0, 100, 3.5, 3.5),),),
        right_kinds=("driving", "driving", "sidewalk"),
    )
    sections = layout.sections()
    assert [s.roles("right") for s in sections] == [
        (("driving", 1), ("driving", 2), ("sidewalk", 3)),
        (("driving", 1), ("driving", 2), ("sidewalk", 3)),
        (("driving", 1), ("sidewalk", 3)),
    ]
    assert layout.driving_offsets(100.0) == {-1: -1.75, 1: 1.75}
    assert layout.driving_offsets(0.0) == {-1: -1.75, -2: -5.25, 1: 1.75}
    root = make_lane_sections(layout).get_element()
    last = root.findall("laneSection")[-1]
    sidewalk = last.find("right/lane[@id='-2']")  # was -3 before the drop
    assert sidewalk.find("link/predecessor").get("id") == "-3"
    middle = root.findall("laneSection")[1]
    assert middle.find("right/lane[@id='-3']/link/successor").get("id") == "-2"
    with pytest.raises(ValueError, match="not contiguous"):
        LaneLayout(10.0, right=((WidthPiece(0, 5, 3.5, 3.5),), (WidthPiece(0, 10, 3.5, 3.5),)),
                   left=()).sections()


def test_plain_layout_sections_have_no_roles():
    layout = LaneLayout(50.0, right=((WidthPiece(0, 50, 3.5, 3.5),),), left=((WidthPiece(0, 50, 3.5, 3.5),),))
    (sec,) = layout.sections()
    assert sec.right_roles == sec.left_roles == ()


def test_graph_json_round_trip_with_overrides():
    g = two_junctions(60.0)
    plain = graph_to_dict(g)
    assert all("cross_section" not in e and "control" not in n for e, n in zip(plain["edges"], plain["nodes"]))
    g.edges[1].cross_section = "rural"
    g.nodes[1].control = "signal"
    data = graph_to_dict(g)
    back = graph_from_dict(data)
    assert back.edges[1].cross_section == "rural" and back.nodes[1].control == "signal"


def test_repair_with_cross_sections():
    """B9 (prompt 13): an edge only the side lanes make too short is never collapsed or
    removed; its road ends become bare (side lanes end before the junction, no curb
    return) until it fits."""
    g = two_junctions(16.0)  # today: trims 5.5 + 5.5; with sidewalks and curb returns 8.5 + 8.5
    assert short_edges(g) == [] and short_edges(g, cross_section=CS) == [1]
    assert repair_short_edges(g, cross_section=CS, keep_stubs=frozenset(stub_roads(g))) == 0
    assert 1 in g.edges and set(g.nodes) == {1, 2, 3, 4, 5, 6}
    bare = g.metadata["no_side_lanes"]
    assert bare and {n for n, _ in bare} <= {1, 2}
    assert short_edges(g, cross_section=CS) == []
    network_alignment(g, cross_section=CS)


def test_repair_keeps_existing_stubs():
    g = two_junctions(15.0)  # a 4 m stub road today
    known = frozenset(stub_roads(g))
    assert known == {1}
    g.edges[1].cross_section = "none"  # and no wider with cross-sections
    for e in (2, 3, 4, 5):
        g.edges[e].cross_section = "none"
    assert repair_short_edges(g, cross_section=CS, keep_stubs=known) == 0


def test_hd_params_from_config():
    from roadgen.config import DEFAULTS

    hd = HdMapParams.from_config(DEFAULTS)
    assert not hd.enabled and hd.active_cross_section is None
    on = HdMapParams.from_config(DEFAULTS, seed=4, enabled=True)
    from roadgen.geometry.cross_section import TurnLaneParams

    assert on.seed == 4 and on.active_cross_section == CrossSectionParams(turn_lanes=TurnLaneParams())


def test_pipeline_hd_fits_and_builds(tmp_path):
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", "--preset", "grid", "--allow-traps", "--hd",
                 "-o", str(out)]) == 0
    off = tmp_path / "off"
    assert main(["--no-history", "pipeline", "lsystem", "--preset", "grid", "--allow-traps", "-o", str(off)]) == 0
    assert out.with_suffix(".xodr").read_bytes() != off.with_suffix(".xodr").read_bytes()  # wider trims


def test_fit_also_repairs_dead_end_stubs():
    # A T junction whose stem is a 9 m dead end: 3.5 m of road today (trim 5.5),
    # 0.5 m with sidewalks and curb returns (trim 8.5): repaired only with dead_ends.
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-100.0, 0.0), 3: (100.0, 0.0), 4: (0.0, 9.0)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate([(2, 1), (1, 3), (1, 4)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    assert stub_roads(g, cross_section=CS) == []
    assert stub_roads(g, cross_section=CS, dead_ends=True) == [3]
    known = frozenset(stub_roads(g, dead_ends=True))
    assert known == {3}  # 3.5 m today: a stub already, so left alone ...
    assert repair_short_edges(g, cross_section=CS, keep_stubs=known, dead_ends=True) == 0
    g.nodes[4].y = 12.0  # ... but at 12 m it has 6.5 m today and 3.5 m with sidewalks:
    # its side lanes make it a stub, so road ends at its junction become bare (B9, prompt 13):
    # at a T the stem's trim comes from the through road, whose ends there lose theirs
    assert repair_short_edges(g, cross_section=CS, keep_stubs=frozenset(stub_roads(g, dead_ends=True)),
                              dead_ends=True) == 0
    assert 3 in g.edges and g.metadata["no_side_lanes"] == [[1, 1], [1, 2]]
    assert stub_roads(g, cross_section=CS, dead_ends=True) == []


def test_collapse_keeps_bridges_on_their_edges():
    g = two_junctions(8.0)  # too short even without side lanes; edge 5 runs from x = 8 to x = 108
    g.edges[5].bridges = [(80.0, 95.0)]
    assert repair_short_edges(g, cross_section=CS, keep_stubs=frozenset(stub_roads(g))) == 1
    length = g.edge_length(5)  # node 2 merged into node 1 at x = 4: 104 m now
    assert length == pytest.approx(104.0)
    (s0, s1), = g.edges[5].bridges
    assert (s0, s1) == pytest.approx((80.0 * 1.04, 95.0 * 1.04)) and s1 <= length
    assert g.validate() == []


def test_bare_road_ends_build(tmp_path):
    """The block the repair kept with bare ends builds with HD maps and validates; no
    sidewalk reaches either junction (0 m wide at the road's ends, if any is left)."""
    from lxml import etree

    from roadgen.opendrive.builder import OpenDriveBuilder
    from roadgen.validate.continuity import RoadEval, _width, check_continuity
    from roadgen.validate.hdmap import check_hdmap
    from roadgen.validate.topology import check_topology

    g = two_junctions(18.0)  # 7 m of road today, 1 m with side lanes, 6.7 m with bare ends (curbs stay)
    hd = HdMapParams(enabled=True)
    assert repair_short_edges(g, cross_section=hd.active_cross_section, keep_stubs=frozenset(stub_roads(g))) == 0
    assert g.metadata["no_side_lanes"] and stub_roads(g, cross_section=hd.active_cross_section) == []
    path = tmp_path / "bare.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(g)
    b.write(path)
    assert check_topology(path) == [] and check_continuity(path) == [] and check_hdmap(path) == []
    road = next(r for r in etree.parse(str(path)).getroot().findall("road")
                if r.get("id") == str(b.edge_to_road[1]))
    ev = RoadEval(road)
    sections = road.findall("lanes/laneSection")
    for sec, ds in ((sections[0], 0.0), (sections[-1], ev.length - float(sections[-1].get("s")))):
        walks = [lane for lane in sec.iter("lane") if lane.get("type") == "sidewalk"]
        assert all(_width(lane, ds) == pytest.approx(0.0, abs=1e-6) for lane in walks)
