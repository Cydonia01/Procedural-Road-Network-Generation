"""The junction generator (stage C6): one JunctionArt-style junction per seed."""

from __future__ import annotations

import math
from collections import Counter

import pytest

from cli import main
from roadgen.generators.junction import PRESETS, JunctionGenerator, JunctionLayoutParams
from roadgen.graph.io import graph_to_dict, load_json
from roadgen.metrics.network import network_metrics
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.connectivity import check_traffic_connectivity
from tests.test_junction_variety import build_and_check


def gen(seed: int = 0, preset: str = "random", **kw):
    return JunctionGenerator().generate(JunctionLayoutParams.preset(preset, **kw), seed)


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_builds_validates_and_drives(preset, tmp_path):
    g = gen(0, preset)
    assert g.validate() == []
    build_and_check(g, tmp_path)


def test_random_fills_every_leg_count():
    legs = Counter(gen(seed).degree(1) for seed in range(200))
    assert set(legs) == {3, 4, 5, 6, 7}


def test_shapes_follow_the_presets():
    t = gen(0, "t")
    heads = sorted(round(math.degrees(math.atan2(t.nodes[o].y, t.nodes[o].x)) % 360) for o in t.neighbors(1))
    assert heads == [0, 90, 180]
    assert gen(3, "six_way").degree(1) == 6
    pair = gen(1, "offset_t")
    assert pair.degree(1) == pair.degree(2) == 3 and network_metrics(pair)["offset_t_pairs"] == 1


def test_arms_keep_their_minimum_angle():
    for seed in range(50):
        g = gen(seed, min_angle_deg=40.0)
        n = g.nodes[1]
        heads = sorted(math.atan2(g.nodes[o].y - n.y, g.nodes[o].x - n.x) % (2 * math.pi) for o in g.neighbors(1))
        gaps = [(heads[(i + 1) % len(heads)] - h) % (2 * math.pi) for i, h in enumerate(heads)]
        assert min(gaps) >= math.radians(40.0) - 1e-9


def test_lanes_and_bulbs():
    g = gen(2, "t", lane_probs=(0.0, 1.0, 0.0, 0.0))  # every leg 1 + 1
    assert all(e.total_lanes in (1, 2) for e in g.edges.values())
    assert len(g.metadata["culdesacs"]) == 3
    assert check_traffic_connectivity(g) == []  # every leg end turns around
    bare = gen(2, "t", lane_probs=(0.0, 1.0, 0.0, 0.0), culdesacs=False)
    assert "culdesacs" not in bare.metadata and network_metrics(bare)["counts"]["dead_ends"] == 3


def test_deterministic():
    assert graph_to_dict(gen(7)) == graph_to_dict(gen(7))


def test_impossible_layout_is_reported():
    with pytest.raises(ValueError, match="no buildable junction"):
        gen(0, "random", legs=7, min_angle_deg=60.0, max_tries=3)


@pytest.mark.parametrize("kw", [{"legs": 8}, {"leg_weights": (1, 0, 0)}, {"angles_deg": (0.0, 90.0)},
                                {"lane_probs": (1.0, 0.0, 0.0, 0.0)}, {"leg_length": 0.0}])
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        JunctionLayoutParams(**kw)


def test_cli_pipeline_junction(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    prefix = tmp_path / "k"
    assert main(["pipeline", "junction", "--preset", "k", "--seed", "3", "--allow-traps",
                 "-o", str(prefix)]) == 0
    assert load_json(prefix.with_suffix(".json")).metadata["generator"] == "junction"
    OpenDriveBuilder().build(load_json(prefix.with_suffix(".json")))
