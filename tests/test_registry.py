from __future__ import annotations

from dataclasses import dataclass

import pytest

from cli import main
from roadgen.generators import base
from roadgen.generators.base import (
    assign_node_kinds,
    RoadNetworkGenerator,
    build_params,
    generator_names,
    get_generator,
    load_params,
    register,
)
from roadgen.generators.lsystem import LSystemParams
from roadgen.graph.io import load_json
from roadgen.graph.model import Edge, Node, RoadGraph


def test_builtin_generators_are_registered():
    assert "lsystem" in generator_names()
    assert "manhattan" not in generator_names()  # removed in stage C: lsystem grows grids
    assert get_generator("lsystem").params_type is LSystemParams


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
    flat.write_text("extent: [500, 240]\nmajor_lanes: [3, 0]\n")
    p = load_params(LSystemParams, flat)
    assert (p.extent, p.major_lanes) == ((500, 240), (3, 0))  # list -> tuple

    nested = tmp_path / "nested.yaml"
    nested.write_text("preset: manhattan_like\nparams:\n  extent: [750, 400]\n")
    p = load_params(LSystemParams, nested, overrides={"rotation_deg": 10.0})
    assert (p.extent, p.rotation_deg, p.boundary) == ((750, 400), 10.0, "ring")


def test_build_params_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown LSystemParams fields"):
        build_params(LSystemParams, {"bogus": 1})


def test_cli_generators_lists_everything(capsys):
    assert main(["generators"]) == 0
    out = capsys.readouterr().out
    assert "lsystem:" in out
    assert "organic_town" in out and "manhattan_like" in out
    assert "snap_radius = 15.0" in out


def test_cli_generate_lsystem_with_yaml_and_set(tmp_path):
    cfg = tmp_path / "ls.yaml"
    cfg.write_text("extent: [500, 160]\nstart: [0, 0]\n")
    out = tmp_path / "g.json"
    assert main(["generate", "lsystem", "--params", str(cfg), "--set", "block_size=[250, 80]",
                 "--set", "rotation_deg=15", "--seed", "3", "-o", str(out)]) == 0
    g = load_json(out)
    assert len(g.nodes) == 3 * 3
    assert g.metadata["params"]["rotation_deg"] == 15
    assert g.metadata["seed"] == 3


def test_cli_bad_set_value(tmp_path):
    with pytest.raises(SystemExit):
        main(["generate", "lsystem", "--set", "no_equals_sign", "-o", str(tmp_path / "x.json")])


@dataclass(frozen=True)
class CrossParams:
    arm: float = 150.0  # [m]


class CrossGenerator(RoadNetworkGenerator[CrossParams]):
    """A third-party style generator: no maps, no config section, one 4-way junction."""

    params_type = CrossParams
    description = "one crossing"

    def generate(self, params, seed):
        g = RoadGraph()
        g.add_node(Node(1, 0.0, 0.0))
        for k, (dx, dy) in enumerate(((1, 0), (0, 1), (-1, 0), (0, -1)), 2):
            g.add_node(Node(k, dx * params.arm, dy * params.arm))
            g.add_edge(Edge(k, 1, k, "avenue", 1, 1))
        assign_node_kinds(g)
        g.metadata.update(name="cross", generator="cross", seed=seed, params={"arm": params.arm})
        return g


def test_pipeline_runs_a_generator_other_than_lsystem(monkeypatch, tmp_path, capsys):
    """The registry stays open to new generators: one that reads no maps and has no
    defaults.yaml section runs through generate and pipeline like lsystem."""
    monkeypatch.setattr(base, "_REGISTRY", dict(base._REGISTRY))
    register("cross")(CrossGenerator)
    assert main(["generate", "cross", "--set", "arm=120", "-o", str(tmp_path / "c.json")]) == 0
    assert load_json(tmp_path / "c.json").nodes[2].x == 120.0
    prefix = tmp_path / "cross"
    assert main(["pipeline", "cross", "--allow-traps", "--no-hd", "-o", str(prefix)]) == 0
    assert prefix.with_suffix(".xodr").exists()
    # Maps are built around a network that doesn't read them.
    assert main(["pipeline", "cross", "--allow-traps", "--maps", "--flat", "-o", str(tmp_path / "m")]) == 0
    assert main(["generators"]) == 0
    assert "arm = 150.0" in capsys.readouterr().out  # the params class's default
    bad = tmp_path / "bad.yaml"
    bad.write_text("generators:\n  crosss: {arm: 1}\n")
    with pytest.raises(SystemExit, match="crosss"):
        main(["--config", str(bad), "generate", "cross", "-o", str(tmp_path / "x.json")])
