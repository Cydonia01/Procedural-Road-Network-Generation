"""Stage B4: what esmini's OSI ground truth makes of the `curb` road mark.

  python plans/scripts/b4_osi_curb.py [--protobuf DIR]

Builds a straight 200 m urban street (1+1 lanes, parking, curb, sidewalk) with
HD maps on, writes it twice to out/b4_osi/ (as built, with curb marks; and
with every curb mark removed), runs esmini 3.9 on a scenario without
vehicles with --osi_file, and decodes the first GroundTruth message with the
OSI bindings esmini ships (/opt/esmini/scripts/osi3). Prints, per variant,
the OSI lane boundary types and the OSI lane subtypes.

The bindings need the `protobuf` package, which the dev container lacks:
install it into a scratch directory and pass it, e.g.
  pip install --target /tmp/pb 'protobuf>=4.21,<5' && python plans/scripts/b4_osi_curb.py --protobuf /tmp/pb
Run in the dev container.
"""
from __future__ import annotations

import argparse
import json
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
from roadgen.sim.screenshot import still_xosc

OUT = Path("out/b4_osi")


def street() -> RoadGraph:
    g = RoadGraph()
    g.add_node(Node(1, 0.0, 0.0))
    g.add_node(Node(2, 200.0, 0.0))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1))
    return g


def variants() -> dict[str, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    marked = OUT / "curb_marks.xodr"
    b = OpenDriveBuilder(hd=HdMapParams(enabled=True))
    b.build(street())
    b.write(marked)
    tree = etree.parse(str(marked))
    removed = 0
    for mark in tree.getroot().iter("roadMark"):
        if mark.get("type") == "curb":
            mark.getparent().remove(mark)
            removed += 1
    plain = OUT / "no_curb_marks.xodr"
    tree.write(str(plain), xml_declaration=True, encoding="utf-8")
    print(f"removed {removed} curb marks for {plain.name}")
    return {"curb marks": marked, "no curb marks": plain}


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


def summary(gt) -> dict:
    from osi3.osi_lane_pb2 import Lane, LaneBoundary

    btype = LaneBoundary.Classification.Type
    bcolor = LaneBoundary.Classification.Color
    subtype = Lane.Classification.Subtype
    return {
        "lane_boundaries": dict(Counter(f"{btype.Name(b.classification.type)}/{bcolor.Name(b.classification.color)}"
                                        for b in gt.lane_boundary)),
        "lane_subtypes": dict(Counter(subtype.Name(lane.classification.subtype) for lane in gt.lane)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protobuf", help="directory with the protobuf package")
    args = ap.parse_args()
    if args.protobuf:
        sys.path.insert(0, args.protobuf)
    sys.path.insert(0, "/opt/esmini/scripts")
    out = {name: summary(ground_truth(path)) for name, path in variants().items()}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
