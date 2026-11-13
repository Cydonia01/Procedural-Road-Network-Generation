"""Stage B1: cross-sections (medians, area profiles), junction trims, lane roles, repair."""

from __future__ import annotations

import math

import pytest

from cli import main
from roadgen.generators.repair import repair_short_edges, short_edges, stub_roads
from roadgen.geometry.cross_section import (
    CrossSectionParams,
    half_widths,
    loop_edges,
    outer_half_width,
)
from roadgen.geometry.junction_space import (
    Arm,
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
from tests.grids import GRID_CLI
from tests.test_repair import two_junctions

CS = CrossSectionParams()


def test_medians_by_class_and_profile():
    assert CS.median_width(Edge(1, 1, 2, "street", 1, 1)) == 0.0
    assert CS.median_width(Edge(1, 1, 2, "highway", 2, 2)) == 2.0
    assert CS.median_width(Edge(1, 1, 2, "highway", 2, 0)) == 0.0  # one-way: no median
    assert CS.median_width(Edge(1, 1, 2, "highway", 1, 1)) == 0.0  # 1 + 1: too narrow
    assert CS.median_width(Edge(1, 1, 2, "highway", 2, 2, cross_section="none")) == 0.0
    assert CS.median_width(Edge(1, 1, 2, "highway", 2, 2, cross_section="rural")) == 2.0
    with pytest.raises(ValueError, match="unknown cross-section profile"):
        CS.median_width(Edge(7, 1, 2, "highway", 2, 2, cross_section="downtown"))
    with pytest.raises(ValueError, match="unknown cross-section profile"):
        CrossSectionParams(profile="downtown")


def test_loop_edges():
    g = RoadGraph(metadata={"roundabouts": [{"ring": [1, 2, 3]}], "culdesacs": [{"node": 9, "loop": [5, 6]}]})
    for nid in (1, 2, 3, 4, 5, 6):
        g.add_node(Node(nid, float(nid), 0.0))
    for eid, (a, b) in enumerate([(1, 2), (2, 3), (3, 4), (5, 6)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 0))
    assert loop_edges(g) == {1, 2, 4}


def test_half_widths_are_the_driving_lanes_and_half_the_median():
    street = Edge(1, 1, 2, "street", 1, 1)
    assert half_widths(street, CS, 1, 1) == pytest.approx((3.5, 3.5))  # no side lanes (polish N1)
    highway = Edge(1, 1, 2, "highway", 2, 2)
    assert half_widths(highway, CS, 2, 2) == pytest.approx((8.0, 8.0))  # 7 + 1 of the median
    assert half_widths(highway, CS, 2, 2, at_junction=True) == pytest.approx((7.0, 7.0))  # it has ended
    assert half_widths(highway, CS, 2, 2, loop=True) == pytest.approx((7.0, 7.0))
    one_way = Edge(1, 1, 2, "street", 2, 0)  # reference line on its left edge
    assert outer_half_width(one_way, CS) == pytest.approx(7.0)


def _cross(theta_deg: float, width: float) -> list[Arm]:
    t = math.radians(theta_deg)
    headings = [0.0, t, math.pi, math.pi + t]
    return [Arm(k, h, width, width) for k, h in enumerate(headings, 1)]


def test_trims():
    # 90 deg: the road edges cross 3.5 m out.
    assert list(junction_trims(_cross(90, 3.5), 2.0).values()) == pytest.approx([3.5 + 2.0] * 4)
    # 60 deg: the acute corners need (b + a cos 60) / sin 60
    trims = junction_trims(_cross(60, 3.5), 2.0)
    assert max(trims.values()) == pytest.approx((3.5 + 3.5 * 0.5) / math.sin(math.radians(60)) + 2.0)


def test_cross_section_trims_equal_the_driving_lanes_without_pockets():
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    plain = node_trims(g, 1)
    arms = [Arm(a.edge_id, a.heading, a.left, a.right) for a in node_arms(g, 1)]
    assert plain == junction_trims(arms, 2.0)
    assert node_trims(g, 1, cross_section=CS) == plain  # no side lanes, medians end before it


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


def test_lane_roles_link_a_median_across_a_lane_drop():
    # right: a median, then two driving lanes, the outer one dropped at s = 60 (taper 40-60)
    layout = LaneLayout(
        100.0,
        right=((WidthPiece(0, 100, 1.0, 1.0),),
               (WidthPiece(0, 100, 3.5, 3.5),),
               (WidthPiece(0, 40, 3.5, 3.5), WidthPiece(40, 60, 3.5, 0.0))),
        left=((WidthPiece(0, 100, 3.5, 3.5),),),
        right_kinds=("median", "driving", "driving"),
    )
    sections = layout.sections()
    assert [s.roles("right") for s in sections] == [
        (("median", 1), ("driving", 2), ("driving", 3)),
        (("median", 1), ("driving", 2), ("driving", 3)),
        (("median", 1), ("driving", 2)),
    ]
    assert layout.driving_offsets(100.0) == {-2: -2.75, 1: 1.75}
    root = make_lane_sections(layout).get_element()
    last = root.findall("laneSection")[-1]
    assert last.find("right/lane[@id='-1']").get("type") == "median"
    assert last.find("right/lane[@id='-2']/link/predecessor").get("id") == "-2"
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
    """Without side lanes the cross-section widens trims only by turn pockets: an edge
    that fits with the driving lanes fits with HD maps too."""
    g = two_junctions(16.0)
    assert short_edges(g) == [] and short_edges(g, cross_section=CS) == []
    assert repair_short_edges(g, cross_section=CS, keep_stubs=frozenset(stub_roads(g))) == 0
    assert set(g.nodes) == {1, 2, 3, 4, 5, 6}


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
    assert hd.enabled  # the default since B10
    off = HdMapParams.from_config(DEFAULTS, enabled=False)
    assert not off.enabled and off.active_cross_section is None
    on = HdMapParams.from_config(DEFAULTS, seed=4, enabled=True)
    from roadgen.geometry.cross_section import TurnLaneParams

    assert on.seed == 4 and on.active_cross_section == CrossSectionParams(turn_lanes=TurnLaneParams())


def test_pipeline_hd_fits_and_builds(tmp_path):
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", *GRID_CLI, "--allow-traps", "--hd",
                 "-o", str(out)]) == 0
    off = tmp_path / "off"
    assert main(["--no-history", "pipeline", "lsystem", *GRID_CLI, "--allow-traps", "--no-hd",
                 "-o", str(off)]) == 0
    assert out.with_suffix(".xodr").read_bytes() != off.with_suffix(".xodr").read_bytes()  # marks, signs, ...


def test_collapse_keeps_bridges_on_their_edges():
    g = two_junctions(8.0)  # too short even without side lanes; edge 5 runs from x = 8 to x = 108
    g.edges[5].bridges = [(80.0, 95.0)]
    assert repair_short_edges(g, cross_section=CS, keep_stubs=frozenset(stub_roads(g))) == 1
    length = g.edge_length(5)  # node 2 merged into node 1 at x = 4: 104 m now
    assert length == pytest.approx(104.0)
    (s0, s1), = g.edges[5].bridges
    assert (s0, s1) == pytest.approx((80.0 * 1.04, 95.0 * 1.04)) and s1 <= length
    assert g.validate() == []
