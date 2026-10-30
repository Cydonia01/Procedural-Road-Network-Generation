"""Stage B2: side lanes and medians in the built roads, plot-xodr."""

from __future__ import annotations

import pytest
from lxml import etree

from cli import main
from roadgen.geometry.alignment import network_alignment
from roadgen.geometry.cross_section import CrossSectionParams, loop_edges
from roadgen.geometry.profiles import lane_layout
from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.scenario.xosc_builder import ScenarioBuilder
from roadgen.validate.continuity import RoadEval, check_continuity
from roadgen.validate.topology import check_topology

CS = CrossSectionParams()
HD = HdMapParams(enabled=True)


def _rm_available() -> bool:
    try:
        from roadgen.sim.esmini import esmini_library

        esmini_library("esminiRMLib")
        return True
    except FileNotFoundError:
        return False


def _build(graph: RoadGraph, tmp_path, name: str = "net", hd: HdMapParams = HD):
    path = tmp_path / f"{name}.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot()


def _types(section: etree._Element, side: str) -> list[str]:
    lanes = sorted(section.findall(f"{side}/lane"), key=lambda lane: abs(int(lane.get("id"))))
    return [lane.get("type") for lane in lanes]


def _road(root: etree._Element, edge_name: str) -> etree._Element:
    return next(r for r in root.findall("road") if r.get("name") == edge_name)


def test_avenue_side_lanes_and_curb_extensions(tmp_path):
    path, root = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    assert check_topology(path) == [] and check_continuity(path) == []
    avenue = next(r for r in root.findall("road") if r.get("junction") == "-1"
                  and len(r.find("lanes/laneSection").findall("right/lane")) == 6)
    sections = avenue.findall("lanes/laneSection")
    middle = sections[len(sections) // 2]
    assert _types(middle, "right") == ["driving", "driving", "biking", "parking", "curb", "sidewalk"]
    assert _types(middle, "left") == ["driving", "driving", "biking", "parking", "curb", "sidewalk"]
    # parking ends before the junction (the road's end at the junction has no parking)
    end = sections[-1] if avenue.find("link/successor") is not None else sections[0]
    assert "parking" not in _types(end, "right")
    # curbs and sidewalks are raised 0.15 m
    curb = next(lane for lane in middle.findall("right/lane") if lane.get("type") == "curb")
    assert float(curb.find("height").get("inner")) == pytest.approx(0.15)
    # side lanes carry no road marks yet (B4); driving lanes keep theirs
    assert all(lane.find("roadMark") is None for lane in middle.iter("lane") if lane.get("type") not in
               ("driving", "none"))
    # only driving lanes have junction connections
    for conn in root.iter("connection"):
        road = next(r for r in root.findall("road") if r.get("id") == conn.get("incomingRoad"))
        for link in conn.findall("laneLink"):
            types = {lane.get("type") for lane in road.iter("lane") if lane.get("id") == link.get("from")}
            assert types == {"driving"}


def test_side_lanes_keep_their_role_across_a_lane_drop(tmp_path):
    # a 2+2 avenue becomes 1+1 at a bend node: the sidewalk keeps its links while its id shifts
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (400.0, 0.0)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 2, "avenue", 2, 2))
    g.add_edge(Edge(2, 2, 3, "avenue", 1, 1))
    path, root = _build(g, tmp_path)
    assert check_continuity(path) == []
    road = root.find("road")
    ids = [int(lane.get("id")) for sec in road.findall("lanes/laneSection")
           for lane in sec.findall("right/lane") if lane.get("type") == "sidewalk"]
    assert ids[0] == -6 and ids[-1] == -5  # outside two, then one driving lane, bike, parking, curb
    for sec in road.findall("lanes/laneSection")[1:]:
        sidewalk = next(lane for lane in sec.findall("right/lane") if lane.get("type") == "sidewalk")
        pred = sidewalk.find("link/predecessor")
        assert pred is not None
        prev = sec.getprevious()
        assert prev.find(f"right/lane[@id='{pred.get('id')}']").get("type") == "sidewalk"


def test_one_way_road_has_side_lanes_on_both_edges(tmp_path):
    path, root = _build(load_json("tests/fixtures/4way_oneway.json"), tmp_path)
    assert check_continuity(path) == []
    one_way = next(r for r in root.findall("road") if r.get("junction") == "-1"
                   and not [lane for lane in r.find("lanes/laneSection").findall("left/lane")
                            if lane.get("type") == "driving"])
    sec = one_way.findall("lanes/laneSection")[len(one_way.findall("lanes/laneSection")) // 2]
    assert _types(sec, "right")[:2] == ["driving", "driving"]
    assert _types(sec, "left") == ["parking", "curb", "sidewalk"]  # positive ids on the left edge


def _highway(kind_end: str = "junction") -> RoadGraph:
    """A 2+2 highway between two T junctions (or ramp nodes), 400 m apart."""
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (400.0, 0.0), 3: (-200.0, 0.0), 4: (0.0, 200.0), 5: (400.0, -200.0),
           6: (600.0, 0.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y, kind_end if nid in (1, 2) else "auto"))
    g.add_edge(Edge(1, 1, 2, "highway", 2, 2, speed_limit=22.2))
    g.add_edge(Edge(2, 3, 1, "highway", 2, 2, speed_limit=22.2))
    g.add_edge(Edge(3, 1, 4, "street", 1, 1))
    g.add_edge(Edge(4, 2, 5, "street", 1, 1))
    g.add_edge(Edge(5, 2, 6, "highway", 2, 2, speed_limit=22.2))
    return g


def test_median_shifts_driving_lanes_and_ends_before_junctions(tmp_path):
    path, root = _build(_highway(), tmp_path)
    assert check_topology(path) == [] and check_continuity(path) == []
    road = _road(root, "edge_1")
    sections = road.findall("lanes/laneSection")
    middle = sections[len(sections) // 2]
    assert _types(middle, "right") == ["median", "driving", "driving", "shoulder"]
    assert _types(middle, "left") == ["median", "driving", "driving", "shoulder"]
    # at both junctions the median lane is 0 m wide (driving lanes keep ids -2, -3) and the
    # urban shoulder has narrowed to 0
    for end, s_end in ((sections[0], 0.0), (sections[-1], RoadEval(road).length)):
        assert _types(end, "right")[:3] == ["median", "driving", "driving"]
        ds = s_end - float(end.get("s"))
        for kind in ("median", "shoulder"):
            lane = next((x for x in end.findall("right/lane") if x.get("type") == kind), None)
            if lane is not None:
                w = lane.find("width")
                a, b, c, d = (float(w.get(k)) for k in "abcd")
                assert a + b * ds + c * ds ** 2 + d * ds ** 3 == pytest.approx(0.0, abs=1e-6)
    # junction connections start from the driving lanes at the road's ends
    for conn in root.iter("connection"):
        if conn.get("incomingRoad") != road.get("id"):
            continue
        end = sections[0] if conn.get("contactPoint") == "start" else sections[-1]
        types = {lane.get("id"): lane.get("type") for lane in end.iter("lane")}
        assert all(types[link.get("from")] == "driving" for link in conn.findall("laneLink"))


def test_shoulders_at_junctions_by_profile(tmp_path):
    g = _highway()
    ca = network_alignment(g, cross_section=CS).of_edge(1)
    junctions = frozenset({1, 2})
    urban = lane_layout(g, ca, CS, junctions, frozenset()).sections()
    rural = lane_layout(g, ca, CrossSectionParams(profile="rural"), junctions, frozenset()).sections()
    assert "shoulder" in {k for k, _ in urban[len(urban) // 2].roles("right")}
    def shoulder_at_end(sections) -> float:
        sec = sections[-1]
        piece = next((p for (kind, _), p in zip(sec.roles("right"), sec.right) if kind == "shoulder"), None)
        return 0.0 if piece is None else piece.width(sec.s1)

    assert shoulder_at_end(urban) == pytest.approx(0.0)  # urban: ends before the junction
    assert shoulder_at_end(rural) == pytest.approx(3.0)  # rural: through the corner
    ramp = _highway("ramp")
    ca = network_alignment(ramp, cross_section=CS).of_edge(1)
    rural_ramp = lane_layout(ramp, ca, CrossSectionParams(profile="rural"), junctions, frozenset()).sections()
    assert shoulder_at_end(rural_ramp) == pytest.approx(0.0)  # every profile at a ramp node


def test_parking_stops_over_bridges(tmp_path):
    g = RoadGraph()
    g.add_node(Node(1, 0.0, 0.0))
    g.add_node(Node(2, 300.0, 0.0))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1, bridges=[(100.0, 200.0)]))
    ca = network_alignment(g, cross_section=CS).of_edge(1)
    layout = lane_layout(g, ca, CS)
    for sec in layout.sections():
        mid = 0.5 * (sec.s0 + sec.s1)
        parked = "parking" in {k for k, _ in sec.roles("right")}
        if 100.0 <= mid <= 200.0:
            assert not parked
        if mid < 90.0 or mid > 210.0:
            assert parked


def test_loops_have_curb_and_sidewalk_outside_and_curb_inside(tmp_path):
    out = tmp_path / "rb"
    assert main(["--no-history", "pipeline", "junction", "--preset", "roundabout", "--allow-traps", "--hd",
                 "-o", str(out)]) == 0
    g = load_json(out.with_suffix(".json"))
    root = etree.parse(str(out.with_suffix(".xodr"))).getroot()
    ring = loop_edges(g) & {int(e) for e in __import__("json").loads(
        out.with_suffix(".roadmap.json").read_text())["edge_to_road"]}
    assert ring
    sidecar = __import__("json").loads(out.with_suffix(".roadmap.json").read_text())
    road_id = str(sidecar["edge_to_road"][str(min(ring))])
    road = next(r for r in root.findall("road") if r.get("id") == road_id)
    sec = road.findall("lanes/laneSection")[0]
    assert _types(sec, "right")[-2:] == ["curb", "sidewalk"]
    assert _types(sec, "left") == ["curb"]
    assert "biking" not in _types(sec, "right") and "parking" not in _types(sec, "right")
    assert sidecar["cross_sections"][str(min(ring))] == "urban"


def test_scenario_spawns_only_on_driving_lanes(tmp_path):
    path, root = _build(_highway(), tmp_path)
    roads = {r.get("id"): RoadEval(r) for r in root.findall("road")}

    def lane_type(road: int, lane: int, s: float) -> str:
        sec = roads[str(road)]._section(s)
        return next(x.get("type") for x in sec.iter("lane") if x.get("id") == str(lane))

    sb = ScenarioBuilder.from_files(path)
    assert sb.slots and sb.lane_graph.number_of_nodes()
    for sl in sb.slots:  # driving over the whole slot
        for s in (sl.s0 + 1e-3, 0.5 * (sl.s0 + sl.s1), sl.s1 - 1e-3):
            assert lane_type(sl.road, sl.lane, s) == "driving"


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
@pytest.mark.parametrize("args", [
    ["lsystem", "--preset", "grid"],
    ["junction", "--preset", "roundabout"],
    ["junction", "--preset", "diamond"],
], ids=["grid", "roundabout", "diamond"])
def test_drive_check_with_side_lanes(tmp_path, args):
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", *args, "--allow-traps", "--hd", "--drive-check", "-o", str(out)]) == 0


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_esmini_drives_past_a_median(tmp_path):
    from roadgen.sim.drive_check import drive_lanes, drive_movements

    path, _ = _build(_highway(), tmp_path)
    assert all(r.ok for r in drive_movements(path))
    lanes = drive_lanes(path)
    assert lanes and all(r.ok for r in lanes)


def test_plot_xodr(tmp_path):
    path, _ = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    png = tmp_path / "lanes.png"
    assert main(["--no-history", "plot-xodr", str(path), str(png)]) == 0
    assert png.stat().st_size > 1000


def test_pipeline_writes_lane_plot_and_cross_section_flag(tmp_path):
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", "--preset", "grid", "--allow-traps", "--hd",
                 "--cross-section", "rural", "-o", str(out)]) == 0
    assert (tmp_path / "net_hd.png").exists()
    root = etree.parse(str(out.with_suffix(".xodr"))).getroot()
    kinds = {lane.get("type") for lane in root.iter("lane")}
    assert "shoulder" in kinds and "sidewalk" not in kinds


def test_lane_runs_never_end_at_full_width_inside_a_road():
    from roadgen.geometry.profiles import WidthPiece, _close_runs

    # a run ending at full width at s = 36 (a cut dropped what followed) gets a 5 m taper
    pieces = [WidthPiece(10, 15, 0.0, 2.2), WidthPiece(15, 36, 2.2, 2.2)]
    closed = _close_runs(pieces, 52.6, 5.0)
    assert closed[-1] == WidthPiece(31, 36, 2.2, 0.0)
    # at the road's end a lane may end at full width (a dead end, or it continues into a junction)
    assert _close_runs([WidthPiece(0, 52.6, 2.2, 2.2)], 52.6, 5.0) == [WidthPiece(0, 52.6, 2.2, 2.2)]
    # a run starting at full width inside the road widens from 0 instead
    assert _close_runs([WidthPiece(20, 52.6, 2.2, 2.2)], 52.6, 5.0)[0] == WidthPiece(20, 25, 0.0, 2.2)


def test_lane_step_follows_the_lane_on_curves():
    from roadgen.sim.drive_check import STEP, _lane_step

    xml = ('<road id="1" length="100" junction="-1"><planView><geometry s="0" x="0" y="0" hdg="0" length="100">'
           '<arc curvature="0.05"/></geometry></planView><lanes><laneSection s="0"><left><lane id="1" '
           'type="driving"><width sOffset="0" a="3.5" b="0" c="0" d="0"/></lane></left><center><lane id="0" '
           'type="none"/></center><right><lane id="-1" type="driving"><width sOffset="0" a="3.5" b="0" c="0" '
           'd="0"/></lane></right></laneSection></lanes></road>')
    evals = {1: RoadEval(etree.fromstring(xml))}
    inner = _lane_step(evals, (1, 1, 10.0), (1, 1, 10.0 + STEP))  # left lane: inside of a left curve
    outer = _lane_step(evals, (1, -1, 10.0), (1, -1, 10.0 + STEP))
    assert inner == pytest.approx(STEP * (1 - 0.05 * 1.75), rel=1e-3)
    assert outer == pytest.approx(STEP * (1 + 0.05 * 1.75), rel=1e-3)
    assert _lane_step(None, (1, 1, 0.0), (1, 1, STEP)) == STEP


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_drive_check_still_catches_a_jump(tmp_path):
    from roadgen.sim.drive_check import drive_movements

    path, root = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    conn = next(r for r in root.findall("road") if r.get("junction") != "-1")
    for g in conn.findall("planView/geometry"):  # move one connecting road 0.3 m sideways
        g.set("y", str(float(g.get("y")) + 0.3))
    etree.ElementTree(root).write(str(path))
    results = {r.movement["connecting_road"]: r for r in drive_movements(path)}
    jumped = results[int(conn.get("id"))]
    # 0.3 m sideways within a 0.5 m step: sqrt(0.5^2 + 0.3^2) - 0.5 = 0.083 m longer
    assert not jumped.ok and jumped.max_jump == pytest.approx(0.083, abs=0.01)
    assert sum(not r.ok for r in results.values()) == 1
