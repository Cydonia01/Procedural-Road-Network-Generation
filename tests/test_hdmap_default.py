"""HD maps are on by default (B10): the builder, the pipeline and the sidecar's hdmap_version."""

from __future__ import annotations

import json

from cli import main
from roadgen.config import DEFAULTS
from roadgen.graph.io import load_json
from roadgen.opendrive import HDMAP_VERSION
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams

FIXTURE = "tests/fixtures/4way_avenue_x_street.json"


def _sidecar(tmp_path, hd: HdMapParams) -> dict:
    b = OpenDriveBuilder(hd=hd)
    b.build(load_json(FIXTURE))
    return json.loads(b.write(tmp_path / "net.xodr").read_text())


def test_hd_maps_are_the_default():
    assert DEFAULTS["hdmap"]["enabled"] is True
    assert HdMapParams().enabled and HdMapParams.from_config(DEFAULTS).enabled


def test_sidecar_has_hdmap_version_only_with_hd_maps(tmp_path):
    assert _sidecar(tmp_path, HdMapParams())["hdmap_version"] == HDMAP_VERSION == 1
    assert "hdmap_version" not in _sidecar(tmp_path, HdMapParams(enabled=False))


def test_pipeline_default_is_hd_and_no_hd_is_plain(tmp_path):
    on, off = tmp_path / "on", tmp_path / "off"
    base = ["--no-history", "pipeline", "lsystem", "--preset", "grid", "--allow-traps"]
    assert main([*base, "-o", str(on)]) == 0
    assert main([*base, "--no-hd", "-o", str(off)]) == 0
    assert json.loads(on.with_suffix(".roadmap.json").read_text())["hdmap_version"] == HDMAP_VERSION
    assert "hdmap_version" not in json.loads(off.with_suffix(".roadmap.json").read_text())
    assert 'type="sidewalk"' in on.with_suffix(".xodr").read_text()
    assert 'type="sidewalk"' not in off.with_suffix(".xodr").read_text()
    assert (tmp_path / "on_hd.png").is_file() and not (tmp_path / "off_hd.png").exists()
