from __future__ import annotations

import math
from pathlib import Path

import pytest

from cli import main
from roadgen.graph.io import SCHEMA_VERSION, load_json, save_json
from roadgen.graph.model import Edge, Node, RoadGraph

DATA = Path(__file__).parent / "data"


def make_graph() -> RoadGraph:
    """1 --(10)-- 2 --(11)-- 3, plus 2 --(12)-- 4 diagonal."""
    g = RoadGraph(metadata={"seed": 42})
    g.add_node(Node(1, 0.0, 0.0, "dead_end"))
    g.add_node(Node(2, 10.0, 0.0, "junction"))
    g.add_node(Node(3, 10.0, 20.0, "dead_end"))
    g.add_node(Node(4, 13.0, 4.0, "dead_end"))
    g.add_edge(Edge(10, 1, 2, "avenue", 2, 2))
    g.add_edge(Edge(11, 2, 3, "street", 1, 0, lane_width=3.0, speed_limit=8.3))
    g.add_edge(Edge(12, 4, 2, "street", 1, 1))
    return g


def test_valid_graph_has_no_errors():
    assert make_graph().validate() == []


def test_degree_and_neighbors():
    g = make_graph()
    assert g.degree(1) == 1
    assert g.degree(2) == 3
    assert g.degree(3) == 1
    assert g.neighbors(2) == [1, 3, 4]
    assert [e.id for e in g.incident_edges(2)] == [10, 11, 12]


def test_length_and_heading():
    g = make_graph()
    assert g.edge_length(10) == pytest.approx(10.0)
    assert g.edge_length(11) == pytest.approx(20.0)
    assert g.edge_length(12) == pytest.approx(5.0)
    assert g.edge_heading(10) == pytest.approx(0.0)
    assert g.edge_heading(11) == pytest.approx(math.pi / 2)
    assert g.edge_heading(12) == pytest.approx(math.atan2(-4.0, -3.0))


def test_duplicate_ids_rejected():
    g = make_graph()
    with pytest.raises(ValueError):
        g.add_node(Node(1, 5.0, 5.0))
    with pytest.raises(ValueError):
        g.add_edge(Edge(10, 1, 3, "street", 1, 1))


def test_json_round_trip(tmp_path):
    g = make_graph()
    path = tmp_path / "g.json"
    save_json(g, path)
    loaded = load_json(path)
    assert loaded == g
    assert '"schema_version": %d' % SCHEMA_VERSION in path.read_text()


def test_load_rejects_wrong_schema_version(tmp_path):
    path = tmp_path / "g.json"
    path.write_text('{"schema_version": 999, "nodes": [], "edges": []}')
    with pytest.raises(ValueError, match="schema_version"):
        load_json(path)


def test_validate_missing_node():
    g = make_graph()
    g.add_edge(Edge(20, 1, 99, "street", 1, 1))
    errors = g.validate()
    assert len(errors) == 1
    assert "missing node 99" in errors[0]


def test_validate_zero_length():
    g = make_graph()
    g.add_node(Node(5, 0.0, 0.0))  # same position as node 1
    g.add_edge(Edge(20, 1, 5, "street", 1, 1))
    errors = g.validate()
    assert len(errors) == 1
    assert "zero length" in errors[0]


@pytest.mark.parametrize("a,b", [(1, 2), (2, 1)])
def test_validate_duplicate_edge(a, b):
    g = make_graph()
    g.add_edge(Edge(20, a, b, "street", 1, 1))
    errors = g.validate()
    assert len(errors) == 1
    assert "duplicates edge 10" in errors[0]


def test_validate_no_lanes():
    g = make_graph()
    g.edges[11].lanes_forward = 0
    errors = g.validate()
    assert len(errors) == 1
    assert "no lanes" in errors[0]


def test_to_networkx():
    nxg = make_graph().to_networkx()
    assert nxg.number_of_nodes() == 4
    assert nxg.number_of_edges() == 3
    assert nxg.edges[2, 3]["id"] == 11
    assert nxg.edges[2, 3]["length"] == pytest.approx(20.0)


def test_hand_written_json_plots(tmp_path):
    g = load_json(DATA / "three_nodes.json")
    assert g.validate() == []
    out = tmp_path / "three_nodes.png"
    assert main(["plot-graph", str(DATA / "three_nodes.json"), str(out), "--node-ids"]) == 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
