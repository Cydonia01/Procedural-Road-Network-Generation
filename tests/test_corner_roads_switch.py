"""Stage B9: cross_section.corner_roads false leaves the junction corner roads out."""

from __future__ import annotations

import dataclasses
import json

from lxml import etree

from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.continuity import check_continuity
from roadgen.validate.hdmap import check_hdmap
from roadgen.validate.topology import check_topology

HD = HdMapParams(enabled=True)


def _build(tmp_path, hd: HdMapParams, name: str):
    path = tmp_path / f"{name}.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(load_json("tests/fixtures/4way_avenue_x_street.json"))
    b.write(path)
    return path, etree.parse(str(path)).getroot(), json.loads(path.with_suffix(".roadmap.json").read_text())


def _corners(root) -> list:
    return [r for r in root.findall("road") if "_corner_" in (r.get("name") or "")]


def test_corner_roads_can_be_left_out(tmp_path):
    _, with_root, with_side = _build(tmp_path, HD, "with")
    off = dataclasses.replace(HD, cross_section=dataclasses.replace(HD.cross_section, corner_roads=False))
    path, root, side = _build(tmp_path, off, "without")
    assert _corners(with_root) and with_side["corner_roads"]
    assert not _corners(root) and "corner_roads" not in side
    assert check_topology(path) == [] and check_continuity(path) == [] and check_hdmap(path) == []
    # everything else is built as before: the same movements and normal roads
    assert side["movements"] == with_side["movements"]
    normal = lambda r: sorted(x.get("id") for x in r.findall("road") if x.get("junction", "-1") == "-1")  # noqa: E731
    assert normal(root) == normal(with_root)
