"""Stage B2: medians in the built roads (no side lanes since polish N1), plot-xodr, drive-check."""

from __future__ import annotations


import pytest
from lxml import etree

from cli import main
from roadgen.geometry.cross_section import CrossSectionParams
from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.continuity import RoadEval, _width, check_continuity
from roadgen.validate.topology import check_topology
from tests.grids import GRID_CLI

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


def _lane_width(road: etree._Element, kind: str, side: str, s: float) -> float:
    """Width [m] of the first lane of a kind on one side at station s (0 if there is none)."""
    sec = RoadEval(road)._section(s)
    lane = next((x for x in sec.findall(f"{side}/lane") if x.get("type") == kind), None)
    return 0.0 if lane is None else _width(lane, s - float(sec.get("s")))


def _road(root: etree._Element, edge_name: str) -> etree._Element:
    return next(r for r in root.findall("road") if r.get("name") == edge_name)


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
    assert _types(middle, "right") == ["median", "driving", "driving"]  # no shoulder (polish N1)
    assert _types(middle, "left") == ["median", "driving", "driving"]
    # at both junctions the median lane is 0 m wide (driving lanes keep ids -2, -3)
    length = RoadEval(road).length
    for end, s_end in ((sections[0], 0.0), (sections[-1], length)):
        assert _types(end, "right")[:3] == ["median", "driving", "driving"]
        assert _lane_width(road, "median", "right", s_end) == pytest.approx(0.0, abs=1e-6)
    assert _lane_width(road, "median", "right", length / 2) == pytest.approx(1.0)
    # junction connections start from the driving lanes at the road's ends
    for conn in root.iter("connection"):
        if conn.get("incomingRoad") != road.get("id"):
            continue
        end = sections[0] if conn.get("contactPoint") == "start" else sections[-1]
        types = {lane.get("id"): lane.get("type") for lane in end.iter("lane")}
        assert all(types[link.get("from")] == "driving" for link in conn.findall("laneLink"))


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
@pytest.mark.parametrize("args", [
    ["lsystem", *GRID_CLI],
    ["junction", "--preset", "roundabout"],
    ["junction", "--preset", "diamond"],
], ids=["grid", "roundabout", "diamond"])
def test_drive_check_with_hd_maps(tmp_path, args):
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


def test_pipeline_writes_lane_plot_and_only_driven_lanes(tmp_path):
    """Polish N1: the roads carry driving lanes, turn pockets and medians only; nothing
    else is a lane (no sidewalks, curbs, parking, bike lanes or shoulders)."""
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", *GRID_CLI, "--allow-traps", "--hd",
                 "--cross-section", "rural", "--lane-plot", "-o", str(out)]) == 0
    assert (tmp_path / "net_hd.png").exists()
    root = etree.parse(str(out.with_suffix(".xodr"))).getroot()
    assert {lane.get("type") for lane in root.iter("lane")} <= {"driving", "median", "none"}
    assert not list(root.iter("height"))


def test_lane_step_follows_the_lane_on_curves():
    from roadgen.sim.drive_check import STEP, _lane_steps

    xml = ('<road id="1" length="100" junction="-1"><planView><geometry s="0" x="0" y="0" hdg="0" length="100">'
           '<arc curvature="0.05"/></geometry></planView><lanes><laneSection s="0"><left><lane id="1" '
           'type="driving"><width sOffset="0" a="3.5" b="0" c="0" d="0"/></lane></left><center><lane id="0" '
           'type="none"/></center><right><lane id="-1" type="driving"><width sOffset="0" a="3.5" b="0" c="0" '
           'd="0"/></lane></right></laneSection></lanes></road>')
    evals = {1: RoadEval(etree.fromstring(xml))}
    (inner,) = _lane_steps(evals, [(1, 1, 10.0), (1, 1, 10.0 + STEP)])  # left lane: inside of a left curve
    (outer,) = _lane_steps(evals, [(1, -1, 10.0), (1, -1, 10.0 + STEP)])
    assert inner == pytest.approx(STEP * (1 - 0.05 * 1.75), rel=1e-3)
    assert outer == pytest.approx(STEP * (1 + 0.05 * 1.75), rel=1e-3)
    assert _lane_steps(None, [(1, 1, 0.0), (1, 1, STEP)]) == [STEP]
    assert _lane_steps(evals, [(1, 1, 0.0), (1, 5, STEP)]) == [STEP]  # a lane the road does not have


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
