"""Dead-end pass after growth (stage R3): extend, trim, or leave for a turning circle."""

from __future__ import annotations

import numpy as np
import pytest

from roadgen.generators.lsystem import (
    AVENUE_Y,
    STREET_X,
    LSystemGenerator,
    LSystemParams,
    _Growth,
)
from roadgen.metrics.network import network_metrics
from roadgen.validate.connectivity import check_traffic_connectivity

P = LSystemParams(extent=(1000.0, 600.0), block_size=(200.0, 100.0))


def growth(*edges: tuple[tuple[float, float], tuple[float, float], int]) -> _Growth:
    g = _Growth(P, np.random.default_rng(0))
    nodes: dict[tuple[float, float], int] = {}
    for a, b, axis in edges:
        for pt in (a, b):
            if pt not in nodes:
                nodes[pt] = g.add_node(pt)
        g.add_edge(nodes[a], nodes[b], axis)
    return g


def node_at(g: _Growth, pt: tuple[float, float]) -> int:
    return next(n for n, q in g.pos.items() if q == pt and g.adj[n])


def test_dead_end_is_extended_to_the_road_ahead():
    # A street along x ending 200 m short of an avenue (reach 1.5 x 200 m).
    g = growth(((300.0, 100.0), (300.0, 500.0), AVENUE_Y), ((100.0, 300.0), (500.0, 300.0), AVENUE_Y),
               ((300.0, 300.0), (100.0, 300.0), STREET_X), ((700.0, 100.0), (700.0, 500.0), AVENUE_Y))
    g.dead_end_pass()
    end = node_at(g, (500.0, 300.0))
    assert len(g.adj[end]) == 2  # joined to the avenue at x = 700
    assert any(q == (700.0, 300.0) for n, q in g.pos.items() if g.adj[n])


def test_short_stub_is_trimmed():
    # A 60 m stub off an avenue, pointing at open ground with nothing within reach.
    g = growth(((500.0, 0.0), (500.0, 300.0), AVENUE_Y), ((500.0, 300.0), (500.0, 600.0), AVENUE_Y),
               ((500.0, 300.0), (440.0, 300.0), STREET_X))
    stub = node_at(g, (440.0, 300.0))
    g.dead_end_pass()
    assert not g.adj[stub]
    assert len(g.adj[node_at(g, (500.0, 300.0))]) == 2


def test_long_road_stays_for_a_bulb():
    # 450 m to the edge, nothing within reach: too long to trim.
    g = growth(((500.0, 300.0), (950.0, 300.0), STREET_X), ((500.0, 0.0), (500.0, 300.0), AVENUE_Y),
               ((500.0, 300.0), (500.0, 600.0), AVENUE_Y))
    end = node_at(g, (950.0, 300.0))
    g.dead_end_pass()
    assert len(g.adj[end]) == 1


@pytest.mark.parametrize("preset, seed", [("organic_town", 1), ("grid_organic", 2), ("skewed_grid", 0)])
def test_presets_have_few_dead_ends_and_no_traps(preset, seed):
    g = LSystemGenerator().generate(LSystemParams.preset(preset), seed)
    m = network_metrics(g)
    assert m["counts"]["dead_ends"] <= 0.03 * len(g.nodes)
    assert not [e for e in check_traffic_connectivity(g) if "trap" in e]


def test_off_keeps_dead_ends():
    on = LSystemGenerator().generate(LSystemParams.preset("organic_town"), 1)
    off = LSystemGenerator().generate(LSystemParams.preset("organic_town", dead_ends=False), 1)
    assert network_metrics(off)["counts"]["dead_ends"] > network_metrics(on)["counts"]["dead_ends"]
