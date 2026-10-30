"""Stage B5: junction control, right of way, signs and lines, annotate."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest
from lxml import etree

from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.annotate import AnnotateParams, annotate
from roadgen.maps.model import STYLE_GRID, STYLE_SUBURB, GridSpec, MapLayer
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.control import (
    Approach,
    JunctionControlParams,
    junction_control,
    priority_pairs,
)
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.junctions import Movement
from roadgen.validate.hdmap import check_hdmap, check_hdmap_tree
from roadgen.validate.topology import check_topology

P = JunctionControlParams()


def _star(classes: list[str], angles_deg: list[float], lanes: int = 1, speed: float = 13.9,
          kind: str = "auto", control=None) -> tuple[RoadGraph, list[Approach]]:
    """A junction at node 0 with one arm per class at the given bearings (arms end 100 m out)."""
    g = RoadGraph()
    g.add_node(Node(0, 0.0, 0.0, kind, control=control))
    approaches = []
    for k, (cls, deg) in enumerate(zip(classes, angles_deg), 1):
        a = math.radians(deg)
        g.add_node(Node(k, 100 * math.cos(a), 100 * math.sin(a)))
        g.add_edge(Edge(k, k, 0, cls, lanes, lanes, speed_limit=speed))
        # arriving traffic drives towards the centre, against the arm's bearing
        approaches.append(Approach((k, "end"), k, cls, 2 * lanes, speed, a + math.pi))
    return g, approaches


def _control(classes, angles=(0, 90, 180, 270), **kw):
    g, approaches = _star(list(classes), list(angles[:len(classes)]), **kw)
    return junction_control(0, approaches, g, P, seed=0)


def test_street_junctions_are_right_before_left():
    c = _control(["street"] * 4)
    assert c.kind == "right_before_left" and set(c.roles.values()) == {"right_before_left"}


def test_streets_get_a_priority_road_without_right_before_left():
    g, approaches = _star(["street"] * 3, [0, 90, 180])
    c = junction_control(0, approaches, g, JunctionControlParams.preset("rural"), seed=0)
    assert c.kind == "priority"
    assert set(c.major) == {(1, "end"), (3, "end")}  # the two opposite arms
    assert c.role((2, "end")) in ("yield", "stop")


def test_avenue_through_streets_is_a_priority_road_and_minors_yield():
    c = _control(["avenue", "street", "avenue", "street"])
    assert c.kind == "priority"
    assert set(c.major) == {(1, "end"), (3, "end")}
    assert {c.role((2, "end")), c.role((4, "end"))} <= {"yield", "stop"}


def test_two_crossing_avenues_get_lights():
    assert _control(["avenue"] * 4).kind == "signal"
    assert _control(["highway", "avenue", "highway", "avenue"]).kind == "signal"


def test_five_legs_with_an_avenue_get_lights():
    c = _control(["avenue", "street", "avenue", "street", "street"], angles=(0, 72, 180, 216, 288))
    assert c.kind == "signal"


def test_fast_major_road_makes_minors_stop():
    g, approaches = _star(["avenue", "street", "avenue"], [0, 90, 180], speed=16.7)
    c = junction_control(0, approaches, g, P, seed=0)
    assert c.role((2, "end")) == "stop"  # 16.7 m/s >= stop_speed


def test_skewed_minor_arm_stops():
    g, approaches = _star(["avenue", "street", "avenue"], [0, 40, 180])
    c = junction_control(0, approaches, g, JunctionControlParams(stop_share=0.0), seed=0)
    assert c.role((2, "end")) == "stop"
    g, approaches = _star(["avenue", "street", "avenue"], [0, 90, 180])
    c = junction_control(0, approaches, g, JunctionControlParams(stop_share=0.0), seed=0)
    assert c.role((2, "end")) == "yield"


def test_stop_share_is_seeded():
    g, approaches = _star(["avenue", "street", "avenue"], [0, 90, 180])
    every = JunctionControlParams(stop_share=1.0)
    assert junction_control(0, approaches, g, every, seed=3).role((2, "end")) == "stop"
    roles = {junction_control(0, approaches, g, JunctionControlParams(stop_share=0.5), seed=s).role((2, "end"))
             for s in range(20)}
    assert roles == {"stop", "yield"}
    a = [junction_control(0, approaches, g, JunctionControlParams(stop_share=0.5), seed=s).role((2, "end"))
         for s in range(20)]
    b = [junction_control(0, approaches, g, JunctionControlParams(stop_share=0.5), seed=s).role((2, "end"))
         for s in range(20)]
    assert a == b


@pytest.mark.parametrize("control, kind, minor", [("signal", "signal", "signal"), ("stop", "priority", "stop"),
                                                  ("yield", "priority", "yield"),
                                                  ("right_before_left", "right_before_left", "right_before_left"),
                                                  ("none", "none", "none")])
def test_node_control_overrides_the_rules(control, kind, minor):
    c = _control(["avenue", "street", "avenue", "street"], control=control)
    assert c.kind == kind and c.role((2, "end")) == minor


def test_unknown_node_control_is_rejected():
    with pytest.raises(ValueError, match="unknown control"):
        _control(["street"] * 3, control="roundabout")


def test_two_road_junctions_and_ramp_nodes_are_uncontrolled():
    assert _control(["avenue", "street"]).kind == "none"
    assert _control(["highway", "highway", "highway"], kind="ramp").kind == "none"


def test_roundabout_entry_yields_to_the_ring():
    g, approaches = _star(["street", "street", "avenue"], [0, 90, 180])
    g.metadata["roundabouts"] = [{"node": 9, "entries": [0], "ring": [0, 1, 2]}]
    c = junction_control(0, approaches, g, P, seed=0)
    assert c.kind == "roundabout"
    assert c.role((1, "end")) == c.role((2, "end")) == "major" and c.role((3, "end")) == "yield"


@pytest.mark.parametrize("crossing, kind, ramp_role", [("avenue", "signal", "signal"), ("street", "priority", "stop")])
def test_diamond_terminal(crossing, kind, ramp_role):
    g, approaches = _star([crossing, "highway", crossing, "highway"], [0, 90, 180, 270])
    g.metadata["interchanges"] = [{"type": "diamond", "terminals": [0], "ramps": [2, 4]}]
    c = junction_control(0, approaches, g, P, seed=0)
    assert c.kind == kind and c.role((2, "end")) == ramp_role
    assert set(c.major) == {(1, "end"), (3, "end")}


def _movement(road: int, kind: str, to_road: int, cr: int) -> Movement:
    return Movement(1, cr, kind, 0.0, road, road, -1, to_road, to_road, 1)


def test_priority_pairs_major_over_minor_and_left_turn_rule():
    g, approaches = _star(["avenue", "street", "avenue", "street"], [0, 90, 180, 270])
    control = junction_control(0, approaches, g, P, seed=0)
    by_road = {a.key[0]: a for a in approaches}
    straight_major = _movement(1, "straight", 3, 10)  # from the east arm, westbound
    left_major = _movement(3, "left", 4, 11)  # oncoming from the west, turning left
    minor = _movement(2, "straight", 4, 12)
    moves = [straight_major, left_major, minor]
    approach_of = {m.connecting_road: by_road[m.from_road] for m in moves}
    pairs = priority_pairs(moves, [(10, 11), (10, 12), (11, 12)], control, approach_of)
    assert set(pairs) == {(10, 11), (10, 12), (11, 12)}


def test_right_before_left_pairs():
    g, approaches = _star(["street"] * 4, [0, 90, 180, 270])
    control = junction_control(0, approaches, g, P, seed=0)
    by_road = {a.key[0]: a for a in approaches}
    # arm 1 (east, westbound) and arm 2 (north, southbound): for the westbound car
    # the southbound one comes from its right
    a, b = _movement(1, "straight", 3, 10), _movement(2, "straight", 4, 11)
    pairs = priority_pairs([a, b], [(10, 11)], control, {10: by_road[1], 11: by_road[2]})
    assert pairs == [(11, 10)]


def _layers(density: np.ndarray, style: np.ndarray = None) -> tuple[MapLayer, MapLayer]:
    grid = GridSpec((-250.0, -250.0), (500.0, 500.0), 50.0)  # 10 x 10 cells
    pop = MapLayer("population", grid, density.astype(np.float32))
    sty = None if style is None else MapLayer("style", grid, style.astype(np.uint8))
    return pop, sty


def test_annotate_sets_profiles_and_control_from_density():
    g, _ = _star(["avenue", "street", "avenue", "street"], [0, 90, 180, 270])
    pop, _ = _layers(np.full((10, 10), 8000.0))
    out = annotate(g, pop)
    assert {e.cross_section for e in out.edges.values()} == {"urban"}
    assert out.nodes[0].control == "signal"
    assert g.nodes[0].control is None  # the input is left unchanged
    sparse = annotate(g, _layers(np.full((10, 10), 1000.0))[0])
    assert {e.cross_section for e in sparse.edges.values()} == {"rural"}
    assert sparse.nodes[0].control == "stop"
    mid = annotate(g, _layers(np.full((10, 10), 2000.0))[0])
    assert {e.cross_section for e in mid.edges.values()} == {"suburban"} and mid.nodes[0].control is None
    assert mid.metadata["annotate"]["profiles"]["suburban"] == 4


def test_annotate_keeps_hand_set_values_and_suburb_districts():
    g, _ = _star(["avenue", "street", "avenue", "street"], [0, 90, 180, 270], control="yield")
    g.edges[1].cross_section = "rural"
    pop, style = _layers(np.full((10, 10), 8000.0), np.full((10, 10), STYLE_SUBURB))
    out = annotate(g, pop, style)
    assert out.nodes[0].control == "yield" and out.edges[1].cross_section == "rural"
    assert out.edges[2].cross_section == "suburban"
    _, grid_style = _layers(np.full((10, 10), 8000.0), np.full((10, 10), STYLE_GRID))
    assert annotate(g, pop, grid_style).edges[2].cross_section == "urban"


def test_annotate_params_are_checked():
    with pytest.raises(ValueError):
        AnnotateParams(urban_density=1000.0, suburban_density=2000.0)


# --- built junctions ------------------------------------------------------------------

HD = HdMapParams(enabled=True)


def _build(graph: RoadGraph, tmp_path, hd: HdMapParams = HD):
    path = tmp_path / "net.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot(), json.loads(path.with_suffix(".roadmap.json").read_text())


def _junction(sidecar: dict) -> dict:
    return next(j for j in sidecar["junctions"] if j["control"] != "none")


def test_priority_junction_gets_lines_signs_and_priorities(tmp_path):
    path, root, sidecar = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    assert check_topology(path) == [] and check_hdmap(path) == []
    j = _junction(sidecar)
    assert j["control"] == "priority"
    roles = {a["role"] for a in j["approaches"]}
    assert roles == {"major", "stop"}  # 16.7 m/s avenue: the streets stop
    codes = sorted(s.get("type") for s in root.iter("signal"))
    assert codes == ["206", "206", "301", "301"]
    lines = [o for o in root.iter("object") if o.get("name") == "stopLine"]
    assert len(lines) == 2 and all(o.get("type") == "roadMark" for o in lines)
    marking = lines[0].find("markings/marking")
    assert marking.get("color") == "white" and float(marking.get("width")) == pytest.approx(0.5)
    assert len(marking.findall("cornerReference")) == 2
    priorities = root.findall("junction/priority")
    assert priorities and len(priorities) == j["priorities"]
    # between the priority road and a street the avenue's movement is high; between two
    # avenue movements the left turn yields to the oncoming one
    mc = sidecar["movement_control"]
    kinds = {str(m["connecting_road"]): m["kind"] for m in sidecar["movements"]}
    for p in priorities:
        high, low = mc[p.get("high")], mc[p.get("low")]
        assert high["priority"] or not low["priority"]
        if high["priority"] and low["priority"]:
            assert kinds[p.get("low")] == "left" and kinds[p.get("high")] != "left"
        assert int(p.get("high")) in low["yields_to"]
    assert any(mc[p.get("high")]["priority"] and not mc[p.get("low")]["priority"] for p in priorities)


def test_signs_face_their_approach_and_stand_beside_the_carriageway(tmp_path):
    _, root, _ = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    for road in root.findall("road"):
        for sig in road.findall("signals/signal"):
            at_end = road.find("link/successor[@elementType='junction']") is not None and \
                float(sig.get("s")) > float(road.get("length")) / 2
            assert sig.get("orientation") == ("+" if at_end else "-")
            assert (float(sig.get("t")) < 0) == at_end  # the right roadside of arriving traffic
            assert sig.get("country") == "DE" and sig.get("subtype") == "-1" and sig.get("value") is None


def test_every_sign_code_is_in_esmini_table():
    known = {line.split("=")[0] for line in Path("tests/data/esmini_de_signs.txt").read_text().splitlines()
             if line and not line.startswith("#")}
    from roadgen.opendrive import furniture
    from roadgen.opendrive.control import Control

    used = set()
    for role in ("stop", "yield", "major"):
        for roundabout in (False, True):
            geom = furniture.EndGeometry(1, "end", 100.0, 100.0, (-1,), {-1: (0.0, -3.5)}, lambda s: 3.5)
            sigs, _ = furniture.approach_furniture(1, Control("priority", {(1, "end"): role}), geom, roundabout)
            used |= {s.type for s in sigs}
    assert used == {"205", "206", "215", "301"} and used <= known


def test_two_avenues_get_stop_lines_and_left_turn_priorities(tmp_path):
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    for e in g.edges.values():
        e.road_class, e.lanes_forward, e.lanes_backward, e.lane_width = "avenue", 2, 2, 3.5
    path, root, sidecar = _build(g, tmp_path)
    assert check_hdmap(path) == []
    j = _junction(sidecar)
    assert j["control"] == "signal" and {a["role"] for a in j["approaches"]} == {"signal"}
    assert len([o for o in root.iter("object") if o.get("name") == "stopLine"]) == 4
    assert not list(root.iter("signal"))  # the lights come with B6
    moves = {m["connecting_road"]: m for m in sidecar["movements"]}
    mc = sidecar["movement_control"]
    lefts = [m for m in moves.values() if m["kind"] == "left"]
    assert lefts
    for m in lefts:  # each permissive left yields to the oncoming straight movements
        oncoming = [o for o in moves.values() if o["kind"] == "straight" and o["to_road"] == m["from_road"]]
        assert oncoming and {o["connecting_road"] for o in oncoming} <= set(mc[str(m["connecting_road"])]["yields_to"])


def test_conflicts_are_complete_on_a_4_leg_junction():
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    for e in g.edges.values():
        e.road_class, e.lanes_forward, e.lanes_backward = "avenue", 2, 2
    b = OpenDriveBuilder(hd=HD)
    b.build(g)
    jid = next(iter(b.node_to_junction.values()))
    moves = [m for m in b.movements if m.junction_id == jid]
    pairs = set(b.conflicts[jid])
    assert all(x < y for x, y in pairs) and len(pairs) == len(b.conflicts[jid])
    by = {m.connecting_road: m for m in moves}
    for m in moves:  # a left turn crosses every straight movement from the opposite arm
        if m.kind != "left":
            continue
        for o in moves:
            if o.kind == "straight" and o.to_road == m.from_road:
                assert tuple(sorted((m.connecting_road, o.connecting_road))) in pairs
    for a in moves:  # movements from different arms into the same lane merge: a conflict
        for c in moves:
            if a.connecting_road < c.connecting_road and (a.to_road, a.to_lane) == (c.to_road, c.to_lane) \
                    and a.from_road != c.from_road:
                assert (a.connecting_road, c.connecting_road) in pairs
    assert all(by[x].from_road != by[y].from_road for x, y in pairs)
    # two right turns from neighbouring arms never meet
    rights = [m for m in moves if m.kind == "right"]
    assert any(tuple(sorted((r.connecting_road, q.connecting_road))) not in pairs
               for r in rights for q in rights if r.from_road != q.from_road)


def test_roundabout_entries_yield_with_sign_215(tmp_path):
    from roadgen.generators.base import get_generator, load_params

    gen = get_generator("junction")
    g = gen.generate(load_params(gen.params_type, preset="roundabout"), 0)
    path, root, sidecar = _build(g, tmp_path)
    assert check_hdmap(path) == []
    rings = [j for j in sidecar["junctions"] if j["control"] == "roundabout"]
    assert len(rings) == 4
    codes = [s.get("type") for s in root.iter("signal")]
    assert codes.count("215") >= 3 and codes.count("205") >= codes.count("215")
    assert len([o for o in root.iter("object") if o.get("name") == "yieldLine"]) >= 3
    yield_line = next(o for o in root.iter("object") if o.get("name") == "yieldLine")
    marking = yield_line.find("markings/marking")
    assert (float(marking.get("lineLength")), float(marking.get("spaceLength"))) == (0.5, 0.25)


def test_priorities_off_keeps_them_in_the_sidecar_only(tmp_path):
    hd = dataclasses.replace(HD, control=dataclasses.replace(HD.control, priorities=False))
    _, root, sidecar = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path, hd)
    assert not root.findall("junction/priority")
    assert any(c["yields_to"] for c in sidecar["movement_control"].values())


def test_validator_catches_broken_furniture(tmp_path):
    path, _, sidecar = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)

    def errors(edit) -> list[str]:
        tree = etree.parse(str(path))
        edit(tree.getroot())
        return check_hdmap_tree(tree, sidecar)

    def sign_outside(root):
        next(root.iter("signal")).set("t", "40.0")

    def no_stop_line(root):
        for o in list(root.iter("object")):
            if o.get("name") == "stopLine":
                o.getparent().remove(o)

    def duplicate_id(root):
        sigs = list(root.iter("signal"))
        sigs[1].set("id", sigs[0].get("id"))

    def foreign_priority(root):
        next(root.iter("priority")).set("high", "1")

    def bad_validity(root):
        next(root.iter("signal")).find("validity").set("toLane", "-9")

    assert check_hdmap_tree(etree.parse(str(path)), sidecar) == []
    assert any("outside the road" in e for e in errors(sign_outside))
    assert any("no stop or yield line" in e for e in errors(no_stop_line))
    assert any("used 2 times" in e for e in errors(duplicate_id))
    assert any("not its connecting roads" in e for e in errors(foreign_priority))
    assert any("validity lanes" in e for e in errors(bad_validity))


def test_hd_off_has_no_control(tmp_path):
    _, root, sidecar = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path, HdMapParams())
    assert not list(root.iter("signal")) and not root.findall("junction/priority")
    assert "junctions" not in sidecar and "movement_control" not in sidecar


def test_annotate_command_and_pipeline(tmp_path):
    from cli import main

    out = tmp_path / "city"
    assert main(["--no-history", "pipeline", "lsystem", "--preset", "grid", "--maps", "--hd", "--allow-traps",
                 "-o", str(out)]) == 0
    graph = json.loads(out.with_suffix(".json").read_text())
    assert "annotate" in graph["metadata"]
    assert {e.get("cross_section") for e in graph["edges"]} - {None}
    assert check_hdmap(out.with_suffix(".xodr")) == []
    pop = tmp_path / "city_population.npz"
    assert pop.exists()
    assert main(["--no-history", "annotate", str(out.with_suffix(".json")), "--map-file",
                 f"population={pop}", "-o", str(tmp_path / "a.json")]) == 0


def test_signs_follow_the_road_width_at_their_station(tmp_path):
    # an avenue that gains a lane each way 25 m before the junction: sign 301, 30 m back,
    # stands beside the narrower road there, not at the width of the road end
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (-300.0, 0.0), 3: (0.0, 150.0), 4: (0.0, -150.0), 5: (-25.0, 0.0), 6: (300.0, 0.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 2, 5, "avenue", 1, 1, speed_limit=13.9))
    g.add_edge(Edge(2, 5, 1, "avenue", 2, 2, speed_limit=13.9))
    g.add_edge(Edge(3, 1, 3, "street", 1, 1))
    g.add_edge(Edge(4, 1, 4, "street", 1, 1))
    g.add_edge(Edge(5, 1, 6, "avenue", 1, 1, speed_limit=13.9))
    path, root, _ = _build(g, tmp_path)
    assert check_hdmap(path) == []
    road = next(r for r in root.findall("road") if r.get("name") == "edges_1_2")
    sign = next(s for s in road.findall("signals/signal") if s.get("type") == "301")
    end = road.findall("lanes/laneSection")[-1]
    assert len([lane for lane in end.findall("right/lane") if lane.get("type") == "driving"]) == 2
    from roadgen.validate.continuity import RoadEval
    from roadgen.validate.hdmap import _outer_edges

    right, _ = _outer_edges(RoadEval(road), float(sign.get("s")))
    # one 3.5 m lane, bike lane 1.5 m and parking 2.4 m there, then 0.5 m: on the sidewalk
    assert float(sign.get("t")) == pytest.approx(-(3.5 + 1.5 + 2.4 + 0.5)) and right < float(sign.get("t"))


def test_lines_follow_the_cross_slope_of_a_banked_road_end(tmp_path):
    # an avenue climbing at 4 % through a T: the street arm meets the junction plane's
    # cross slope, and its yield line tilts with it instead of sinking into the road
    g = RoadGraph()
    for nid, (x, y, z) in {1: (0.0, 0.0, 0.0), 2: (-150.0, 0.0, -6.0), 3: (150.0, 0.0, 6.0),
                           4: (0.0, 150.0, 0.0)}.items():
        g.add_node(Node(nid, x, y, z=z))
    g.add_edge(Edge(1, 2, 1, "avenue", 1, 1, speed_limit=13.9))
    g.add_edge(Edge(2, 1, 3, "avenue", 1, 1, speed_limit=13.9))
    g.add_edge(Edge(3, 1, 4, "street", 1, 1))
    hd = dataclasses.replace(HD, control=dataclasses.replace(HD.control, stop_share=0.0))
    path, root, _ = _build(g, tmp_path, hd)
    assert check_hdmap(path) == []
    line = next(o for o in root.iter("object") if o.get("name") == "yieldLine")
    road = line.getparent().getparent()
    s = float(line.get("s"))
    rec = [e for e in road.findall("lateralProfile/superelevation") if float(e.get("s")) <= s][-1]
    ds = s - float(rec.get("s"))
    roll = sum(float(rec.get(k)) * ds ** i for i, k in enumerate("abcd"))
    assert abs(roll) > 0.005
    corners = line.findall("outlines/outline/cornerLocal")
    for c in corners:
        assert float(c.get("z")) == pytest.approx(float(c.get("v")) * math.tan(roll), abs=1e-6)
