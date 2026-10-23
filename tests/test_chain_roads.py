"""OpenDRIVE output for chain roads (stage A3): one road per run of road bends."""

from __future__ import annotations

import json
import math

import pytest
from lxml import etree

from roadgen.geometry import design
from roadgen.geometry.profiles import WidthPiece
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.metrics.geometry import geometry_metrics
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.elevation import evaluate
from roadgen.validate.continuity import RoadEval, check_continuity
from roadgen.validate.topology import check_topology

V = 13.9  # [m/s]


def graph_of(points, edges, z=None) -> RoadGraph:
    """edges: (id, from, to, lanes_forward, lanes_backward[, lane_width[, speed]])."""
    g = RoadGraph()
    for nid, (x, y) in points.items():
        g.add_node(Node(nid, x, y, z=(z or {}).get(nid, 0.0)))
    for eid, a, b, lf, lb, *rest in edges:
        width = rest[0] if rest else 3.5
        speed = rest[1] if len(rest) > 1 else V
        g.add_edge(Edge(eid, a, b, "street", lf, lb, width, speed))
    return g


def build(g: RoadGraph, tmp_path, name: str = "net"):
    b = OpenDriveBuilder()
    b.build(g)
    path = tmp_path / f"{name}.xodr"
    b.write(path)
    assert check_topology(path) == []
    assert check_continuity(path) == []
    root = etree.parse(str(path)).getroot()
    return b, path, root


def road(root, rid: int = 1):
    return root.find(f"road[@id='{rid}']")


def lanes_of(section) -> list[int]:
    return sorted(int(lane.get("id")) for lane in section.iter("lane") if lane.get("id") != "0")


def test_bend_is_one_road_with_spirals(tmp_path):
    g = graph_of({1: (0, 0), 2: (400, 0), 3: (400, 400)}, [(1, 1, 2, 1, 1), (2, 2, 3, 1, 1)])
    b, path, root = build(g, tmp_path)
    assert root.findall("junction") == []
    r = road(root)
    assert [geo[0].tag for geo in r.findall("planView/geometry")] == ["line", "spiral", "arc", "spiral", "line"]
    assert b.edge_to_road == {1: 1, 2: 1}
    data = json.loads(path.with_suffix(".roadmap.json").read_text())
    assert data["geometry_version"] == 2
    assert data["edge_s_range"]["1"][2] == pytest.approx(data["edge_s_range"]["2"][1])
    (corner,) = data["corners"]
    assert corner["advisory_speed"] is None
    m = geometry_metrics(path)
    assert m["lateral_acceleration"]["road"]["max"] <= 3.0
    assert m["curvature_jumps"]["joins"]["over_limit"] == 0


def test_lane_drop_tapers_and_links(tmp_path):
    # 2+2 for 300 m, then 1+1: the outer lanes narrow to 0 before node 2.
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (600, 60)}, [(1, 1, 2, 2, 2), (2, 2, 3, 1, 1)])
    b, _, root = build(g, tmp_path)
    sections = road(root).findall("lanes/laneSection")
    assert [lanes_of(s) for s in sections] == [[-2, -1, 1, 2], [-2, -1, 1, 2], [-1, 1]]
    s_node = b.alignment.chains[0].node_station(2)
    taper = design.taper_length(V, 3.5)
    assert float(sections[1].get("s")) == pytest.approx(s_node - taper)
    assert float(sections[2].get("s")) == pytest.approx(s_node)
    ev = RoadEval(road(root))
    # Cubic smoothstep: full width at the taper start, half way at its middle, 0 at the node.
    t_mid = ev._lane_t(-2, s_node - taper / 2) - ev._lane_t(-1, s_node - taper / 2)
    assert abs(t_mid) == pytest.approx((3.5 + 1.75) / 2, abs=1e-6)
    narrowing = sections[1].find("right/lane[@id='-2']")
    assert narrowing.find("link/predecessor").get("id") == "-2"
    assert narrowing.find("link/successor") is None
    assert sections[1].find("right/lane[@id='-1']/link/successor").get("id") == "-1"


def test_lane_add_widens_after_the_node(tmp_path):
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (600, 60)}, [(1, 1, 2, 1, 1), (2, 2, 3, 2, 2)])
    _, _, root = build(g, tmp_path)
    sections = road(root).findall("lanes/laneSection")
    assert [lanes_of(s) for s in sections] == [[-1, 1], [-2, -1, 1, 2], [-2, -1, 1, 2]]
    widening = sections[1].find("right/lane[@id='-2']")
    assert float(widening.find("width").get("a")) == 0.0
    assert widening.find("link/predecessor") is None
    assert widening.find("link/successor").get("id") == "-2"


def test_lane_width_change_is_a_transition(tmp_path):
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (600, 0)}, [(1, 1, 2, 1, 1, 3.0), (2, 2, 3, 1, 1, 3.5)])
    _, _, root = build(g, tmp_path)
    sections = road(root).findall("lanes/laneSection")
    assert len(sections) == 3
    widths = [float(s.find("right/lane/width").get("a")) for s in sections]
    assert widths == pytest.approx([3.0, 3.0, 3.5])


def test_short_corner_gets_an_advisory_speed_record(tmp_path):
    g = graph_of({1: (0, 0), 2: (80, 0), 3: (80, 300)}, [(1, 1, 2, 1, 1), (2, 2, 3, 1, 1)])
    b, path, root = build(g, tmp_path)
    speeds = [(float(t.get("s")), float(t.find("speed").get("max"))) for t in road(root).findall("type")]
    (corner,) = b.alignment.chains[0].corners
    assert corner.advisory_speed is not None and corner.advisory_speed < V
    # The advisory starts a deceleration lead before the corner and ends with it.
    (s_slow, v_slow), *rest = [(s0, v) for s0, v in speeds if v < V]
    assert v_slow == pytest.approx(corner.advisory_speed) and not rest
    lead = (V ** 2 - corner.advisory_speed ** 2) / (2 * 1.5)
    assert s_slow == pytest.approx(max(0.0, corner.s_start - lead))
    assert speeds[-1] == (pytest.approx(corner.s_end), pytest.approx(V))
    m = geometry_metrics(path)
    assert m["lateral_acceleration"]["road"]["max"] <= 3.0


def test_heights_run_through_the_bend_node(tmp_path):
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (300, 300)}, [(1, 1, 2, 1, 1), (2, 2, 3, 1, 1)],
                 z={1: 0.0, 2: 6.0, 3: 0.0})
    b, _, root = build(g, tmp_path)
    ca = b.alignment.chains[0]
    from roadgen.opendrive.elevation import Elevation

    records = [Elevation(*(float(e.get(k)) for k in ("s", "a", "b", "c", "d")))
               for e in road(root).findall("elevationProfile/elevation")]
    z, slope = evaluate(records, ca.node_station(2))
    assert z == pytest.approx(6.0, abs=1e-6)  # the bend node is a knot, not a flat junction
    assert slope == pytest.approx(0.0, abs=1e-6)  # a local maximum stays flat (PCHIP)
    assert evaluate(records, 0.0)[0] == pytest.approx(0.0)


def test_bridges_across_a_bend_node_merge(tmp_path):
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (600, 0)}, [(1, 1, 2, 1, 1), (2, 2, 3, 1, 1)])
    g.edges[1].bridges = [(250.0, 300.0)]
    g.edges[2].bridges = [(0.0, 40.0)]
    _, _, root = build(g, tmp_path)
    (bridge,) = road(root).findall("objects/bridge")
    assert float(bridge.get("s")) == pytest.approx(250.0)
    assert float(bridge.get("length")) == pytest.approx(90.0)


def test_width_piece_poly_matches_width():
    p = WidthPiece(10.0, 40.0, 3.5, 0.0)
    for at in (10.0, 17.0, 33.0):
        a, b, c, d = p.poly(at)
        for ds in (0.0, 2.5, 6.0):
            assert a + b * ds + c * ds ** 2 + d * ds ** 3 == pytest.approx(p.width(at + ds))
    assert p.width(25.0) == pytest.approx(1.75)
    assert math.isclose(WidthPiece(0, 5, 2.0, 2.0).poly(3.0)[0], 2.0)
