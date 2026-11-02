"""Stage B9 (prompt 12): the lane arrows as polylines over shared corners, in esmini.

  python plans/scripts/b9_arrow_check.py [--protobuf DIR]

Builds the cross of two 2+2 avenues of b8_arrow_outline.py with HD maps on
(out/b9/arrows/cross.xodr) and prints the arrows' count, corners, markings and
bytes; renders a top view over a straight-and-right and a left arrow
(out/b9/arrows/<kind>.png); runs esmini 3.9 with --osi_file and prints the OSI road
markings by type and vertex count, and the stationary objects. Compare with
b8_arrow_outline.py's `lines` (one two-point marking per stroke: 72 road markings,
2 vertices each, 2,721 bytes per arrow). The OSI bindings need `protobuf` (see
b4_osi_curb.py). Run in the dev container.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.sim.esmini import esmini_binary
from roadgen.sim.screenshot import screenshot, still_xosc
from roadgen.validate.continuity import RoadEval

OUT = Path("out/b9/arrows")


def cross() -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-200.0, 0.0), 3: (200.0, 0.0), 4: (0.0, 200.0), 5: (0.0, -200.0)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, "avenue", 2, 2, speed_limit=13.9))
    return g


def ground_truth(xodr: Path):
    from osi3.osi_groundtruth_pb2 import GroundTruth

    xosc = still_xosc(xodr, xodr.with_suffix(".xosc"), 0.2)
    osi = xodr.with_suffix(".osi")
    subprocess.run([str(esmini_binary("esmini")), "--osc", str(xosc.resolve()), "--headless",
                    "--fixed_timestep", "0.1", "--osi_file", str(osi.resolve())],
                   capture_output=True, check=True)
    data = osi.read_bytes()
    size = struct.unpack("I", data[:4])[0]
    gt = GroundTruth()
    gt.ParseFromString(data[4:4 + size])
    return gt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protobuf", help="directory with the protobuf package")
    args = ap.parse_args()
    if args.protobuf:
        sys.path.insert(0, args.protobuf)
    sys.path.insert(0, "/opt/esmini/scripts")
    OUT.mkdir(parents=True, exist_ok=True)
    xodr = OUT / "cross.xodr"
    b = OpenDriveBuilder(hd=HdMapParams(enabled=True))
    b.build(cross())
    b.write(xodr)
    root = etree.parse(str(xodr)).getroot()
    arrows = [o for o in root.iter("object") if o.get("name", "").startswith("arrow")]
    size = sum(len(etree.tostring(o)) for o in arrows)
    corners = sum(len(o.findall("outlines/outline/cornerLocal")) for o in arrows)
    markings = sum(len(o.findall("markings/marking")) for o in arrows)
    print(f"{xodr}: {xodr.stat().st_size} bytes, {len(arrows)} arrows, {corners} corners, {markings} markings, "
          f"{size} bytes of arrows ({size / max(len(arrows), 1):.0f} per arrow)")
    shot: dict[str, Path] = {}
    for o in arrows:
        kind = o.get("name")
        road = o.getparent().getparent()
        ev = RoadEval(road)
        s = float(o.get("s"))
        if kind in shot or kind not in ("arrow_straight_right", "arrow_left") or min(s, ev.length - s) > 30.0:
            continue
        x, y, h = ev.reference(s)
        t = float(o.get("t"))
        shot[kind] = screenshot(OUT / f"{kind}.png", xodr=xodr,
                                camera=(x - math.sin(h) * t, y + math.cos(h) * t, 9.0, h, math.pi / 2))
        print(f"{kind}: {shot[kind]}")
    from osi3.osi_object_pb2 import StationaryObject
    from osi3.osi_roadmarking_pb2 import RoadMarking

    gt = ground_truth(xodr)
    rtype, otype = RoadMarking.Classification.Type, StationaryObject.Classification.Type
    print(json.dumps({
        "road_markings": dict(Counter(f"{rtype.Name(m.classification.type)}, {len(m.base.base_polygon)} vertices"
                                      for m in gt.road_marking)),
        "stationary_objects": dict(Counter(f"{otype.Name(o.classification.type)}, {len(o.base.base_polygon)} vertices"
                                           for o in gt.stationary_object)),
    }, indent=2))


if __name__ == "__main__":
    main()
