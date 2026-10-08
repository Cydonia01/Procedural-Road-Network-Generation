from __future__ import annotations

from dataclasses import dataclass

import pytest

from cli import main
from roadgen.generators import base
from roadgen.generators.base import (
    RoadNetworkGenerator,
    build_params,
    generator_names,
    get_generator,
    load_params,
    register,
)
from roadgen.generators.lsystem import LSystemParams
from roadgen.generators.manhattan import ManhattanParams
from roadgen.graph.io import load_json
from roadgen.graph.model import Node, RoadGraph


def test_builtin_generators_are_registered():
    assert {"manhattan", "lsystem"} <= set(generator_names())
    assert get_generator("lsystem").params_type is LSystemParams
    assert get_generator("manhattan").params_type is ManhattanParams


def test_unknown_generator():
    with pytest.raises(ValueError, match="unknown generator"):
        get_generator("voronoi")


def test_register_decorator(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", dict(base._REGISTRY))

    @dataclass(frozen=True)
    class DotParams:
        x: float = 1.0

    @register("dot")
    class DotGenerator(RoadNetworkGenerator[DotParams]):
        params_type = DotParams

        def generate(self, params, seed):
            g = RoadGraph()
            g.add_node(Node(1, params.x, 0.0))
            return g

    assert "dot" in generator_names()
    assert get_generator("dot").generate(DotParams(2.0), 0).nodes[1].x == 2.0
    with pytest.raises(ValueError, match="already registered"):
        register("dot")(type("Other", (DotGenerator,), {}))


def test_load_params_from_yaml(tmp_path):
    flat = tmp_path / "flat.yaml"
    flat.write_text("n_avenues: 3\nn_streets: 4\navenue_lanes: [3, 0]\n")
    p = load_params(ManhattanParams, flat)
    assert (p.n_avenues, p.avenue_lanes) == (3, (3, 0))  # list -> tuple

    nested = tmp_path / "nested.yaml"
    nested.write_text("preset: manhattan_like\nparams:\n  n_avenues: 4\n  n_streets: 5\n")
    p = load_params(ManhattanParams, nested, overrides={"rotation_deg": 10.0})
    assert (p.n_avenues, p.rotation_deg, p.boundary_mode) == (4, 10.0, "loop")


def test_build_params_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown LSystemParams fields"):
        build_params(LSystemParams, {"bogus": 1})


def test_cli_generators_lists_everything(capsys):
    assert main(["generators"]) == 0
    out = capsys.readouterr().out
    assert "lsystem:" in out and "manhattan:" in out
    assert "grid_organic" in out and "manhattan_like" in out
    assert "snap_radius = 15.0" in out


def test_cli_generate_lsystem_with_yaml_and_set(tmp_path):
    cfg = tmp_path / "ls.yaml"
    cfg.write_text("preset: grid\nparams:\n  extent: [500, 160]\n")
    out = tmp_path / "g.json"
    assert main(["generate", "lsystem", "--params", str(cfg), "--set", "block_size=[250, 80]",
                 "--set", "rotation_deg=15", "--seed", "3", "-o", str(out)]) == 0
    g = load_json(out)
    assert len(g.nodes) == 3 * 3
    assert g.metadata["params"]["rotation_deg"] == 15
    assert g.metadata["seed"] == 3


def test_cli_manhattan_flags_still_work(tmp_path):
    out = tmp_path / "m.json"
    assert main(["generate", "manhattan", "--avenues", "2", "--streets", "3",
                 "--set", "jitter=1.5", "-o", str(out)]) == 0
    assert load_json(out).metadata["params"]["jitter"] == 1.5


def test_cli_bad_set_value(tmp_path):
    with pytest.raises(SystemExit):
        main(["generate", "lsystem", "--set", "no_equals_sign", "-o", str(tmp_path / "x.json")])
