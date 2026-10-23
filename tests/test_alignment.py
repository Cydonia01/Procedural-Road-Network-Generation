from __future__ import annotations

import math
from itertools import pairwise
from typing import Optional

import numpy as np
import pytest
from shapely.geometry import LineString

from roadgen.geometry import design
from roadgen.geometry.alignment import align_chain, corner_shape, network_alignment
from roadgen.geometry.chains import find_chains, is_mergeable, junction_nodes
from roadgen.geometry.design import DesignParams
from roadgen.geometry.planar import Pose
from roadgen.graph.model import Edge, Node, RoadGraph

V = 13.9  # [m/s]


def graph_of(points: dict[int, tuple[float, float]], edges: list[tuple],
             kinds: Optional[dict[int, str]] = None) -> RoadGraph:
    """edges: (id, from, to) or (id, from, to, lanes_forward, lanes_backward)."""
    g = RoadGraph()
    for nid, (x, y) in points.items():
        g.add_node(Node(nid, x, y, kind=(kinds or {}).get(nid, "auto")))
    for e in edges:
        eid, a, b, *lanes = e
        lf, lb = lanes or (1, 1)
        g.add_edge(Edge(eid, a, b, "street", lf, lb, 3.5, V))
    return g


# --- chains ------------------------------------------------------------------------

def test_l_shape_is_one_chain_oriented_from_the_lower_end():
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (300, 300)}, [(10, 2, 3), (11, 2, 1)])
    (c,) = find_chains(g)
    assert c.nodes == (1, 2, 3)
    assert [(ce.edge_id, ce.forward) for ce in c.edges] == [(11, False), (10, True)]
    assert junction_nodes(g, [c]) == []


def test_unmergeable_nodes_stay_junctions():
    pts = {1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (100, 100), 5: (300, 0), 6: (200, -30)}
    # 2 has three roads; 3 is a forced junction; 5 turns back by 165 degrees.
    g = graph_of({**pts, 7: (210, 3)}, [(1, 1, 2), (2, 2, 3), (3, 2, 4), (4, 3, 5), (5, 5, 7)],
                 kinds={3: "junction"})
    assert not is_mergeable(g, 2) and not is_mergeable(g, 3) and not is_mergeable(g, 5)
    chains = find_chains(g)
    assert sorted(len(c.edges) for c in chains) == [1, 1, 1, 1, 1]
    assert junction_nodes(g, chains) == [2, 3, 5]


def test_one_way_into_two_way_is_not_merged():
    g = graph_of({1: (0, 0), 2: (100, 0), 3: (200, 50)}, [(1, 1, 2, 1, 0), (2, 2, 3, 1, 1)])
    assert not is_mergeable(g, 2)


def test_one_way_chain_runs_in_travel_direction():
    # Travel 3 -> 2 -> 1, stored in mixed edge directions.
    g = graph_of({1: (0, 0), 2: (100, 0), 3: (200, 50)}, [(1, 1, 2, 0, 2), (2, 3, 2, 2, 0)])
    (c,) = find_chains(g)
    assert c.nodes == (3, 2, 1)
    assert [ce.lanes(g.edges[ce.edge_id]) for ce in c.edges] == [(2, 0), (2, 0)]


def test_ring_is_cut_into_two_roads():
    g = graph_of({5: (0, 0), 6: (100, 0), 7: (50, 80)}, [(1, 5, 6), (2, 6, 7), (3, 7, 5)])
    chains = find_chains(g)
    assert len(chains) == 2 and all(c.start != c.end for c in chains)
    assert sorted(eid for c in chains for eid in c.edge_ids) == [1, 2, 3]
    assert len(junction_nodes(g, chains)) == 2


def test_loop_back_to_the_same_junction_is_split():
    # Node 1 is a junction (three roads); 2-3-4 lead back to it.
    pts = {1: (0, 0), 2: (100, 0), 3: (100, 100), 4: (0, 100), 9: (-100, 0)}
    g = graph_of(pts, [(1, 1, 2), (2, 2, 3), (3, 3, 4), (4, 4, 1), (5, 1, 9)])
    chains = find_chains(g)
    assert all(c.start != c.end for c in chains)
    assert junction_nodes(g, chains) == [1, 3]


def test_every_edge_is_in_exactly_one_chain():
    pts = {i: (100.0 * (i % 4), 100.0 * (i // 4)) for i in range(12)}
    edges = [(k, a, b) for k, (a, b) in enumerate(
        [(0, 1), (1, 2), (2, 3), (3, 7), (7, 11), (11, 10), (10, 9), (9, 8), (8, 4), (4, 0), (1, 5), (5, 9)])]
    g = graph_of(pts, edges)
    ids = [eid for c in find_chains(g) for eid in c.edge_ids]
    assert sorted(ids) == sorted(g.edges)


# --- alignment -----------------------------------------------------------------------

def _check_fit(g: RoadGraph, ca, tol: float = 1e-6) -> None:
    al = ca.alignment
    for gap, dh, dk in al.joint_errors():
        assert gap < tol and dh < 1e-9 and dk < 1e-9
    first, last = g.nodes[ca.chain.start], g.nodes[ca.chain.end]
    h0, h1 = al.start.hdg, al.end.hdg
    assert al.start.x == pytest.approx(first.x + ca.trim_start * math.cos(h0), abs=tol)
    assert al.end.x == pytest.approx(last.x - ca.trim_end * math.cos(h1), abs=1e-4)
    assert al.end.y == pytest.approx(last.y - ca.trim_end * math.sin(h1), abs=1e-4)
    ranges = [ca.edge_range(eid) for eid in ca.chain.edge_ids]
    assert ranges[0][0] == 0.0 and ranges[-1][1] == pytest.approx(al.length)
    for (_, s1), (s0, _) in pairwise(ranges):
        assert s1 == pytest.approx(s0)


def test_long_l_corner_gets_the_design_radius():
    g = graph_of({1: (0, 0), 2: (400, 0), 3: (400, 400)}, [(1, 1, 2), (2, 2, 3)])
    (ca,) = network_alignment(g).chains
    _check_fit(g, ca)
    (corner,) = ca.corners
    r = design.min_radius(V, 0.0) + 1.75  # the inner lane centre is 1.75 m inside
    assert corner.scale == 1.0 and corner.radius == pytest.approx(r)
    assert corner.lane_radius == pytest.approx(r - 1.75)
    assert corner.advisory_speed is None
    assert max(abs(p.k0) for p in ca.alignment.pieces) == pytest.approx(1 / r)
    assert [p.kind for p in ca.alignment.pieces] == ["line", "spiral", "arc", "spiral", "line"]
    s = ca.node_station(2)
    assert s == pytest.approx((corner.s_start + corner.s_end) / 2)


def test_short_legs_shrink_the_corner_and_set_an_advisory():
    g = graph_of({1: (0, 0), 2: (60, 0), 3: (60, 60)}, [(1, 1, 2), (2, 2, 3)])
    (ca,) = network_alignment(g).chains
    _check_fit(g, ca)
    (corner,) = ca.corners
    assert corner.scale < 1.0
    assert corner.advisory_speed is not None and corner.advisory_speed < V
    assert corner.radius == pytest.approx((design.min_radius(V, 0.0) + 1.75) * corner.scale)
    assert design.max_speed(corner.lane_radius, 0.0) < V
    # The corner uses the whole room on one leg: no straight piece is left there.
    assert ca.alignment.length < 120.0


def test_corners_sharing_a_short_segment_both_fit():
    g = graph_of({1: (0, 0), 2: (200, 0), 3: (200, 40), 4: (400, 40)}, [(1, 1, 2), (2, 2, 3), (3, 3, 4)])
    (ca,) = network_alignment(g).chains
    _check_fit(g, ca)
    a, b = ca.corners
    assert a.scale == pytest.approx(b.scale) and a.scale < 1
    assert b.s_start >= a.s_end - 1e-9


def test_trims_at_a_junction_end():
    pts = {1: (0, 0), 2: (200, 0), 3: (200, 200), 4: (-100, 0), 5: (0, 100), 6: (0, -100)}
    g = graph_of(pts, [(1, 1, 2), (2, 2, 3), (3, 1, 4), (4, 1, 5), (5, 1, 6)])
    na = network_alignment(g)
    assert na.junctions == (1,)
    ca = na.of_edge(1)
    assert ca.trim_start == pytest.approx(na.trims[1][1])
    _check_fit(g, ca)


def test_edge_station_for_a_reversed_edge():
    g = graph_of({1: (0, 0), 2: (300, 0), 3: (300, 300)}, [(10, 2, 3), (11, 2, 1)])
    (ca,) = network_alignment(g).chains
    # Edge 11 runs 2 -> 1, against the chain: its distance 300 m is the chain start.
    assert ca.edge_station(11, 300.0) == pytest.approx(0.0)
    assert ca.edge_station(11, 0.0) == pytest.approx(ca.node_station(2))
    assert ca.edge_station(10, 300.0) == pytest.approx(ca.length)


def test_curve_keeps_clear_of_a_road_inside_the_corner():
    pts = {1: (0, 0), 2: (400, 0), 3: (400, 400), 4: (355, 25), 5: (375, 45)}
    g = graph_of(pts, [(1, 1, 2), (2, 2, 3), (3, 4, 5)])
    free = network_alignment(graph_of({k: pts[k] for k in (1, 2, 3)}, [(1, 1, 2), (2, 2, 3)]))
    na = network_alignment(g)
    ca = na.of_edge(1)
    assert ca.corners[0].scale < free.chains[0].corners[0].scale
    smp = ca.alignment.sample(0.5)
    dist = LineString(smp.xy()).distance(LineString([pts[4], pts[5]]))
    assert dist >= 3.5 + 3.5 + DesignParams().road_clearance - 0.05


def test_radius_cap_and_max_offset():
    g = graph_of({1: (0, 0), 2: (400, 0), 3: (400, 400)}, [(1, 1, 2), (2, 2, 3)])
    (ca,) = network_alignment(g, radius_caps={2: 40.0}).chains
    assert ca.corners[0].radius == pytest.approx(40.0)
    (cb,) = network_alignment(g, DesignParams(max_corner_offset=10.0)).chains
    shape = corner_shape(math.pi / 2, design.min_radius(V, 0.0) + 1.75,
                         cb.corners[0].spiral_length / cb.corners[0].scale)
    assert shape.offset * cb.corners[0].scale == pytest.approx(10.0)


def test_too_short_edge_reports_like_the_builder():
    pts = {1: (0, 0), 2: (8, 0), 3: (-50, 0), 4: (0, 50), 5: (8, 50), 6: (60, 0)}
    g = graph_of(pts, [(1, 1, 2), (2, 1, 3), (3, 1, 4), (4, 2, 5), (5, 2, 6)])
    with pytest.raises(ValueError, match="too short for its junctions"):
        network_alignment(g)


def test_corner_shape_tangent_and_offset():
    shape = corner_shape(math.pi / 2, 100.0, 1e-9 + 0.0001)  # practically an arc
    assert shape.tangent == pytest.approx(100.0, rel=1e-4)
    assert shape.offset == pytest.approx(100.0 * (math.sqrt(2) - 1), rel=1e-4)
    gentle = corner_shape(math.radians(4), 100.0, 30.0)  # spirals alone turn more than 4 degrees
    assert gentle.arc == 0.0 and gentle.kappa == pytest.approx(math.radians(4) / 30.0)
    pieces = gentle.pieces(Pose(0, 0, 0), 1.0)
    assert pieces[-1].end.hdg == pytest.approx(math.radians(4))


@pytest.mark.parametrize("seed", range(8))
def test_random_polylines_fit_continuously(seed):
    rng = np.random.default_rng(seed)
    pts, x, y, h = {}, 0.0, 0.0, 0.0
    for i in range(1, 9):
        pts[i] = (x, y)
        h += math.radians(rng.uniform(-120, 120))
        step = rng.uniform(20, 200)
        x, y = x + step * math.cos(h), y + step * math.sin(h)
    g = graph_of(pts, [(i, i, i + 1) for i in range(1, 8)])
    for c in find_chains(g):
        ca = align_chain(g, c, {})
        _check_fit(g, ca)
        smp = ca.alignment.sample(1.0)
        assert np.all(np.isfinite(smp.x))
