"""Checks on the HD-map content of an .xodr (stage B), run when HD maps are on.

Road marks (B4):
- no <roadMark> on connecting roads: junction interiors are unmarked;
- within a lane, the marks' sOffsets start inside the lane section and
  ascend, so every stretch of the lane has exactly one mark.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Union

from lxml import etree

PathLike = Union[str, Path]
EPS = 1e-6  # [m]


def check_hdmap(xodr_path: PathLike) -> list[str]:
    return check_hdmap_tree(etree.parse(str(xodr_path)))


def check_hdmap_tree(tree: etree._ElementTree) -> list[str]:
    """Return a list of errors; empty means the HD-map content is consistent."""
    errors: list[str] = []
    for road in tree.getroot().findall("road"):
        rid = road.get("id")
        if road.get("junction", "-1") != "-1":
            n = len(road.findall(".//roadMark"))
            if n:
                errors.append(f"road {rid}: connecting road has {n} road marks (junctions are unmarked)")
            continue
        errors += _check_mark_offsets(road)
    return errors


def _check_mark_offsets(road: etree._Element) -> list[str]:
    sections = road.findall("lanes/laneSection")
    bounds = [float(sec.get("s")) for sec in sections] + [float(road.get("length"))]
    errors = []
    for k, sec in enumerate(sections):
        length = bounds[k + 1] - bounds[k]
        for lane in sec.iter("lane"):
            offsets = [float(m.get("sOffset", "0")) for m in lane.findall("roadMark")]
            where = f"road {road.get('id')} section {k} lane {lane.get('id')}"
            if any(s < -EPS or s > length + EPS for s in offsets):
                errors.append(f"{where}: road mark sOffset outside the lane section (length {length:.3f} m)")
            elif any(b <= a + EPS for a, b in pairwise(offsets)):
                errors.append(f"{where}: road mark sOffsets not ascending: {offsets}")
    return errors
