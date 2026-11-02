"""Stage B8 (prompt 11): can a lane arrow be one closed outline instead of a marking per
stroke? How esmini renders and exports each encoding.

  python plans/scripts/b8_arrow_outline.py [--protobuf DIR]

Builds a cross of two 2+2 avenues (200 m arms) with HD maps on (out/b8_arrows/lines.xodr,
arrows as built: an open outline with a white marking per stroke) and derives variants
in which each arrow is one closed outline, the union of its strokes 0.15 m wide:
- outline: the closed outline alone;
- paint: the same with fillType="paint" (OpenDRIVE 1.7; 1.6 has no paint);
- edges: the same with a white marking along every outline edge.
Per variant it prints the file size and the arrows' bytes, renders a top view over a
straight-and-right arrow (out/b8_arrows/<variant>.png) and, for `lines` and `outline`,
runs esmini 3.9 with --osi_file and prints the road markings and stationary objects of
the first GroundTruth, by type and polygon vertex count. The OSI bindings need
`protobuf` (see b4_osi_curb.py). Run in the dev container.
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
from shapely.geometry import LineString
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.sim.esmini import esmini_binary
from roadgen.sim.screenshot import screenshot, still_xosc
from roadgen.validate.continuity import RoadEval

OUT = Path("out/b8_arrows")
LINE = 0.15  # stroke width [m]


def cross() -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-200.0, 0.0), 3: (200.0, 0.0), 4: (0.0, 200.0), 5: (0.0, -200.0)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, "avenue", 2, 2, speed_limit=13.9))
    return g


def arrows(root: etree._Element) -> list[etree._Element]:
    return [o for o in root.iter("object") if o.get("name", "").startswith("arrow")]


def to_outline(obj: etree._Element, fill: str = "", edges: bool = False) -> None:
    """Replace an arrow's strokes by one closed outline: the union of the strokes."""
    corners = obj.findall("outlines/outline/cornerLocal")
    pts = [(float(c.get("u")), float(c.get("v"))) for c in corners]
    shape = unary_union([LineString([a, b]).buffer(LINE / 2, cap_style=2, join_style=2)
                         for a, b in zip(pts[::2], pts[1::2])]).simplify(0.005)
    ring = list(orient(shape, 1.0).exterior.coords)[:-1]
    z = corners[0].get("z")
    for tag in ("outlines", "markings"):
        old = obj.find(tag)
        if old is not None:
            obj.remove(old)
    outline = etree.Element("outline", {"id": "0", "outer": "true", "closed": "true"})
    if fill:
        outline.set("fillType", fill)
    for j, (u, v) in enumerate(ring):
        etree.SubElement(outline, "cornerLocal", {"u": f"{u:.3f}", "v": f"{v:.3f}", "z": z, "height": "0.0",
                                                  "id": str(j)})
    obj.insert(0, etree.Element("outlines"))
    obj[0].append(outline)
    if edges:
        marks = etree.SubElement(obj, "markings")
        for j in range(len(ring)):
            m = etree.SubElement(marks, "marking", {"side": "front", "weight": "standard", "width": str(LINE),
                                                    "color": "white", "zOffset": "0.01", "spaceLength": "0",
                                                    "lineLength": "10", "startOffset": "0", "stopOffset": "0"})
            etree.SubElement(m, "cornerReference", {"id": str(j)})
            etree.SubElement(m, "cornerReference", {"id": str((j + 1) % len(ring))})


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


def osi_summary(gt) -> dict:
    from osi3.osi_object_pb2 import StationaryObject
    from osi3.osi_roadmarking_pb2 import RoadMarking

    rtype = RoadMarking.Classification.Type
    otype = StationaryObject.Classification.Type
    return {
        "road_markings": dict(Counter(f"{rtype.Name(m.classification.type)}, {len(m.base.base_polygon)} vertices"
                                      for m in gt.road_marking)),
        "stationary_objects": dict(Counter(f"{otype.Name(o.classification.type)}, {len(o.base.base_polygon)} vertices"
                                           for o in gt.stationary_object)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protobuf", help="directory with the protobuf package")
    args = ap.parse_args()
    if args.protobuf:
        sys.path.insert(0, args.protobuf)
    sys.path.insert(0, "/opt/esmini/scripts")
    OUT.mkdir(parents=True, exist_ok=True)
    lines = OUT / "lines.xodr"
    b = OpenDriveBuilder(hd=HdMapParams(enabled=True))
    b.build(cross())
    b.write(lines)
    variants = {"lines": {}, "outline": {}, "paint": {"fill": "paint"}, "edges": {"edges": True}}
    for name, opts in variants.items():
        tree = etree.parse(str(lines))
        root = tree.getroot()
        camera = None
        for o in arrows(root):
            if name != "lines":
                to_outline(o, **opts)
            road = o.getparent().getparent()
            ev = RoadEval(road)
            s = float(o.get("s"))
            if camera is None and o.get("name") == "arrow_straight_right" and min(s, ev.length - s) < 30.0:
                x, y, h = ev.reference(s)
                t = float(o.get("t"))
                camera = (x - math.sin(h) * t, y + math.cos(h) * t, 9.0, h, math.pi / 2)
        xodr = OUT / f"{name}.xodr"
        tree.write(str(xodr), xml_declaration=True, encoding="utf-8")
        size = sum(len(etree.tostring(o)) for o in arrows(root))
        print(f"{name}: {xodr.stat().st_size} bytes, {len(arrows(root))} arrows, {size} bytes of arrows "
              f"({size / max(len(arrows(root)), 1):.0f} per arrow); {screenshot(OUT / f'{name}.png', xodr=xodr, camera=camera)}")
        if name in ("lines", "outline"):
            print(json.dumps(osi_summary(ground_truth(xodr)), indent=2))


if __name__ == "__main__":
    main()
