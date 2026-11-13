from __future__ import annotations

import dataclasses

import pytest
import yaml

from cli import build_parser, main
from roadgen.config import DEFAULTS, load_config, merge, section
from roadgen.generators.base import load_params
from roadgen.generators.lsystem import LSystemParams
from roadgen.geometry.cross_section import CrossSectionParams, TurnLaneParams
from roadgen.geometry.design import DesignParams
from roadgen.graph.io import load_json
from roadgen.maps.align import AlignParams
from roadgen.maps.annotate import AnnotateParams
from roadgen.maps.drape import DrapeParams
from roadgen.metrics.geometry import GeometryMetricsParams
from roadgen.metrics.network import NetworkMetricsParams
from roadgen.opendrive.control import JunctionControlParams
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.lights import TrafficLightParams
from roadgen.opendrive.marks import RoadMarkParams
from roadgen.scene.buildings import BuildingParams
from roadgen.scene.ground import GroundParams
from roadgen.scene.structures import StructureParams


@pytest.mark.parametrize("params_type, keys", [
    (LSystemParams, ("generators", "lsystem")),
    (BuildingParams, ("buildings",)),
    (DrapeParams, ("drape",)),
    (GroundParams, ("ground",)),
    (StructureParams, ("structures",)),
    (GeometryMetricsParams, ("metrics",)),
    (NetworkMetricsParams, ("network_metrics",)),
    (DesignParams, ("design",)),
    (AlignParams, ("align",)),
    (CrossSectionParams, ("cross_section",)),
    (HdMapParams, ("hdmap",)),
    (RoadMarkParams, ("road_marks",)),
    (JunctionControlParams, ("junction_control",)),
    (AnnotateParams, ("annotate",)),
    (TrafficLightParams, ("traffic_lights",)),
    (TurnLaneParams, ("turn_lanes",)),
])
def test_dataclass_defaults_come_from_defaults_yaml(params_type, keys):
    defaults = section(DEFAULTS, *keys)
    for f in dataclasses.fields(params_type):
        if f.name == "seed":
            assert f.default == DEFAULTS["seed"]
        elif f.metadata.get("config", True) is False:  # set from another section
            continue
        elif f.default is not dataclasses.MISSING:
            assert f.default == defaults[f.name], f.name
    fields = {f.name for f in dataclasses.fields(params_type)}
    assert set(defaults) <= fields


def test_merge_is_deep():
    merged = merge({"a": {"x": 1, "y": 2}, "b": 3}, {"a": {"y": 5}})
    assert merged == {"a": {"x": 1, "y": 5}, "b": 3}


def test_load_config_overrides_and_rejects_unknown_keys(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"generators": {"lsystem": {"lane_width": 3.0}}}))
    cfg = load_config(path)
    assert cfg["generators"]["lsystem"]["lane_width"] == 3.0
    assert cfg["generators"]["lsystem"]["block_size"] == [250.0, 80.0]

    path.write_text(yaml.safe_dump({"buildings": {"speed": 1}}))
    with pytest.raises(ValueError, match="buildings"):
        load_config(path)


def test_param_precedence_defaults_preset_overrides():
    defaults = {"lane_width": 3.0, "rotation_deg": 5.0}
    p = load_params(LSystemParams, preset="manhattan_like", defaults=defaults,
                    overrides={"extent": [500.0, 240.0]})
    assert p.lane_width == 3.0  # config default (preset doesn't set it)
    assert p.rotation_deg == 29.0  # preset beats config
    assert p.extent == (500.0, 240.0)  # override beats preset


def test_parser_defaults_follow_config():
    cfg = merge(load_config(), {"seed": 7, "esmini": {"view_density": 2.0}})
    assert build_parser(cfg).parse_args(["view", "x.xodr"]).density == 2.0
    assert build_parser(cfg).parse_args(["ground", "--map-file", "elevation=e.npz"]).seed == 7
    args = build_parser(cfg).parse_args(["view", "x.xodr", "--density", "3"])
    assert args.density == 3.0  # flag beats config


def test_cli_config_file(tmp_path):
    cfg = tmp_path / "defaults.yaml"
    cfg.write_text(yaml.safe_dump({"generators": {"lsystem": {"block_size": [100.0, 80.0]}}}))
    out = tmp_path / "g.json"
    argv = ["--config", str(cfg), "generate", "lsystem", "--set", "extent=[200, 160]",
            "--set", "start=[0, 0]"]
    assert main(argv + ["-o", str(out)]) == 0
    assert load_json(out).metadata["params"]["block_size"] == [100.0, 80.0]
    assert main(argv + ["--set", "block_size=[50, 80]", "-o", str(out)]) == 0
    assert load_json(out).metadata["params"]["block_size"] == [50.0, 80.0]


def test_cli_bad_config_file(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("bogus: 1\n")
    with pytest.raises(SystemExit, match="bogus"):
        main(["--config", str(cfg), "generators"])


def test_cli_precedence_preset_config_params_set(tmp_path):
    """defaults < preset < --config < --params < flags and --set."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("generators:\n  lsystem:\n    rotation_deg: 5.0\n    angle_jitter_deg: 1.0\n"
                   "    lane_width: 3.0\n")
    params = tmp_path / "p.yaml"
    params.write_text("angle_jitter_deg: 2.0\nlane_width: 3.25\n")
    out = tmp_path / "g.json"
    assert main(["--config", str(cfg), "generate", "lsystem", "--preset", "manhattan_like",
                 "--params", str(params), "--set", "lane_width=3.75", "--set", "extent=[500, 240]",
                 "-o", str(out)]) == 0
    p = load_json(out).metadata["params"]
    assert p["rotation_deg"] == 5.0  # --config beats the preset (29)
    assert p["angle_jitter_deg"] == 2.0  # --params beats --config
    assert p["lane_width"] == 3.75  # --set beats --params
    assert p["boundary"] == "ring"  # from the preset


def test_every_preset_in_presets_yaml_builds_its_params():
    """Polish N2: the presets live in roadgen/presets.yaml; each names fields of its
    params class (keyed as in defaults.yaml) and builds."""
    from roadgen.config import presets
    from roadgen.generators.junction import JunctionLayoutParams
    from roadgen.maps.elevation import FbmElevationParams
    from roadgen.maps.population import CentersPopulationParams
    from roadgen.maps.style import ZonesStyleParams
    from roadgen.maps.terrain import NoiseTerrainParams
    from roadgen.opendrive.control import JunctionControlParams

    classes = {("generators", "lsystem"): LSystemParams, ("generators", "junction"): JunctionLayoutParams,
               ("maps", "terrain", "noise"): NoiseTerrainParams, ("maps", "elevation", "fbm"): FbmElevationParams,
               ("maps", "population", "centers"): CentersPopulationParams,
               ("maps", "style", "zones"): ZonesStyleParams, ("junction_control",): JunctionControlParams}
    for keys, cls in classes.items():
        named = presets(*keys)
        assert named and named == cls.PRESETS, keys
        for name in named:
            cls.preset(name)
    assert presets("no", "such", "section") == {}
