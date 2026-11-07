"""Stage B8: lane use, left-turn pockets, banned turns, arrows, junction-surface fill."""

from __future__ import annotations

import dataclasses
import json

import pytest
from lxml import etree

from roadgen.geometry.cross_section import (
    CrossSectionParams,
    TurnLaneParams,
    pocket_ends,
)
from roadgen.geometry.junction_space import node_trims
from roadgen.generators.repair import repair_short_edges, short_edges, stub_roads
from roadgen.graph.io import load_json, save_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.junctions import lane_use
from roadgen.validate.continuity import RoadEval, _width, check_continuity
from roadgen.validate.hdmap import check_hdmap
from roadgen.validate.topology import check_topology

HD = HdMapParams(enabled=True)


def _build(graph: RoadGraph, tmp_path, hd: HdMapParams = HD, name: str = "net"):
    path = tmp_path / f"{name}.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot(), json.loads(path.with_suffix(".roadmap.json").read_text()), b


def _cross(cls: str = "avenue", lanes: int = 2, arm: float = 200.0, speed: float = 13.9) -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-arm, 0.0), 3: (arm, 0.0), 4: (0.0, arm), 5: (0.0, -arm)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, cls, lanes, lanes, speed_limit=speed))
    return g


def test_pockets_need_a_left_exit_room_and_a_pocket_class():
    tl = TurnLaneParams()
    g = _cross()
    assert pocket_ends(g, 1, tl) == {1, 2, 3, 4}
    assert pocket_ends(_cross(arm=80.0), 1, tl) == frozenset()  # too short for 40 + 15 m and the far end
    assert pocket_ends(_cross("street"), 1, tl) == frozenset()  # streets get none by default
    assert pocket_ends(g, 1, dataclasses.replace(tl, left_pockets=False)) == frozenset()
    g.nodes[1].control = "right_before_left"
    assert pocket_ends(g, 1, tl) == frozenset()


def test_trims_count_the_pocket():
    cs = CrossSectionParams()
    plain = node_trims(_cross(), 1, cross_section=cs)
    pockets = node_trims(_cross(), 1, cross_section=dataclasses.replace(cs, turn_lanes=TurnLaneParams()))
    assert all(pockets[e] > plain[e] for e in plain)


def test_pocket_lane_geometry_and_ids(tmp_path):
    path, root, sidecar, _ = _build(_cross(), tmp_path)
    assert check_topology(path) == [] and check_continuity(path) == [] and check_hdmap(path) == []
    road = next(r for r in root.findall("road") if r.get("name") == "edge_1")  # arrives at the junction at its end
    length = RoadEval(road).length
    sections = road.findall("lanes/laneSection")

    def right(sec):
        return {int(lane.get("id")): lane for lane in sec.findall("right/lane")}

    # the pocket position (-1, no median on avenues) runs the whole road: none where 0 m wide
    first, last = right(sections[0]), right(sections[-1])
    assert first[-1].get("type") == "none" and last[-1].get("type") == "driving"
    assert [first[k].get("type") for k in (-2, -3)] == ["driving", "driving"]  # through lanes keep their ids
    ds = length - float(sections[-1].get("s"))
    assert _width(last[-1], 0.0) == pytest.approx(3.5) and ds >= 40.0 - 1e-6  # the full-width pocket
    taper = next(s for s in sections if right(s)[-1].get("type") == "driving")
    assert float(sections[-1].get("s")) - float(taper.get("s")) == pytest.approx(15.0)  # pocket_taper
    assert not right(taper)[-1].findall("link/predecessor")  # it widens from 0: no link
    (use,) = [u for u in sidecar["lane_use"] if u["road"] == int(road.get("id")) and u["contact"] == "end"]
    assert use["pockets"] == [-1]
    assert use["lanes"]["-1"] == ["left"] and "left" not in use["lanes"]["-2"] + use["lanes"]["-3"]


def test_pocket_next_to_a_median(tmp_path):
    _, root, sidecar, _ = _build(_cross("highway", 2, 300.0, 22.2), tmp_path)
    road = next(r for r in root.findall("road") if r.get("name") == "edge_1")
    last = {int(lane.get("id")): lane.get("type") for lane in road.findall("lanes/laneSection")[-1].findall("right/lane")}
    assert [last[k] for k in (-1, -2, -3, -4)] == ["median", "driving", "driving", "driving"]
    (use,) = [u for u in sidecar["lane_use"] if u["road"] == int(road.get("id")) and u["contact"] == "end"]
    assert use["pockets"] == [-2]


def test_lane_use_from_movements(tmp_path):
    _, _, sidecar, b = _build(load_json("tests/fixtures/4way_avenue_x_street.json"), tmp_path)
    uses = lane_use(b.movements)
    for rec in sidecar["lane_use"]:
        side = -1 if rec["contact"] == "end" else 1
        assert {int(k): v for k, v in rec["lanes"].items()} == uses[(rec["road"], side)]


def test_arrows_match_lane_use(tmp_path):
    _, root, sidecar, _ = _build(_cross(), tmp_path)
    arrows = [o for o in root.iter("object") if o.get("name", "").startswith("arrow")]
    assert arrows
    uses = {(u["road"], int(k)): v for u in sidecar["lane_use"] for k, v in u["lanes"].items()}
    for a in arrows:
        lane = int(a.find("validity").get("fromLane"))
        road = int(a.getparent().getparent().get("id"))
        kinds = [k for k in uses[(road, lane)] if k != "uturn"]
        assert a.get("name") == "arrow_" + "_".join(k for k in ("left", "straight", "right") if k in kinds)
        assert float(a.get("hdg")) == pytest.approx(0.0 if lane < 0 else 3.141593, abs=1e-5)


def _highway_with_minor_roads() -> RoadGraph:
    """A 2+2 highway (priority road) through a junction with two street arms, plus a loop
    of streets so the minor roads stay reachable without their left turns."""
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (-400.0, 0.0), 3: (400.0, 0.0), 4: (0.0, 200.0), 5: (0.0, -200.0),
           6: (-400.0, 200.0), 7: (400.0, -200.0), 8: (-400.0, -200.0), 9: (400.0, 200.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 2, 1, "highway", 2, 2, speed_limit=16.7))
    g.add_edge(Edge(2, 1, 3, "highway", 2, 2, speed_limit=16.7))
    g.add_edge(Edge(3, 1, 4, "street", 1, 1))
    g.add_edge(Edge(4, 5, 1, "street", 1, 1))
    for eid, (a, b) in enumerate(((4, 6), (6, 2), (5, 7), (7, 3), (2, 8), (8, 5), (3, 9), (9, 4)), 5):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    return g


def test_right_in_right_out_on_a_wide_highway(tmp_path):
    path, root, sidecar, _ = _build(_highway_with_minor_roads(), tmp_path)
    assert check_hdmap(path) == []
    bans = [b for b in sidecar["banned"] if b["reason"] == "right_in_right_out"]
    assert bans
    jid = bans[0]["junction"]
    minor_kinds = {m["kind"] for m in sidecar["movements"] if m["junction_id"] == jid
                   and any(m["from_road"] == b["from"][0] for b in bans if b["from"][0] not in {x["to"][0] for x in bans})}
    assert minor_kinds <= {"right"}
    signs = [s for s in root.iter("signal") if s.get("type") in ("209", "214")]
    assert {(s.get("type"), s.get("subtype")) for s in signs} >= {("209", "20")}


def test_seeded_bans_keep_every_road_reachable_and_no_movement_takes_them(tmp_path):
    hd = dataclasses.replace(HD, control=dataclasses.replace(HD.control, ban_share=1.0, ban_left_highway=False))
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    path, root, sidecar, _ = _build(g, tmp_path, hd)
    # a single junction of dead-end arms: banning a left there would cut the network
    assert "banned" not in sidecar
    g = _highway_with_minor_roads()
    path, _, sidecar, _ = _build(g, tmp_path, hd, "loop")
    assert check_hdmap(path) == []
    banned = {(tuple(b["from"]), tuple(b["to"])) for b in sidecar["banned"]}
    assert banned and {b["reason"] for b in sidecar["banned"]} == {"seeded"}
    for m in sidecar["movements"]:  # no junction movement takes a banned turn
        fc = "end" if m["from_lane"] < 0 else "start"
        tc = "start" if m["to_lane"] < 0 else "end"
        assert ((m["from_road"], fc), (m["to_road"], tc)) not in banned


def test_junction_surface_fill(tmp_path):
    path, root, _, b = _build(_cross(), tmp_path)
    patches = [o for o in root.iter("object") if o.get("type") == "patch"]
    assert patches and len(patches) == b.fills
    for p in patches:
        outline = p.find("outlines/outline")
        assert outline.get("closed") == "true" and outline.get("fillType") == "asphalt"
        assert len(outline.findall("cornerLocal")) >= 3
        assert p.getparent().getparent().get("junction") != "-1"  # on a connecting road
    assert check_hdmap(path) == []


def test_junction_fill_in_the_scene_instead_of_patches(tmp_path):

    from shapely.geometry import Polygon

    from roadgen.scene.junctions import junction_fill_mesh, read_fills

    # The .xodr always has the patch objects; the scene draws the same areas under them.
    path, root, sidecar, b = _build(_cross(), tmp_path)
    fills = read_fills(path.with_suffix(".roadmap.json"))
    assert len(fills) == b.fills == len(sidecar["junction_fills"]) > 0
    assert len([o for o in root.iter("object") if o.get("type") == "patch"]) == len(fills)
    assert check_hdmap(path) == []
    mesh = junction_fill_mesh(fills)
    area = sum(Polygon([p[:2] for p in t]).area for t in mesh.tris)
    assert area == pytest.approx(sum(Polygon([p[:2] for p in f]).area for f in fills), rel=1e-6)
    assert all(n[2] > 0.99 for n in mesh.normals)  # a flat junction: facing up
    z = {round(p[2], 6) for t in mesh.tris for p in t}
    assert max(z) <= max(p[2] for f in fills for p in f) - 0.02 + 1e-6  # under the lanes


def test_hd_off_unchanged_by_b8(tmp_path):
    _, root, sidecar, _ = _build(_cross(), tmp_path, HdMapParams(enabled=False))
    assert "lane_use" not in sidecar and "banned" not in sidecar
    assert not list(root.iter("object"))


def _rm_available() -> bool:
    try:
        from roadgen.sim.esmini import esmini_library

        esmini_library("esminiRMLib")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_drive_check_through_pockets(tmp_path):
    from roadgen.sim.drive_check import drive_lanes, drive_movements

    path, _, sidecar, _ = _build(_cross(), tmp_path)
    results = drive_movements(path)
    pockets = {(u["road"], lane) for u in sidecar["lane_use"] for lane in u["pockets"]}
    from_pockets = [r for r in results if (r.movement["from_road"], r.movement["from_lane"]) in pockets]
    assert len(from_pockets) == 4 and all(r.movement["kind"] == "left" for r in from_pockets)
    assert all(r.ok for r in results)
    lanes = drive_lanes(path)
    assert all(r.ok for r in lanes)
    # one load of the RoadManager for both passes (the CLI's drive-check) drives the same
    from roadgen.sim.drive_check import drive_all

    both, both_lanes = drive_all(path)
    assert [(r.route, r.final_lane, r.max_jump) for r in both] == [(r.route, r.final_lane, r.max_jump)
                                                                   for r in results]
    assert [(r.road, r.lane, r.max_deviation) for r in both_lanes] == [(r.road, r.lane, r.max_deviation)
                                                                       for r in lanes]


def test_bans_only_on_approaches_with_arriving_traffic(tmp_path):
    # one-way grid junctions: an arm traffic only leaves on cannot lose a turn
    hd = dataclasses.replace(HD, control=dataclasses.replace(HD.control, ban_share=1.0))
    from tests.grids import small_grid

    g = small_grid(4, 4, preset="manhattan_like")
    path, _, sidecar, _ = _build(g, tmp_path, hd)
    assert check_hdmap(path) == []
    arriving = {(m["from_road"], "end" if m["from_lane"] < 0 else "start") for m in sidecar["movements"]}
    assert all(tuple(b["from"]) in arriving for b in sidecar.get("banned", []))


def test_pockets_stay_out_of_curves(tmp_path):
    from roadgen.validate.dynamics import check_dynamics

    g = _cross()
    # the west arm bends 60 deg, 110 m before the junction: its last edge starts in the curve
    g.nodes[2].x, g.nodes[2].y = -110.0, 0.0
    g.add_node(Node(9, -110.0 - 150.0 * 0.5, -150.0 * 0.866))
    g.add_edge(Edge(9, 9, 2, "avenue", 2, 2, speed_limit=13.9))
    path, root, sidecar, _ = _build(g, tmp_path)
    assert check_dynamics(path, 3.0) == [] and check_hdmap(path) == []
    road = next(r for r in root.findall("road") if r.get("name") == "edges_1_9" or r.get("name") == "edges_9_1")
    rid = int(road.get("id"))
    corners = [c for c in sidecar["corners"] if c["road"] == rid]
    pockets = [lane for u in sidecar["lane_use"] if u["road"] == rid for lane in u["pockets"]]
    assert corners and pockets
    sections = road.findall("lanes/laneSection")
    bounds = [float(x.get("s")) for x in sections] + [float(road.get("length"))]
    for k, sec in enumerate(sections):
        for lane in pockets:
            side = "right" if lane < 0 else "left"
            el = sec.find(f"{side}/lane[@id='{lane}']")
            if el is not None and el.get("type") == "driving":  # pocket or taper: clear of every curve
                for c in corners:
                    assert bounds[k + 1] <= c["s_start"] + 1e-6 or bounds[k] >= c["s_end"] - 1e-6


def _short_block(length: float = 32.0) -> RoadGraph:
    """An avenue cross whose east arm is a block of `length` m to a street junction: it
    keeps enough road between its trims without the other arms' pockets, too little with
    them (a stub road, repair.MIN_ROAD_LENGTH)."""
    g = RoadGraph()
    pts = {1: (0.0, 0.0), 2: (-200.0, 0.0), 3: (length, 0.0), 4: (0.0, 200.0), 5: (0.0, -200.0),
           6: (length, 100.0), 7: (length, -100.0)}
    for nid, (x, y) in pts.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b, cls) in enumerate(((2, 1, "avenue"), (1, 3, "avenue"), (1, 4, "avenue"), (5, 1, "avenue"),
                                       (3, 6, "street"), (7, 3, "street")), 1):
        lanes = 2 if cls == "avenue" else 1
        g.add_edge(Edge(eid, a, b, cls, lanes, lanes, speed_limit=13.9))
    return g


def test_short_blocks_lose_their_pockets_instead_of_being_repaired(tmp_path):
    cs = HD.active_cross_section
    g = _short_block()
    assert short_edges(g, cross_section=cs) == [] and stub_roads(g, cross_section=cs) == [2]
    assert repair_short_edges(g, cross_section=cs) == 0
    assert set(g.edges) == set(range(1, 7))  # nothing collapsed or removed
    left_out = g.metadata["no_pockets"]
    assert left_out and all(n == 1 for n, _ in left_out)
    assert stub_roads(g, cross_section=cs) == []
    kept = pocket_ends(g, 1, cs.turn_lanes)
    assert kept == {1, 3, 4} - {e for _, e in left_out}
    save_json(g, tmp_path / "g.json")
    for _, e in left_out:  # each pocket left out was needed
        trial = load_json(tmp_path / "g.json")
        trial.metadata["no_pockets"] = [p for p in left_out if p[1] != e]
        assert stub_roads(trial, cross_section=cs) == [2]
    _, _, sidecar, b = _build(g, tmp_path)
    junction = b.node_to_junction[1]
    assert sum(bool(u["pockets"]) for u in sidecar["lane_use"] if u["junction"] == junction) == len(kept)


def test_pockets_left_out_only_with_turn_lanes():
    g = _short_block()
    no_pockets = dataclasses.replace(HD.active_cross_section,
                                     turn_lanes=dataclasses.replace(HD.turn_lanes, left_pockets=False))
    assert stub_roads(g, cross_section=no_pockets) == []
    assert repair_short_edges(g, cross_section=no_pockets) == 0
    assert "no_pockets" not in g.metadata


def test_arrows_are_polylines_over_shared_corners(tmp_path):
    """B9: each arrow is a few markings through shared outline corners (esmini draws a
    marking through all its corners, OSI reports it as one road marking)."""
    _, root, _, _ = _build(_cross(), tmp_path)
    arrows = [o for o in root.iter("object") if o.get("name", "").startswith("arrow")]
    assert arrows
    for a in arrows:
        corners = [(c.get("u"), c.get("v")) for c in a.findall("outlines/outline/cornerLocal")]
        ids = {c.get("id") for c in a.findall("outlines/outline/cornerLocal")}
        assert len(set(corners)) == len(corners)  # no corner twice
        refs = [[r.get("id") for r in m.findall("cornerReference")] for m in a.findall("markings/marking")]
        assert all(len(r) >= 2 and set(r) <= ids for r in refs)
        assert {i for r in refs for i in r} == ids  # every corner is used
        if a.get("name") == "arrow_straight":
            assert len(corners) == 4 and len(refs) == 2
