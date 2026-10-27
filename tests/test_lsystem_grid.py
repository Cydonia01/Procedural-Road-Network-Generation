"""Manhattan-style lsystem grids (stage C1): alternating one-way lines, ring road,
dropped streets, the manhattan_like preset; traffic connectivity and lanes at
mixed one-way junctions."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

import pytest

from cli import main
from roadgen.generators.lsystem import LSystemParams, grid_extent
from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.connectivity import check_traffic_connectivity
from tests.grids import SPACING, grid_cli, node_at, small_grid


def edge_between(g: RoadGraph, a: tuple[float, float], b: tuple[float, float]) -> Edge:
    ids = {node_at(g, *a), node_at(g, *b)}
    return next(e for e in g.edges.values() if {e.from_node, e.to_node} == ids)


# --- one-way patterns -------------------------------------------------------------


def test_alternating_streets():
    g = small_grid(3, 4, oneway_pattern=("none", "alternating"))
    dx, dy = SPACING
    for j in range(4):
        e = edge_between(g, (0, j * dy), (dx, j * dy))
        assert (e.lanes_forward, e.lanes_backward) == (2, 0)  # all lanes forward
        eastbound = g.nodes[e.to_node].x > g.nodes[e.from_node].x
        assert eastbound == (j % 2 == 0)
    avenues = [e for e in g.edges.values() if e.road_class == "avenue"]
    assert all((e.lanes_forward, e.lanes_backward) == (2, 2) for e in avenues)


def test_alternating_avenues_whole_lines():
    g = small_grid(4, 3, oneway_pattern=("alternating", "none"))
    for e in g.edges.values():
        if e.road_class != "avenue":
            continue
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        assert (e.lanes_forward, e.lanes_backward) == (4, 0)
        assert (b.y > a.y) == (round(a.x / SPACING[0]) % 2 == 0)  # whole line one way


def test_lines_count_from_the_seed_point():
    # Grown from the middle: lines are numbered from the seed's, so its own line runs +y.
    p = LSystemParams.preset("grid", start=(4 / 9, 0.5), oneway_pattern=("alternating", "none"))
    from roadgen.generators.lsystem import LSystemGenerator

    g = LSystemGenerator().generate(p, 0)
    for e in g.edges.values():
        if e.road_class == "avenue":
            a, b = g.nodes[e.from_node], g.nodes[e.to_node]
            assert (b.y > a.y) == ((round(a.x / 250.0) - 4) % 2 == 0)
    assert check_traffic_connectivity(g)  # no ring: the edges are traps


def test_one_way_lanes_without_pattern():
    g = small_grid(3, 3, major_lanes=(4, 0))
    for e in g.edges.values():
        if e.road_class == "avenue":
            assert (e.lanes_forward, e.lanes_backward) == (4, 0)


@pytest.mark.parametrize("kw", [
    {"oneway_pattern": ("zigzag", "none")},
    {"oneway_pattern": ("none",)},
    {"boundary": "wrap"},
    {"ring_offset": 0.0},
    {"ring_lanes": (0, 0)},
    {"edge_drop_prob": 1.5},
])
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        LSystemParams(**kw)


def test_grid_extent():
    assert grid_extent(10, 30, (250.0, 80.0)) == (2250.0, 2320.0)
    with pytest.raises(ValueError):
        grid_extent(1, 1, (250.0, 80.0))


# --- preset -----------------------------------------------------------------------


def test_manhattan_like_preset():
    p = LSystemParams.preset("manhattan_like")
    assert p.extent == grid_extent(10, 30, (250.0, 80.0))
    assert p.rotation_deg == pytest.approx(29.0)
    assert p.oneway_pattern == ("alternating", "alternating")
    assert sum(p.major_lanes) == 4 and sum(p.minor_lanes) == 2
    assert p.boundary == "ring"


def test_manhattan_like_is_strongly_connected():
    from roadgen.generators.lsystem import LSystemGenerator

    g = LSystemGenerator().generate(LSystemParams.preset("manhattan_like"), 1)
    assert check_traffic_connectivity(g) == []
    # 10 x 30 grid, every line extended to the ring, 4 corners
    assert len(g.nodes) == 10 * 30 + 2 * (10 + 30) + 4
    assert g.validate() == []


@pytest.mark.parametrize("seed", range(4))
def test_dropped_streets_stay_strongly_connected(seed):
    g = small_grid(5, 12, seed, preset="manhattan_like", edge_drop_prob=0.5)
    streets = sum(e.road_class == "street" for e in g.edges.values())
    assert streets < 4 * 12 + 2 * 12  # grid streets + their extensions to the ring
    assert check_traffic_connectivity(g) == []


def test_edge_drop_leaves_growth_unchanged():
    # Dropping draws from its own random stream: the edges that stay are the same roads.
    full, dropped = small_grid(5, 8, 3), small_grid(5, 8, 3, edge_drop_prob=0.3)

    def roads(g):
        return {tuple(sorted((round(g.nodes[n].x, 6), round(g.nodes[n].y, 6))
                             for n in (e.from_node, e.to_node))) for e in g.edges.values()}

    assert roads(dropped) < roads(full)


# --- ring road ----------------------------------------------------------------------


def test_ring_road():
    a, s, off = 4, 5, 60.0
    g = small_grid(a, s, boundary="ring", ring_offset=off)
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
    assert all(degrees[node_at(g, i * 250.0, j * 80.0)] == 4 for i in range(a) for j in range(s))
    ring = [e for e in g.edges.values() if e.lanes_forward == e.lanes_backward == 2
            and e.road_class == "avenue" and (abs(g.nodes[e.from_node].x + off) < 1e-6
                                             and abs(g.nodes[e.to_node].x + off) < 1e-6)]
    assert len(ring) == s + 1  # left side: corner, s extensions, corner


def test_ring_extensions_follow_road_direction():
    g = small_grid(3, 4, preset="manhattan_like", rotation_deg=0.0)
    grid = {node_at(g, i * 250.0, j * 80.0) for i in range(3) for j in range(4)}
    seen = 0
    for e in g.edges.values():
        if (e.from_node in grid) + (e.to_node in grid) != 1:
            continue  # grid edge or ring edge
        seen += 1
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        inner = a if e.from_node in grid else b
        if e.road_class == "avenue":  # avenue i is northbound iff i is even
            assert (b.y > a.y) == (round(inner.x / 250.0) % 2 == 0)
        else:
            assert (b.x > a.x) == (round(inner.y / 80.0) % 2 == 0)
        assert e.lanes_backward == 0
    assert seen == 2 * (3 + 4)


def test_ring_is_rotated_and_in_map_bounds():
    from roadgen.generators.lsystem import LSystemGenerator

    p = LSystemParams.preset("manhattan_like")
    g = LSystemGenerator().generate(p, 0)
    x0, y0, x1, y1 = LSystemGenerator().map_bounds(p)
    assert all(x0 - 1e-6 <= n.x <= x1 + 1e-6 and y0 - 1e-6 <= n.y <= y1 + 1e-6 for n in g.nodes.values())
    left = [n for n in g.nodes.values() if math.isclose(n.x, min(m.x for m in g.nodes.values()))]
    assert len(left) == 1  # rotated by 29 deg: one corner is leftmost


def test_no_ring_reports_traps():
    errors = check_traffic_connectivity(small_grid(10, 30, 1, preset="manhattan_like", boundary="none"))
    text = "\n".join(errors)
    assert "trap" in text and "no way in" in text and "not strongly connected" in text


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
    g = small_grid(2, 2)
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


@pytest.mark.parametrize("boundary", ["ring", "none"])
def test_every_incoming_lane_has_a_movement(boundary):
    g = small_grid(4, 6, preset="manhattan_like", boundary=boundary)
    b = OpenDriveBuilder()
    b.build(g)
    exits = defaultdict(set)
    for m in b.movements:
        exits[(m.junction_id, m.from_edge)].add(m.from_lane)

    stranded = []
    for node, junction in b.node_to_junction.items():
        incident = g.incident_edges(node)
        outgoing = [e for e in incident if _lanes_leaving(b, e, node)]
        for e in incident:
            lanes = _lanes_arriving(b, e, node)
            has_exit = any(o.id != e.id for o in outgoing)
            if lanes and has_exit:
                missing = set(lanes) - exits[(junction, e.id)]
                stranded += [(node, e.id, sorted(missing))] if missing else []
    assert stranded == []


def _road_lanes(b: OpenDriveBuilder, e: Edge, node: int) -> tuple[list[int], list[int], bool]:
    """(right lane ids, left lane ids, node is where the road's reference line ends)."""
    chain = b.alignment.of_edge(e.id).chain
    k = chain.edge_ids.index(e.id)
    right, left = chain.edges[k].lanes(e)
    at_end = node == chain.nodes[k + 1]
    return [-i for i in range(1, right + 1)], list(range(1, left + 1)), at_end


def _lanes_arriving(b: OpenDriveBuilder, e: Edge, node: int) -> list[int]:
    right, left, at_end = _road_lanes(b, e, node)
    return right if at_end else left


def _lanes_leaving(b: OpenDriveBuilder, e: Edge, node: int) -> list[int]:
    right, left, at_end = _road_lanes(b, e, node)
    return left if at_end else right


# --- CLI ------------------------------------------------------------------------------


def test_cli_pipeline_preset_passes(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert main(["pipeline", "lsystem", "--preset", "manhattan_like", "--seed", "1",
                 "-o", str(tmp_path / "m")]) == 0


def test_cli_pipeline_fails_on_traps_unless_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    w, h = grid_extent(3, 4, SPACING)
    argv = ["pipeline", "lsystem", "--preset", "manhattan_like", "--set", f"extent=[{w}, {h}]",
            "--set", "boundary=none", "-o", str(tmp_path / "m")]
    assert main(argv) == 1
    assert main(argv + ["--allow-traps"]) == 0


def test_cli_set_overrides_preset(tmp_path):
    out = tmp_path / "g.json"
    assert main(["generate", "lsystem", "--preset", "manhattan_like", "--set", "extent=[500, 240]",
                 "--set", "oneway_pattern=[alternating, none]", "--set", "minor_lanes=[1, 1]",
                 "-o", str(out)]) == 0
    params = load_json(out).metadata["params"]
    assert params["oneway_pattern"] == ["alternating", "none"]
    assert params["extent"] == [500, 240] and params["boundary"] == "ring"


def test_cli_small_grid(tmp_path):
    out = tmp_path / "g.json"
    assert main(["generate", "lsystem", *grid_cli(3, 2), "-o", str(out)]) == 0
    assert len(load_json(out).nodes) == 6


def test_cli_replaying_a_manhattan_run_explains_its_removal(tmp_path):
    import yaml

    spec = tmp_path / "run.yaml"
    spec.write_text(yaml.safe_dump({"command": ["generate", "manhattan"], "args": {}, "config": {}}))
    with pytest.raises(SystemExit, match="manhattan_like"):
        main(["run", str(spec)])
    spec.write_text(yaml.safe_dump({"command": ["generate", "lsystem"], "args": {},
                                    "config": {"generators": {"manhattan": {"n_avenues": 3}}}}))
    with pytest.raises(SystemExit, match="manhattan_like"):
        main(["run", str(spec)])


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

    b = OpenDriveBuilder()
    b.build(small_grid(4, 6, 2, preset="manhattan_like", angle_jitter_deg=1.0))
    b.write(tmp_path / "m.xodr")
    results = drive_movements(tmp_path / "m.xodr")
    assert [r.describe() for r in results if not r.ok] == []
