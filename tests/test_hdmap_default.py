"""HD maps are on by default (B10): the builder, the pipeline and the sidecar's hdmap_version."""

from __future__ import annotations

import json

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


