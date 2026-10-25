from __future__ import annotations

import dataclasses

import pytest
import yaml

from cli import build_parser, main
from roadgen.bench.benchmark import BenchConfig
from roadgen.config import DEFAULTS, load_config, merge, section
from roadgen.generators.base import load_params
from roadgen.generators.lsystem import LSystemParams
from roadgen.generators.manhattan import ManhattanParams
from roadgen.geometry.design import DesignParams
from roadgen.graph.io import load_json
from roadgen.maps.align import AlignParams
from roadgen.maps.drape import DrapeParams
from roadgen.metrics.geometry import GeometryMetricsParams
from roadgen.scenario.xosc_builder import ScenarioParams
from roadgen.scene.buildings import BuildingParams
from roadgen.scene.ground import GroundParams


@pytest.mark.parametrize("params_type, keys", [
    (ManhattanParams, ("generators", "manhattan")),
    (LSystemParams, ("generators", "lsystem")),
    (BuildingParams, ("buildings",)),
    (ScenarioParams, ("scenario",)),
    (DrapeParams, ("drape",)),
    (GroundParams, ("ground",)),
    (GeometryMetricsParams, ("metrics",)),
    (DesignParams, ("design",)),
    (AlignParams, ("align",)),
])
def test_dataclass_defaults_come_from_defaults_yaml(params_type, keys):
    defaults = section(DEFAULTS, *keys)
    for f in dataclasses.fields(params_type):
        if f.name == "seed":
            assert f.default == DEFAULTS["seed"]
        elif f.default is not dataclasses.MISSING:
            assert f.default == defaults[f.name], f.name
    fields = {f.name for f in dataclasses.fields(params_type)}
    assert set(defaults) <= fields


def test_merge_is_deep():
    merged = merge({"a": {"x": 1, "y": 2}, "b": 3}, {"a": {"y": 5}})
    assert merged == {"a": {"x": 1, "y": 5}, "b": 3}


def test_load_config_overrides_and_rejects_unknown_keys(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"generators": {"manhattan": {"lane_width": 3.0}}}))
    cfg = load_config(path)
    assert cfg["generators"]["manhattan"]["lane_width"] == 3.0
    assert cfg["generators"]["manhattan"]["avenue_spacing"] == 250.0

    path.write_text(yaml.safe_dump({"scenario": {"speed": 1}}))
    with pytest.raises(ValueError, match="scenario"):
        load_config(path)


def test_param_precedence_defaults_preset_overrides():
    defaults = {"lane_width": 3.0, "rotation_deg": 5.0}
    p = load_params(ManhattanParams, preset="manhattan_like", defaults=defaults,
                    overrides={"n_avenues": 3})
    assert p.lane_width == 3.0  # config default (preset doesn't set it)
    assert p.rotation_deg == 29.0  # preset beats config
    assert p.n_avenues == 3  # override beats preset


def test_parser_defaults_follow_config():
    cfg = merge(load_config(), {"seed": 7, "scenario": {"sim_duration": 12.0}})
    args = build_parser(cfg).parse_args(["scenario", "x.xodr", "-o", "x.xosc", "--vehicles", "1"])
    assert (args.seed, args.duration) == (7, 12.0)
    args = build_parser(cfg).parse_args(
        ["scenario", "x.xodr", "-o", "x.xosc", "--vehicles", "1", "--duration", "3"]
    )
    assert args.duration == 3.0  # flag beats config


def test_cli_config_file(tmp_path):
    cfg = tmp_path / "defaults.yaml"
    cfg.write_text(yaml.safe_dump({"generators": {"manhattan": {"avenue_spacing": 100.0}}}))
    out = tmp_path / "g.json"
    argv = ["--config", str(cfg), "generate", "manhattan", "--avenues", "2", "--streets", "2"]
    assert main(argv + ["-o", str(out)]) == 0
    assert load_json(out).metadata["params"]["avenue_spacing"] == 100.0
    assert main(argv + ["--avenue-spacing", "120", "-o", str(out)]) == 0
    assert load_json(out).metadata["params"]["avenue_spacing"] == 120.0


def test_cli_bad_config_file(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("bogus: 1\n")
    with pytest.raises(SystemExit, match="bogus"):
        main(["--config", str(cfg), "generators"])


def test_bench_config_defaults(tmp_path):
    path = tmp_path / "sweep.yaml"
    path.write_text(yaml.safe_dump({"grid_sizes": [[2, 2]], "vehicle_counts": [1], "seeds": [1]}))
    assert BenchConfig.from_yaml(path).timeout_s == DEFAULTS["bench"]["timeout_s"]
    cfg = BenchConfig.from_yaml(path, {**DEFAULTS["bench"], "timeout_s": 5.0})
    assert cfg.timeout_s == 5.0


def test_cli_precedence_preset_config_params_set(tmp_path):
    """defaults < preset < --config < --params < flags and --set."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("generators:\n  manhattan:\n    rotation_deg: 5.0\n    jitter: 1.0\n    lane_width: 3.0\n")
    params = tmp_path / "p.yaml"
    params.write_text("jitter: 2.0\nlane_width: 3.25\n")
    out = tmp_path / "g.json"
    assert main(["--config", str(cfg), "generate", "manhattan", "--preset", "manhattan_like",
                 "--params", str(params), "--set", "lane_width=3.75", "-o", str(out)]) == 0
    p = load_json(out).metadata["params"]
    assert p["rotation_deg"] == 5.0  # --config beats the preset (29)
    assert p["jitter"] == 2.0  # --params beats --config
    assert p["lane_width"] == 3.75  # --set beats --params
    assert p["n_avenues"] > 0  # from the preset
