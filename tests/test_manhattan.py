from __future__ import annotations

import math
from collections import Counter

import networkx as nx
import pytest

from cli import main
from roadgen.generators.base import RoadNetworkGenerator
from roadgen.generators.manhattan import ManhattanGenerator, ManhattanParams, node_id
from roadgen.graph.io import graph_to_dict
from roadgen.graph.model import RoadGraph


def generate(seed: int = 0, **kw) -> RoadGraph:
    kw.setdefault("n_avenues", 4)
    kw.setdefault("n_streets", 6)
    return ManhattanGenerator().generate(ManhattanParams(**kw), seed)


def test_is_a_generator():
    assert isinstance(ManhattanGenerator(), RoadNetworkGenerator)
    assert ManhattanGenerator.params_type is ManhattanParams


@pytest.mark.parametrize("a,s", [(2, 2), (3, 5), (10, 10), (1, 5), (5, 1)])
def test_counts(a, s):
    g = generate(n_avenues=a, n_streets=s)
    assert len(g.nodes) == a * s
    roads = Counter(e.road_class for e in g.edges.values())
    assert roads["avenue"] == a * (s - 1)
    assert roads["street"] == (a - 1) * s
    assert g.validate() == []


def test_spacing_and_attributes():
    g = generate(n_avenues=3, n_streets=4, avenue_spacing=200.0, street_spacing=60.0)
    for i in range(3):
        for j in range(4):
            n = g.nodes[node_id(ManhattanParams(3, 4), i, j)]
            assert (n.x, n.y) == pytest.approx((i * 200.0, j * 60.0))
    for e in g.edges.values():
        if e.road_class == "avenue":
            assert g.edge_length(e.id) == pytest.approx(60.0)
            assert g.edge_heading(e.id) == pytest.approx(math.pi / 2)
            assert (e.lanes_forward, e.lanes_backward, e.speed_limit) == (2, 2, 13.9)
        else:
            assert g.edge_length(e.id) == pytest.approx(200.0)
            assert g.edge_heading(e.id) == pytest.approx(0.0)
            assert (e.lanes_forward, e.lanes_backward, e.speed_limit) == (1, 1, 11.1)


def test_rotation_and_origin():
    p = ManhattanParams(3, 3, rotation_deg=90.0, origin=(10.0, -5.0))
    g = ManhattanGenerator().generate(p, 0)
    n = g.nodes[node_id(p, 1, 0)]  # one avenue spacing along x, rotated onto +y
    assert (n.x, n.y) == pytest.approx((10.0, 245.0))
    n = g.nodes[node_id(p, 0, 1)]  # one street spacing along y, rotated onto -x
    assert (n.x, n.y) == pytest.approx((-70.0, -5.0))
    for e in g.edges.values():
        expected = math.pi if e.road_class == "avenue" else math.pi / 2
        assert g.edge_heading(e.id) == pytest.approx(expected)


def test_node_kinds():
    g = generate(n_avenues=3, n_streets=3)
    kinds = Counter(n.kind for n in g.nodes.values())
    assert kinds == {"auto": 4, "junction": 5}  # 4 corners, 4 borders + 1 center
    line = generate(n_avenues=1, n_streets=4)
    assert Counter(n.kind for n in line.nodes.values()) == {"dead_end": 2, "auto": 2}


def test_jitter_bounded_and_seeded():
    base = generate()
    g = generate(seed=3, jitter=2.0)
    moved = 0
    for nid, n in g.nodes.items():
        dx, dy = n.x - base.nodes[nid].x, n.y - base.nodes[nid].y
        assert abs(dx) <= 2.0 and abs(dy) <= 2.0
        moved += (dx, dy) != (0.0, 0.0)
    assert moved == len(g.nodes)
    assert generate(seed=4, jitter=2.0) != g


@pytest.mark.parametrize("kw", [{}, {"jitter": 3.0, "edge_drop_prob": 0.4, "rotation_deg": 17.0}])
def test_deterministic(kw):
    a, b = generate(seed=11, **kw), generate(seed=11, **kw)
    assert a == b
    assert graph_to_dict(a) == graph_to_dict(b)


@pytest.mark.parametrize("seed", range(5))
def test_edge_drop_keeps_connectivity(seed):
    full = generate()
    g = generate(seed=seed, edge_drop_prob=0.5)
    assert nx.is_connected(g.to_networkx())
    assert len(g.edges) < len(full.edges)
    avenues = [e for e in g.edges.values() if e.road_class == "avenue"]
    assert len(avenues) == 4 * 5  # avenues are never dropped
    assert g.validate() == []


def test_drop_everything_leaves_a_spanning_set():
    g = generate(n_avenues=4, n_streets=6, edge_drop_prob=1.0)
    assert nx.is_connected(g.to_networkx())
    # Avenues are intact columns; exactly one street must remain between neighbors.
    assert sum(e.road_class == "street" for e in g.edges.values()) == 3


@pytest.mark.parametrize(
    "kw",
    [
        {"n_avenues": 1, "n_streets": 1},
        {"n_avenues": 2, "n_streets": 2, "street_spacing": 0.0},
        {"n_avenues": 2, "n_streets": 2, "edge_drop_prob": 1.5},
        {"n_avenues": 2, "n_streets": 2, "street_lanes": (0, 0)},
        {"n_avenues": 2, "n_streets": 2, "jitter": -1.0},
    ],
)
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        ManhattanParams(**kw)


# --- CLI / full pipeline ------------------------------------------------------------


def test_cli_generate(tmp_path):
    out = tmp_path / "grid.json"
    assert main(["generate", "manhattan", "--avenues", "5", "--streets", "10",
                 "--seed", "1", "-o", str(out)]) == 0
    assert out.is_file()


@pytest.mark.parametrize(
    "size,extra",
    [
        ((2, 2), []),
        ((3, 5), []),
        ((10, 10), []),
        # Dropped boundary segments and same-direction one-ways create traps.
        ((3, 5), ["--jitter", "4", "--edge-drop", "0.3", "--rotation", "30", "--allow-traps"]),
        ((4, 4), ["--avenue-lanes", "3", "0", "--street-lanes", "0", "1", "--allow-traps"]),
    ],
)
def test_pipeline_passes_validation(tmp_path, monkeypatch, size, extra):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    prefix = tmp_path / "grid"
    argv = ["pipeline", "manhattan", "--avenues", str(size[0]), "--streets", str(size[1]),
            "--seed", "2", "-o", str(prefix), *extra]
    assert main(argv) == 0
    for suffix in (".json", ".png", ".xodr", ".roadmap.json"):
        assert prefix.with_suffix(suffix).is_file()


def _rm_available() -> bool:
    from roadgen.sim.esmini import esmini_library

    try:
        esmini_library("esminiRMLib")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_esmini_drives_every_movement_in_grid(tmp_path):
    from roadgen.opendrive.builder import OpenDriveBuilder
    from roadgen.sim.drive_check import drive_movements

    g = generate(seed=5, n_avenues=3, n_streets=5, jitter=4.0, edge_drop_prob=0.3, rotation_deg=30.0)
    builder = OpenDriveBuilder()
    builder.build(g)
    builder.write(tmp_path / "grid.xodr")
    results = drive_movements(tmp_path / "grid.xodr")
    assert len(results) == len(builder.movements)
    assert [r.describe() for r in results if not r.ok] == []
