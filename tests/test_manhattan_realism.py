"""Phase 4: one-way patterns, multi-lane avenues, presets, ring road, traffic connectivity."""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from cli import main
from roadgen.generators.manhattan import ManhattanGenerator, ManhattanParams, node_id
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder, oriented_lanes
from roadgen.validate.connectivity import check_traffic_connectivity


def gen(seed: int = 0, **kw) -> RoadGraph:
    return ManhattanGenerator().generate(ManhattanParams(**kw), seed)


def grid_edge(g: RoadGraph, p: ManhattanParams, a: tuple[int, int], b: tuple[int, int]) -> Edge:
    ids = {node_id(p, *a), node_id(p, *b)}
    return next(e for e in g.edges.values() if {e.from_node, e.to_node} == ids)


# --- one-way patterns -------------------------------------------------------------


def test_alternating_streets():
    p = ManhattanParams(3, 4, street_lanes=(1, 1), street_oneway_pattern="alternating")
    g = ManhattanGenerator().generate(p, 0)
    for j in range(4):
        e = grid_edge(g, p, (0, j), (1, j))
        assert (e.lanes_forward, e.lanes_backward) == (2, 0)  # all lanes forward
        eastbound = e.from_node == node_id(p, 0, j)
        assert eastbound == (j % 2 == 0)
    avenues = [e for e in g.edges.values() if e.road_class == "avenue"]
    assert all((e.lanes_forward, e.lanes_backward) == (2, 2) for e in avenues)


def test_alternating_avenues():
    p = ManhattanParams(4, 3, avenue_lanes=(2, 2), avenue_oneway_pattern="alternating")
    g = ManhattanGenerator().generate(p, 0)
    for i in range(4):
        e = grid_edge(g, p, (i, 0), (i, 1))
        assert (e.lanes_forward, e.lanes_backward) == (4, 0)
        assert (e.from_node == node_id(p, i, 0)) == (i % 2 == 0)


def test_four_lane_one_way_avenues_without_pattern():
    p = ManhattanParams(3, 3, avenue_lanes=(4, 0))
    g = ManhattanGenerator().generate(p, 0)
    for e in g.edges.values():
        if e.road_class == "avenue":
            assert (e.lanes_forward, e.lanes_backward) == (4, 0)
            assert g.nodes[e.to_node].y > g.nodes[e.from_node].y  # all northbound


@pytest.mark.parametrize("kw", [
    {"avenue_oneway_pattern": "zigzag"},
    {"boundary_mode": "wrap"},
    {"ring_offset": 0.0},
    {"ring_lanes": (0, 0)},
])
def test_invalid_new_params(kw):
    with pytest.raises(ValueError):
        ManhattanParams(3, 3, **kw)


# --- preset -----------------------------------------------------------------------


def test_preset_values():
    p = ManhattanParams.preset("manhattan_like")
    assert (p.n_avenues, p.n_streets) == (10, 30)
    assert p.rotation_deg == pytest.approx(29.0)
    assert (p.avenue_spacing, p.street_spacing) == (250.0, 80.0)
    assert p.street_oneway_pattern == p.avenue_oneway_pattern == "alternating"
    assert sum(p.avenue_lanes) == 4 and sum(p.street_lanes) == 2
    assert p.boundary_mode == "loop"


def test_preset_overrides_and_unknown():
    p = ManhattanParams.preset("manhattan_like", n_avenues=3, n_streets=5, rotation_deg=0.0)
    assert (p.n_avenues, p.n_streets, p.rotation_deg) == (3, 5, 0.0)
    assert p.street_oneway_pattern == "alternating"
    with pytest.raises(ValueError, match="unknown preset"):
        ManhattanParams.preset("paris_like")


def test_preset_is_strongly_connected():
    g = ManhattanGenerator().generate(ManhattanParams.preset("manhattan_like"), 1)
    assert check_traffic_connectivity(g) == []


@pytest.mark.parametrize("seed", range(4))
def test_preset_with_dropped_streets_stays_strongly_connected(seed):
    p = ManhattanParams.preset("manhattan_like", n_avenues=5, n_streets=12, edge_drop_prob=0.5)
    g = ManhattanGenerator().generate(p, seed)
    assert sum(e.road_class == "street" for e in g.edges.values()) < 4 * 12 + 2 * 12
    assert check_traffic_connectivity(g) == []


# --- boundary modes -----------------------------------------------------------------


def test_loop_adds_ring():
    a, s, off = 4, 5, 60.0
    p = ManhattanParams(a, s, ring_offset=off, boundary_mode="loop")
    g = ManhattanGenerator().generate(p, 0)
    grid_edges = a * (s - 1) + (a - 1) * s
    ring_nodes = 2 * a + 2 * s + 4
    assert len(g.nodes) == a * s + ring_nodes
    assert len(g.edges) == grid_edges + (2 * a + 2 * s) + ring_nodes
    xs = [n.x for n in g.nodes.values()]
    ys = [n.y for n in g.nodes.values()]
    assert (min(xs), max(xs)) == pytest.approx((-off, (a - 1) * 250.0 + off))
    assert (min(ys), max(ys)) == pytest.approx((-off, (s - 1) * 80.0 + off))
    degrees = Counter()
    for e in g.edges.values():
        degrees[e.from_node] += 1
        degrees[e.to_node] += 1
    assert min(degrees.values()) >= 2  # no dead ends anywhere
    assert all(degrees[node_id(p, i, j)] == 4 for i in range(a) for j in range(s))


def test_loop_extensions_follow_road_direction():
    p = ManhattanParams.preset("manhattan_like", n_avenues=3, n_streets=4, rotation_deg=0.0)
    g = ManhattanGenerator().generate(p, 0)
    grid = {node_id(p, i, j) for i in range(3) for j in range(4)}
    for e in g.edges.values():
        ends_in_grid = (e.from_node in grid) + (e.to_node in grid)
        if ends_in_grid != 1:
            continue  # grid edge or ring edge
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        if e.road_class == "avenue":  # spur: avenue i is northbound iff i is even
            i = round(a.x / 250.0)
            assert (b.y > a.y) == (i % 2 == 0)
        else:
            j = round(a.y / 80.0)
            assert (b.x > a.x) == (j % 2 == 0)
        assert e.lanes_backward == 0


def test_dead_end_mode_reports_traps():
    p = ManhattanParams.preset("manhattan_like", boundary_mode="dead_end")
    errors = check_traffic_connectivity(ManhattanGenerator().generate(p, 1))
    text = "\n".join(errors)
    # Corner (0, 29): northbound avenue 0 meets westbound street 29 -> trap.
    assert f"node {node_id(p, 0, 29)}: trap" in text
    # Corner (0, 0): both roads leave it -> nothing ever enters.
    assert f"node {node_id(p, 0, 0)}: no way in" in text
    assert "not strongly connected" in text


# --- connectivity validator -------------------------------------------------------------


def line_graph(*edges: tuple[int, int, int, int]) -> RoadGraph:
    g = RoadGraph()
    nodes = {n for e in edges for n in e[:2]}
    for n in sorted(nodes):
        g.add_node(Node(n, float(n * 100), float((n % 2) * 37)))
    for k, (a, b, f, bw) in enumerate(edges, 1):
        g.add_edge(Edge(k, a, b, "street", f, bw))
    return g


def test_connectivity_dead_end_is_a_trap():
    errors = check_traffic_connectivity(line_graph((1, 2, 1, 1)))
    assert any("node 2: trap" in e for e in errors)
    assert any("node 1: trap" in e for e in errors)


def test_connectivity_one_way_sink():
    g = line_graph((1, 2, 1, 0), (3, 2, 1, 0), (2, 4, 1, 1))
    errors = check_traffic_connectivity(g)
    assert any("node 4: trap" in e for e in errors)
    assert any("no way in" in e for e in errors)


def test_connectivity_loop_without_uturns_is_fine():
    # A two-way square loop: clockwise and counter-clockwise traffic can never
    # swap, but no vehicle is ever stuck, so this is not an error.
    g = gen(n_avenues=2, n_streets=2)
    assert check_traffic_connectivity(g) == []


def test_connectivity_one_way_cycle():
    g = RoadGraph()
    for n, (x, y) in enumerate([(0, 0), (100, 0), (100, 100), (0, 100)], 1):
        g.add_node(Node(n, float(x), float(y)))
    for k, (a, b) in enumerate([(1, 2), (2, 3), (3, 4), (4, 1)], 1):
        g.add_edge(Edge(k, a, b, "street", 2, 0))
    assert check_traffic_connectivity(g) == []


# --- builder: mixed one-way / two-way junctions -------------------------------------------


def test_one_way_avenue_into_t_uses_every_lane():
    # 4-lane northbound avenue ends at a one-way eastbound 2-lane street.
    g = RoadGraph()
    for n in [Node(1, 0, 0), Node(2, 0, -80), Node(3, -80, 0), Node(4, 80, 0)]:
        g.add_node(n)
    g.add_edge(Edge(1, 2, 1, "avenue", 4, 0))
    g.add_edge(Edge(2, 3, 1, "street", 2, 0))
    g.add_edge(Edge(3, 1, 4, "street", 2, 0))
    b = OpenDriveBuilder()
    b.build(g)
    turns = sorted((m.from_lane, m.to_lane) for m in b.movements if m.from_edge == 1)
    assert turns == [(-4, -2), (-3, -1), (-2, -1), (-1, -1)]
    assert {m.kind for m in b.movements if m.from_edge == 1} == {"right"}


@pytest.mark.parametrize("boundary", ["loop", "dead_end"])
def test_every_incoming_lane_has_a_movement(boundary):
    p = ManhattanParams.preset("manhattan_like", n_avenues=4, n_streets=6, boundary_mode=boundary)
    g = ManhattanGenerator().generate(p, 0)
    b = OpenDriveBuilder()
    b.build(g)
    exits = defaultdict(set)
    for m in b.movements:
        exits[(m.junction_id, m.from_edge)].add(m.from_lane)

    stranded = []
    for node, junction in b.node_to_junction.items():
        incident = g.incident_edges(node)
        outgoing = [e for e in incident if _lanes_leaving(e, node)]
        for e in incident:
            lanes = _lanes_arriving(e, node)
            has_exit = any(o.id != e.id for o in outgoing)
            if lanes and has_exit:
                missing = set(lanes) - exits[(junction, e.id)]
                stranded += [(node, e.id, sorted(missing))] if missing else []
    assert stranded == []


def _lanes_arriving(e: Edge, node: int) -> list[int]:
    right, left, reverse = oriented_lanes(e)
    end_node = e.from_node if reverse else e.to_node
    return [-i for i in range(1, right + 1)] if node == end_node else list(range(1, left + 1))


def _lanes_leaving(e: Edge, node: int) -> list[int]:
    right, left, reverse = oriented_lanes(e)
    start_node = e.to_node if reverse else e.from_node
    return [-i for i in range(1, right + 1)] if node == start_node else list(range(1, left + 1))


# --- CLI ------------------------------------------------------------------------------


def test_cli_pipeline_preset_passes(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert main(["pipeline", "manhattan", "--preset", "manhattan_like", "--seed", "1",
                 "-o", str(tmp_path / "m")]) == 0


def test_cli_pipeline_fails_on_traps_unless_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    argv = ["pipeline", "manhattan", "--preset", "manhattan_like", "--avenues", "3",
            "--streets", "4", "--boundary", "dead_end", "-o", str(tmp_path / "m")]
    assert main(argv) == 1
    assert main(argv + ["--allow-traps"]) == 0


def test_cli_generate_requires_size_without_preset(tmp_path):
    with pytest.raises(SystemExit):
        main(["generate", "manhattan", "-o", str(tmp_path / "g.json")])


def test_cli_flags_override_preset(tmp_path):
    out = tmp_path / "g.json"
    assert main(["generate", "manhattan", "--preset", "manhattan_like", "--avenues", "3",
                 "--streets", "4", "--street-oneway", "none", "--street-lanes", "1", "1",
                 "-o", str(out)]) == 0
    from roadgen.graph.io import load_json

    params = load_json(out).metadata["params"]
    assert params["street_oneway_pattern"] == "none"
    assert params["avenue_oneway_pattern"] == "alternating"
    assert params["n_avenues"] == 3


def _rm_available() -> bool:
    from roadgen.sim.esmini import esmini_library

    try:
        esmini_library("esminiRMLib")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_esmini_drives_every_movement_in_small_preset(tmp_path):
    from roadgen.sim.drive_check import drive_movements

    p = ManhattanParams.preset("manhattan_like", n_avenues=4, n_streets=6, jitter=3.0)
    b = OpenDriveBuilder()
    b.build(ManhattanGenerator().generate(p, 2))
    b.write(tmp_path / "m.xodr")
    results = drive_movements(tmp_path / "m.xodr")
    assert [r.describe() for r in results if not r.ok] == []
