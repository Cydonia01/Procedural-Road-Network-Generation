from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path

import pytest
from lxml import etree

from cli import main
from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.geometry import Pose, hermite_poly3, wrap_angle
from roadgen.opendrive.junctions import (
    classify,
    continuation_pairs,
    Arm,
    junction_trims,
    lane_pairs,
)
from roadgen.validate.continuity import check_continuity, check_continuity_tree
from roadgen.validate.topology import check_topology, check_topology_tree

FIXTURES = Path(__file__).parent / "fixtures"
ALL_FIXTURES = ["single_4way", "single_T", "4way_avenue_x_street", "4way_oneway"]


def build(name: str, tmp_path: Path) -> tuple[OpenDriveBuilder, Path]:
    builder = OpenDriveBuilder(name)
    builder.build(load_json(FIXTURES / f"{name}.json"))
    path = tmp_path / f"{name}.xodr"
    builder.write(path)
    return builder, path


def movement_set(builder: OpenDriveBuilder) -> set[tuple[int, int, str, int, int]]:
    """(from_edge, to_edge, kind, from_lane, to_lane) for every connecting road."""
    return {
        (m.from_edge, m.to_edge, m.kind, m.from_lane, m.to_lane) for m in builder.movements
    }


# --- unit helpers ---------------------------------------------------------------


def test_wrap_angle():
    assert wrap_angle(3 * math.pi) == pytest.approx(math.pi)
    assert wrap_angle(-math.pi / 2) == pytest.approx(-math.pi / 2)
    assert wrap_angle(2 * math.pi + 0.1) == pytest.approx(0.1)


@pytest.mark.parametrize(
    "h_in,h_out,kind",
    [
        (0.0, 0.0, "straight"),
        (0.0, math.pi / 2, "left"),
        (0.0, -math.pi / 2, "right"),
        (math.pi, -math.pi / 2, "left"),  # westbound turning south
        (-math.pi / 2, math.pi, "right"),  # southbound turning west
    ],
)
def test_classify(h_in, h_out, kind):
    assert classify(h_in, h_out) == kind


def test_lane_pairs():
    assert lane_pairs("straight", [-1, -2], [-1, -2, -3]) == [(-1, -1), (-2, -2)]
    assert lane_pairs("right", [-1, -2], [1, 2]) == [(-2, 2)]
    assert lane_pairs("left", [-1, -2], [1, 2]) == [(-1, 1)]
    assert lane_pairs("left", [-1], []) == []


def test_continuation_pairs():
    assert continuation_pairs([-1, -2], [1, 2]) == [(-1, 1), (-2, 2)]
    assert continuation_pairs([-1, -2, -3], [1]) == [(-1, 1), (-2, 1), (-3, 1)]
    assert continuation_pairs([-1], [1, 2]) == [(-1, 1)]
    assert continuation_pairs([-1], []) == []


def test_junction_trims_right_angles_use_widest_road():
    # 2+2 avenue (7 m each side) crossing a 1+1 street (3 m) at 90 degrees.
    arms = [Arm(1, 0.0, 7, 7), Arm(2, math.pi / 2, 3, 3), Arm(3, math.pi, 7, 7), Arm(4, -math.pi / 2, 3, 3)]
    assert junction_trims(arms, 2.0) == {k: pytest.approx(9.0) for k in (1, 2, 3, 4)}


def test_junction_trims_acute_angle():
    # A 3.5 m street 30 degrees from an avenue arm: their facing edges cross far out.
    arms = [Arm(1, 0.0, 7, 7), Arm(2, math.radians(30), 3.5, 3.5), Arm(3, math.pi, 7, 7)]
    trims = junction_trims(arms, 2.0)
    theta = math.radians(30)
    assert trims[1] == pytest.approx((3.5 + 7 * math.cos(theta)) / math.sin(theta) + 2.0)
    assert trims[2] == pytest.approx((7 + 3.5 * math.cos(theta)) / math.sin(theta) + 2.0)
    assert trims[3] == pytest.approx(9.0)  # the opposite arm is unaffected


def test_junction_trims_reject_slivers():
    with pytest.raises(ValueError, match="meet at"):
        junction_trims([Arm(1, 0.0, 3, 3), Arm(2, math.radians(5), 3, 3)], 2.0)


@pytest.mark.parametrize(
    "start,end",
    [
        (Pose(0, 0, 0), Pose(10, 10, math.pi / 2)),
        (Pose(5, -3, math.pi), Pose(-4, -12, -math.pi / 2)),
        (Pose(0, 0, 0), Pose(20, 3.5, 0)),
    ],
)
def test_hermite_matches_endpoints(start, end):
    curve = hermite_poly3(start, end)
    u1, v1 = sum(curve.u), sum(curve.v)
    du1 = curve.u[1] + 2 * curve.u[2] + 3 * curve.u[3]
    dv1 = curve.v[1] + 2 * curve.v[2] + 3 * curve.v[3]
    c, s = math.cos(start.hdg), math.sin(start.hdg)
    assert start.x + u1 * c - v1 * s == pytest.approx(end.x)
    assert start.y + u1 * s + v1 * c == pytest.approx(end.y)
    assert wrap_angle(start.hdg + math.atan2(dv1, du1) - end.hdg) == pytest.approx(0, abs=1e-9)
    assert curve.v[1] == 0.0  # leaves along the start heading
    assert curve.length >= math.hypot(end.x - start.x, end.y - start.y)


# --- fixtures -------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixture_topology_and_continuity(tmp_path, name):
    _, path = build(name, tmp_path)
    assert check_topology(path) == []
    assert check_continuity(path) == []


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixture_cli(tmp_path, monkeypatch, name):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert main(["build-xodr", str(FIXTURES / f"{name}.json"), str(tmp_path / "x.xodr")]) == 0


# Edges in every 4-way fixture: 1 = west arm, 2 = east, 3 = north, 4 = south.
FOUR_WAY_KINDS = {
    (1, 2): "straight", (1, 3): "left", (1, 4): "right",
    (2, 1): "straight", (2, 4): "left", (2, 3): "right",
    (3, 4): "straight", (3, 2): "left", (3, 1): "right",
    (4, 3): "straight", (4, 1): "left", (4, 2): "right",
}


def test_single_4way_movements(tmp_path):
    builder, _ = build("single_4way", tmp_path)
    got = {(m.from_edge, m.to_edge): m.kind for m in builder.movements}
    assert got == FOUR_WAY_KINDS
    assert len(builder.movements) == 12


def test_single_T_movements(tmp_path):
    # Edges: 1 = west arm, 2 = east, 3 = south.
    builder, _ = build("single_T", tmp_path)
    got = {(m.from_edge, m.to_edge): m.kind for m in builder.movements}
    assert got == {
        (1, 2): "straight", (1, 3): "right",
        (2, 1): "straight", (2, 3): "left",
        (3, 1): "left", (3, 2): "right",
    }


def test_avenue_x_street_lane_mapping(tmp_path):
    builder, _ = build("4way_avenue_x_street", tmp_path)
    moves = movement_set(builder)
    assert len(moves) == 14
    # Eastbound avenue (edge 1 ends at the junction: in lanes -1, -2).
    assert (1, 2, "straight", -1, -1) in moves
    assert (1, 2, "straight", -2, -2) in moves
    assert (1, 3, "left", -1, 1) in moves  # leftmost lane -> northbound street
    assert (1, 4, "right", -2, -1) in moves  # rightmost lane -> southbound street
    # Westbound avenue (edge 2 starts at the junction: in lanes 1, 2).
    assert (2, 1, "straight", 1, 1) in moves and (2, 1, "straight", 2, 2) in moves
    assert (2, 4, "left", 1, -1) in moves
    assert (2, 3, "right", 2, 1) in moves
    # Street straight crossing has a single lane.
    assert [m for m in moves if m[:3] == (3, 4, "straight")] == [(3, 4, "straight", -1, -1)]


def test_oneway_respects_directions(tmp_path):
    # Edge 3: north arm, one-way southbound into the junction (2+0).
    # Edge 4: south arm, backward-only edge 5->1, i.e. one-way southbound away.
    builder, _ = build("4way_oneway", tmp_path)
    moves = movement_set(builder)
    assert not any(m[1] == 3 for m in moves), "nothing may enter the one-way north arm"
    assert not any(m[0] == 4 for m in moves), "nothing comes out of the one-way south arm"
    pairs = {(m[0], m[1]): m[2] for m in moves}
    assert pairs == {
        (1, 2): "straight", (1, 4): "right",
        (2, 1): "straight", (2, 4): "left",
        (3, 4): "straight", (3, 2): "left", (3, 1): "right",
    }
    assert len([m for m in moves if m[:3] == (3, 4, "straight")]) == 2
    assert len(moves) == 8


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_xodr_structure(tmp_path, name):
    builder, path = build(name, tmp_path)
    root = etree.parse(str(path)).getroot()
    junctions = root.findall("junction")
    assert len(junctions) == 1
    jid = junctions[0].get("id")

    roads = {r.get("id"): r for r in root.findall("road")}
    normal = {str(r) for r in builder.edge_to_road.values()}
    connecting = {r for r, el in roads.items() if el.get("junction") == jid}
    assert connecting == {str(m.connecting_road) for m in builder.movements}
    assert len(junctions[0].findall("connection")) == len(connecting)

    for rid in normal:
        links = roads[rid].findall("link/*")
        assert [(l.get("elementType"), l.get("elementId")) for l in links] == [("junction", jid)]

    by_id = {str(m.connecting_road): m for m in builder.movements}
    for rid in connecting:
        road, m = roads[rid], by_id[rid]
        geom = road.find("planView/geometry")[0].tag
        assert geom == ("line" if m.kind == "straight" else "paramPoly3")
        lanes = road.findall("lanes/laneSection/*/lane")
        assert sorted(int(l.get("id")) for l in lanes) == [-1, 0]
        assert road.find("link/predecessor").get("elementId") == str(m.from_road)
        assert road.find("link/successor").get("elementId") == str(m.to_road)


def test_roads_trimmed_to_junction_boundary(tmp_path):
    _, path = build("single_4way", tmp_path)
    root = etree.parse(str(path)).getroot()
    for road in root.findall("road[@junction='-1']"):
        # 80 m arm minus (1 lane * 3.5 m + 2 m margin).
        assert float(road.get("length")) == pytest.approx(74.5)


def test_custom_margin(tmp_path):
    builder = OpenDriveBuilder(junction_margin=5.0)
    builder.build(load_json(FIXTURES / "single_4way.json"))
    builder.write(tmp_path / "x.xodr")
    root = etree.parse(str(tmp_path / "x.xodr")).getroot()
    assert float(root.find("road").get("length")) == pytest.approx(71.5)


def test_edge_too_short_for_junction(tmp_path):
    graph = load_json(FIXTURES / "single_4way.json")
    graph.nodes[2].x = -4.0  # west arm only 4 m long
    with pytest.raises(ValueError, match="too short"):
        OpenDriveBuilder().build(graph)


# --- validators catch broken files ------------------------------------------------


def test_continuity_detects_gap(tmp_path):
    _, path = build("single_4way", tmp_path)
    tree = etree.parse(str(path))
    geom = tree.getroot().find("road[@id='1']/planView/geometry")
    geom.set("y", str(float(geom.get("y")) + 0.05))
    errors = check_continuity_tree(tree)
    assert errors
    assert all("road 1" in e and "position mismatch" in e for e in errors)


def test_continuity_detects_heading_error(tmp_path):
    _, path = build("single_4way", tmp_path)
    tree = etree.parse(str(path))
    turn = next(r for r in tree.getroot().findall("road") if "_left_" in r.get("name"))
    geom = turn.find("planView/geometry")
    geom.set("hdg", str(float(geom.get("hdg")) + 0.05))
    errors = check_continuity_tree(tree)
    assert any(f"road {turn.get('id')}" in e and "heading mismatch" in e for e in errors)


def test_topology_detects_bad_lane_links(tmp_path):
    _, path = build("single_4way", tmp_path)
    tree = etree.parse(str(path))
    root = tree.getroot()
    root.find("junction/connection/laneLink").set("from", "-7")
    connecting = next(r for r in root.findall("road") if r.get("junction") != "-1")
    connecting.find("lanes/laneSection/right/lane/link/successor").set("id", "5")
    errors = check_topology_tree(tree)
    assert any("laneLink from lane -7" in e for e in errors)
    assert any("successor lane 5 missing" in e for e in errors)


# --- esmini drive-through (skipped when esmini is not available) --------------------


def _rm_available() -> bool:
    from roadgen.sim.esmini import esmini_library

    try:
        esmini_library("esminiRMLib")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_esmini_drives_every_movement(tmp_path, name):
    from roadgen.sim.drive_check import drive_movements

    builder, path = build(name, tmp_path)
    results = drive_movements(path)
    assert len(results) == len(builder.movements)
    failed = [r.describe() for r in results if not r.ok]
    assert failed == []


# --- angled and 3- to 5-way junctions ------------------------------------------------------

ANGLED = ["angled_Y", "acute_T", "star_5way", "skewed_4way", "mixed_5way"]


def road_surfaces(xodr: Path) -> dict:
    """Rectangle covering each normal road's lanes (all roads here are straight)."""
    from shapely.geometry import Polygon

    out = {}
    for r in etree.parse(str(xodr)).getroot().findall("road[@junction='-1']"):
        g = r.find("planView/geometry")
        x, y, h, length = (float(g.get(k)) for k in ("x", "y", "hdg", "length"))
        lw = sum(float(l.find("width").get("a")) for l in r.findall("lanes/laneSection/left/lane"))
        rw = sum(float(l.find("width").get("a")) for l in r.findall("lanes/laneSection/right/lane"))
        c, s = math.cos(h), math.sin(h)
        out[r.get("id")] = Polygon([
            (x - s * lw, y + c * lw), (x + c * length - s * lw, y + s * length + c * lw),
            (x + c * length + s * rw, y + s * length - c * rw), (x + s * rw, y - c * rw),
        ])
    return out


@pytest.mark.parametrize("name", ANGLED)
def test_angled_junctions_are_valid(tmp_path, name):
    builder, path = build(name, tmp_path)
    assert check_topology(path) == []
    assert check_continuity(path) == []
    surfaces = road_surfaces(path)
    for (a, pa), (b, pb) in combinations(surfaces.items(), 2):
        assert pa.intersection(pb).area < 0.01, f"roads {a} and {b} overlap"


@pytest.mark.parametrize("name", ANGLED)
def test_at_most_one_straight_exit_per_lane(tmp_path, name):
    builder, _ = build(name, tmp_path)
    straight_targets = {}
    for m in builder.movements:
        assert (m.turn_angle > 0) == (m.kind == "left") or m.kind == "straight"
        if m.kind == "straight":
            straight_targets.setdefault(m.from_edge, set()).add(m.to_edge)
    assert all(len(t) == 1 for t in straight_targets.values())


def test_star_5way_movement_count(tmp_path):
    builder, _ = build("star_5way", tmp_path)
    assert len(builder.movements) == 5 * 4  # every arm to every other arm, 1 lane each


def test_mixed_5way_respects_one_ways(tmp_path):
    builder, _ = build("mixed_5way", tmp_path)
    # Edge 2 only enters the junction and edge 4 only leaves it.
    assert not any(m.to_edge == 2 for m in builder.movements)
    assert not any(m.from_edge == 4 for m in builder.movements)


def test_sliver_angle_rejected(tmp_path):
    from roadgen.graph.model import Edge, Node, RoadGraph

    g = RoadGraph()
    for i, (x, y) in enumerate([(0, 0), (100, 0), (100, 5), (-100, 0)], 1):
        g.add_node(Node(i, float(x), float(y)))
    for k, b in enumerate((2, 3, 4), 1):
        g.add_edge(Edge(k, 1, b, "street", 1, 1))
    with pytest.raises(ValueError, match="node 1: edges .* meet at"):
        OpenDriveBuilder().build(g)


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
@pytest.mark.parametrize("name", ANGLED)
def test_esmini_drives_angled_junctions(tmp_path, name):
    from roadgen.sim.drive_check import drive_movements

    builder, path = build(name, tmp_path)
    results = drive_movements(path)
    assert len(results) == len(builder.movements)
    assert [r.describe() for r in results if not r.ok] == []
