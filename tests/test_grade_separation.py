"""Grade separation and tunnels (stage C8)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cli import main
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.geometry.grade_separation import (
    GradeSeparationParams,
    clearance_at,
    find_crossings,
    separate,
)
from roadgen.graph.io import graph_from_dict, graph_to_dict, load_json, save_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.drape import drape
from roadgen.maps.model import MapSet
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.clearance import check_clearance
from roadgen.validate.continuity import check_continuity
from roadgen.validate.topology import check_topology
from tests.test_lsystem_maps import GRID, X, Y, elevation, population


def crossing_graph() -> RoadGraph:
    """A highway along x crossed by a street at x = -100 (no shared node), a side
    street joining the highway at x = 0 and an at-grade street further west."""
    g = RoadGraph(metadata={"name": "x"})
    pts = {1: (-600, 0), 2: (-300, 0), 3: (0, 0), 4: (300, 0), 5: (600, 0),
           10: (-100, -300), 11: (-100, -60), 12: (-100, 60), 13: (-100, 300), 20: (0, 250)}
    for n, (x, y) in pts.items():
        g.add_node(Node(n, float(x), float(y)))
    edges = [(1, 2, "highway"), (2, 3, "highway"), (3, 4, "highway"), (4, 5, "highway"),
             (10, 11, "street"), (11, 12, "street"), (12, 13, "street"), (3, 20, "street")]
    for k, (a, b, cls) in enumerate(edges, 1):
        g.add_edge(Edge(k, a, b, cls, *((2, 2) if cls == "highway" else (1, 1))))
    return g


def profile_grades(g: RoadGraph, eid: int) -> np.ndarray:
    d, z = g.edge_profile(eid)
    return np.abs(np.diff(z) / np.diff(d))


def test_find_crossings_puts_the_higher_class_on_top():
    (c,) = find_crossings(crossing_graph())
    assert (c.upper, c.lower) == (2, 6)  # highway edge 2-3 over street 11-12
    assert (c.x, c.y) == pytest.approx((-100.0, 0.0))
    assert c.d_upper == pytest.approx(200.0) and c.d_lower == pytest.approx(60.0)


def test_separate_raises_the_upper_road_smoothly():
    p = GradeSeparationParams()
    g = separate(crossing_graph(), p)
    (c,) = find_crossings(g)
    assert clearance_at(g, c) == pytest.approx(p.clearance, abs=1e-3)
    for eid in (1, 2, 3):  # ramps on the highway stay within max_grade (smoothstep, sampled)
        assert profile_grades(g, eid).max() <= p.max_grade * 1.05
    assert g.edges[6].profile == [] and g.nodes[11].z == g.nodes[12].z == 0.0  # lower road untouched
    assert g.edges[2].bridges and g.edges[2].bridges[0][0] < 200.0 < g.edges[2].bridges[0][1]
    assert 0 < g.nodes[3].z < p.clearance  # on the ramp
    assert g.edges[8].profile and profile_grades(g, 8).max() <= p.max_grade * 1.05  # side street ramps down
    assert g.metadata["overpasses"][0]["upper"] == 2


def test_separate_without_crossings_changes_nothing():
    g = crossing_graph()
    del g.edges[5], g.edges[6], g.edges[7]
    out = separate(g)
    assert graph_to_dict(out) == graph_to_dict(g)


def test_overpass_builds_and_passes_clearance(tmp_path):
    g = crossing_graph()
    b = OpenDriveBuilder()
    b.build(g)
    b.write(tmp_path / "flat.xodr")
    assert check_clearance(tmp_path / "flat.xodr")  # two roads at one height
    b = OpenDriveBuilder()
    b.build(separate(g))
    b.write(tmp_path / "sep.xodr")
    assert check_clearance(tmp_path / "sep.xodr") == []
    assert check_topology(tmp_path / "sep.xodr") == [] and check_continuity(tmp_path / "sep.xodr") == []
    assert "<bridge" in (tmp_path / "sep.xodr").read_text()


def test_skewed_crossing_gets_a_wider_top():
    g = crossing_graph()
    g.nodes[10].x, g.nodes[13].x = -400.0, 200.0  # the street crosses at about 45 deg
    g.nodes[11].x, g.nodes[12].x = -160.0, -40.0
    g.nodes[20].y = -250.0  # the side street to the south, out of the street's way
    (c,) = find_crossings(g)
    assert c.sin < 0.9
    span = separate(g).edges[c.upper].bridges[0]
    straight = separate(crossing_graph()).edges[2].bridges[0]
    assert span[1] - span[0] > straight[1] - straight[0]


def test_invalid_params():
    with pytest.raises(ValueError):
        GradeSeparationParams(max_grade=0.0)


def test_cli_separate(tmp_path):
    save_json(crossing_graph(), tmp_path / "x.json")
    assert main(["separate", str(tmp_path / "x.json")]) == 0
    assert load_json(tmp_path / "x.separated.json").metadata["overpasses"]


# --- lsystem: freeways --------------------------------------------------------------------


BASE = {"extent": (1000.0, 600.0), "block_size": (100.0, 60.0), "start": (0.5, 0.5),
        "highway_length": 100.0, "highway_ray_length": 400.0}


def grow(**kw) -> RoadGraph:
    # A peak to the north-east: the highway runs diagonally across the street grid.
    peak = 2000.0 + 15000.0 * np.exp(-((X - 950.0) ** 2 + (Y - 550.0) ** 2) / 300.0 ** 2)
    return LSystemGenerator().generate(LSystemParams(**{**BASE, **kw}), 0, MapSet(population=population(peak)))


def test_separated_highways_have_crossings_and_no_access():
    at_grade, separated = grow(), grow(highway_crossings="separated")
    assert find_crossings(at_grade) == []
    crossings = find_crossings(separated)
    assert crossings and all(separated.edges[c.upper].road_class == "highway" for c in crossings)
    # Highway nodes join only highways, apart from the seed point.
    seed = 1
    for n in separated.nodes:
        classes = {e.road_class for e in separated.incident_edges(n)}
        if "highway" in classes and n != seed:
            assert classes == {"highway"}
    g = separate(separated)
    assert all(clearance_at(g, c) >= GradeSeparationParams().clearance - 1e-3 for c in find_crossings(g))
    OpenDriveBuilder().build(g)


def test_invalid_crossings_mode():
    with pytest.raises(ValueError):
        LSystemParams(highway_crossings="maybe")


# --- tunnels ----------------------------------------------------------------------------------


def ridge(height: float = 80.0):
    """A ridge across the area at x = 600, rising to height."""
    return elevation(height * np.exp(-((X - 600.0) / 60.0) ** 2))


def test_tunnels_through_a_ridge(tmp_path):
    maps = MapSet(elevation=ridge(), population=population(5000.0))
    kw = {**BASE, "start": (0.2, 0.5), "highway_max_grade": 0.05}
    blocked = LSystemGenerator().generate(LSystemParams(**kw), 0, maps)
    tunneled = LSystemGenerator().generate(LSystemParams(**kw, tunnels=True), 0, maps)
    assert "tunnels" not in blocked.metadata
    assert tunneled.metadata.get("tunnels")
    east = max(n.x for e in tunneled.edges.values() if e.road_class == "highway"
               for n in (tunneled.nodes[e.from_node], tunneled.nodes[e.to_node]))
    assert east > 650.0  # through the ridge
    draped = drape(tunneled, maps.elevation)
    spans = [(eid, t) for eid, e in draped.edges.items() for t in e.tunnels]
    assert spans and all(eid in tunneled.metadata["tunnels"] for eid, _ in spans)
    for eid, _ in spans:  # a constant grade through the hill
        d, z = draped.edge_profile(eid)
        assert np.allclose(np.diff(z) / np.diff(d), (z[-1] - z[0]) / d[-1], atol=1e-3)
    b = OpenDriveBuilder()
    b.build(draped)
    b.write(tmp_path / "t.xodr")
    assert "<tunnel" in (tmp_path / "t.xodr").read_text()


def test_tunnels_round_trip_and_validate():
    g = crossing_graph()
    g.edges[1].tunnels = [(10.0, 50.0)]
    back = graph_from_dict(graph_to_dict(g))
    assert back.edges[1].tunnels == [(10.0, 50.0)]
    assert "tunnels" not in graph_to_dict(crossing_graph())["edges"][0]  # unset: left out
    g.edges[1].tunnels = [(10.0, 9999.0)]
    assert any("tunnel outside" in e for e in g.validate())
