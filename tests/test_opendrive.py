from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from lxml import etree

from cli import main
from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder, sidecar_path
from roadgen.opendrive.geometry import segment_start, trim
from roadgen.opendrive.lanes import make_lanes
from roadgen.validate.schema import validate_schema
from roadgen.validate.topology import check_topology, check_topology_tree

DATA = Path(__file__).parent / "data"


def single_road(forward: int, backward: int) -> RoadGraph:
    g = RoadGraph()
    g.add_node(Node(1, 10.0, 20.0, "dead_end"))
    g.add_node(Node(2, 130.0, 70.0, "dead_end"))
    g.add_edge(Edge(7, 1, 2, "street", forward, backward, lane_width=3.25))
    return g


def build_and_parse(graph: RoadGraph, tmp_path: Path) -> etree._Element:
    builder = OpenDriveBuilder()
    builder.build(graph)
    path = tmp_path / "road.xodr"
    builder.write(path)
    return etree.parse(str(path)).getroot()


def lane_ids(road: etree._Element, side: str) -> list[int]:
    return sorted(int(l.get("id")) for l in road.findall(f"lanes/laneSection/{side}/lane"))


# --- geometry -----------------------------------------------------------------


def test_segment_start():
    pose, length = segment_start((1.0, 2.0), (4.0, 6.0))
    assert (pose.x, pose.y) == (1.0, 2.0)
    assert pose.hdg == pytest.approx(math.atan2(4.0, 3.0))
    assert length == pytest.approx(5.0)


def test_segment_start_zero_length():
    with pytest.raises(ValueError):
        segment_start((1.0, 1.0), (1.0, 1.0))


def test_trim():
    s, e = trim((0.0, 0.0), (0.0, 10.0), 2.0, 3.0)
    assert s == pytest.approx((0.0, 2.0))
    assert e == pytest.approx((0.0, 7.0))


@pytest.mark.parametrize("ts,te", [(5.0, 5.0), (-1.0, 0.0), (11.0, 0.0)])
def test_trim_rejects_bad_distances(ts, te):
    with pytest.raises(ValueError):
        trim((0.0, 0.0), (10.0, 0.0), ts, te)


# --- lanes --------------------------------------------------------------------


@pytest.mark.parametrize("fwd,bwd", [(1, 1), (2, 2), (2, 0), (3, 1)])
def test_make_lanes_sides(fwd, bwd):
    section = make_lanes(fwd, bwd, 3.5).lanesections[0]
    assert len(section.rightlanes) == fwd
    assert len(section.leftlanes) == bwd


def test_make_lanes_requires_forward_lane():
    with pytest.raises(ValueError):
        make_lanes(0, 2, 3.5)


# --- builder ------------------------------------------------------------------


@pytest.mark.parametrize("fwd,bwd", [(1, 1), (2, 2), (2, 0)])
def test_single_road(tmp_path, fwd, bwd):
    graph = single_road(fwd, bwd)
    root = build_and_parse(graph, tmp_path)

    roads = root.findall("road")
    assert len(roads) == 1
    road = roads[0]
    assert float(road.get("length")) == pytest.approx(graph.edge_length(7))

    geom = road.find("planView/geometry")
    assert geom.find("line") is not None
    assert float(geom.get("x")) == pytest.approx(10.0)
    assert float(geom.get("y")) == pytest.approx(20.0)
    assert float(geom.get("hdg")) == pytest.approx(graph.edge_heading(7))
    assert float(geom.get("length")) == pytest.approx(graph.edge_length(7))

    assert lane_ids(road, "right") == list(range(-fwd, 0))
    assert lane_ids(road, "left") == list(range(1, bwd + 1))
    for lane in road.findall("lanes/laneSection/right/lane"):
        assert lane.get("type") == "driving"
        assert float(lane.find("width").get("a")) == pytest.approx(3.25)

    assert float(road.find("type/speed").get("max")) == pytest.approx(13.9)
    assert check_topology_tree(etree.ElementTree(root)) == []


def test_road_marks_2_2(tmp_path):
    road = build_and_parse(single_road(2, 2), tmp_path).find("road")
    section = road.find("lanes/laneSection")

    def mark(lane_id: int) -> str:
        lane = section.find(f".//lane[@id='{lane_id}']")
        return lane.find("roadMark").get("type")

    assert mark(0) == "solid"
    assert mark(-1) == "broken" and mark(1) == "broken"
    assert mark(-2) == "solid" and mark(2) == "solid"


def test_backward_only_road_is_reversed(tmp_path):
    graph = single_road(0, 2)
    road = build_and_parse(graph, tmp_path).find("road")
    geom = road.find("planView/geometry")
    assert float(geom.get("x")) == pytest.approx(130.0)
    assert float(geom.get("y")) == pytest.approx(70.0)
    assert float(geom.get("hdg")) == pytest.approx(graph.edge_heading(7) - math.pi)
    assert lane_ids(road, "right") == [-2, -1]
    assert lane_ids(road, "left") == []


def test_output_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        builder = OpenDriveBuilder()
        builder.build(single_road(2, 2))
        builder.write(d / "road.xodr")
    assert (a / "road.xodr").read_bytes() == (b / "road.xodr").read_bytes()


def test_sidecar_mapping(tmp_path):
    builder = OpenDriveBuilder()
    builder.build(single_road(1, 1))
    sidecar = builder.write(tmp_path / "road.xodr")
    assert sidecar == sidecar_path(tmp_path / "road.xodr")
    data = json.loads(sidecar.read_text())
    assert data == {
        "xodr": "road.xodr",
        "edge_to_road": {"7": 1},
        "node_to_junction": {},
        "movements": [],
    }


def test_degree_2_node_becomes_bend_junction(tmp_path):
    # Node 2 joins a 2+2 avenue to a one-way 1+0 street at a right angle.
    builder = OpenDriveBuilder()
    builder.build(load_json(DATA / "three_nodes.json"))
    builder.write(tmp_path / "bend.xodr")
    assert builder.node_to_junction == {2: 3}
    lanes = sorted((m.from_lane, m.to_lane) for m in builder.movements)
    assert lanes == [(-2, -1), (-1, -1)]  # both avenue lanes continue, merging
    assert check_topology(tmp_path / "bend.xodr") == []


def test_invalid_graph_rejected():
    graph = single_road(1, 1)
    graph.add_edge(Edge(8, 1, 99, "street", 1, 1))
    with pytest.raises(ValueError, match="missing node"):
        OpenDriveBuilder().build(graph)


def test_write_before_build(tmp_path):
    with pytest.raises(RuntimeError):
        OpenDriveBuilder().write(tmp_path / "x.xodr")


# --- validators ---------------------------------------------------------------


BAD_XODR = """<OpenDRIVE>
  <header/>
  <road id="1" junction="-1"><link><successor elementType="road" elementId="5"/></link></road>
  <road id="1" junction="9"><link><predecessor elementType="junction" elementId="3"/></link></road>
  <junction id="3"><connection id="0" incomingRoad="1" connectingRoad="42"/></junction>
</OpenDRIVE>"""


def test_topology_reports_errors(tmp_path):
    path = tmp_path / "bad.xodr"
    path.write_text(BAD_XODR)
    errors = check_topology(path)
    assert any("duplicate road id 1" in e for e in errors)
    assert any("missing road 5" in e for e in errors)
    assert any("missing junction 9" in e for e in errors)
    assert any("connectingRoad references missing road 42" in e for e in errors)
    assert len(errors) == 4


def test_schema_skipped_without_xsd(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    path = tmp_path / "x.xodr"
    path.write_text("<OpenDRIVE/>")
    assert validate_schema(path) is None
    assert "skipping" in caplog.text


def test_schema_with_xsd(tmp_path):
    xsd = tmp_path / "mini.xsd"
    xsd.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="OpenDRIVE"><xs:complexType/></xs:element></xs:schema>'
    )
    good, bad = tmp_path / "good.xodr", tmp_path / "bad.xodr"
    good.write_text("<OpenDRIVE/>")
    bad.write_text("<OpenDRIVE><road/></OpenDRIVE>")
    assert validate_schema(good, xsd) == []
    assert len(validate_schema(bad, xsd)) == 1


# --- CLI ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["road_1_1", "road_2_2", "road_2_0"])
def test_cli_build_xodr(tmp_path, monkeypatch, name):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    out = tmp_path / f"{name}.xodr"
    assert main(["build-xodr", str(DATA / f"{name}.json"), str(out)]) == 0
    assert etree.parse(str(out)).getroot().tag == "OpenDRIVE"
    assert sidecar_path(out).is_file()


def test_cli_view_without_esmini(monkeypatch, tmp_path):
    monkeypatch.delenv("ESMINI_HOME", raising=False)
    monkeypatch.setattr("roadgen.sim.esmini._BUNDLED_ESMINI", tmp_path / "no-esmini")
    assert main(["view", "missing.xodr"]) == 1
