"""Stage B9 (prompt 12): how densely esmini samples the corner roads, from its OSI output.

  python plans/scripts/b9_osi_points.py GRAPH.json SEED [--protobuf DIR]
  e.g. python plans/scripts/b9_osi_points.py out/stage_b/b8r/city.json 7 --protobuf /tmp/pb

esmini 3.9 computes OSI points for every lane while it loads (SetLaneOSIPoints); the
step shrinks where the road's pitch or roll changes or curvature meets banking
(GetMaxSegmentLen), down to 0.2 m. Builds the graph with HD maps twice, with and
without corner roads (cross_section.corner_roads), into out/b9/osi_points/, times
RM_Init on each (best of 3), runs esmini with --osi_file and counts the OSI lanes,
their centre-line points and the lane-boundary points of the first GroundTruth. The
difference is the corner roads': points per metre of corner-road lane against the
rest of the network. The OSI bindings need `protobuf` (see b4_osi_curb.py). Run in
the dev container.
"""
from __future__ import annotations

import argparse
import dataclasses
import struct
import subprocess
import sys
import time
from pathlib import Path

from lxml import etree

from roadgen.config import DEFAULTS
from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.sim import drive_check
from roadgen.sim.esmini import esmini_binary
from roadgen.sim.screenshot import still_xosc

OUT = Path("out/b9/osi_points")
TIMER = """
import sys, time
from roadgen.sim import drive_check
lib = drive_check._load_rm()
t = time.perf_counter()
assert lib.RM_Init(sys.argv[1].encode()) == 0
print(time.perf_counter() - t)
"""


def lane_metres(path: Path, corner: bool) -> float:
    """Lane length [m] (lanes x road length) of the corner roads or of all other roads."""
    total = 0.0
    for r in etree.parse(str(path)).getroot().findall("road"):
        if ("_corner_" in (r.get("name") or "")) != corner:
            continue
        lanes = max((len(s.findall("*/lane")) for s in r.findall("lanes/laneSection")), default=0)
        total += lanes * float(r.get("length"))
    return total


def osi_counts(path: Path) -> dict:
    from osi3.osi_groundtruth_pb2 import GroundTruth

    xosc = still_xosc(path, path.with_suffix(".xosc"), 0.2)
    osi = path.with_suffix(".osi")
    subprocess.run([str(esmini_binary("esmini")), "--osc", str(xosc.resolve()), "--headless",
                    "--fixed_timestep", "0.1", "--osi_file", str(osi.resolve())], capture_output=True, check=True)
    data = osi.read_bytes()
    size = struct.unpack("I", data[:4])[0]
    gt = GroundTruth()
    gt.ParseFromString(data[4:4 + size])
    return {"lanes": len(gt.lane),
            "centre_points": sum(len(lane.classification.centerline) for lane in gt.lane),
            "boundary_points": sum(len(b.boundary_line) for b in gt.lane_boundary)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("graph")
    ap.add_argument("seed", type=int)
    ap.add_argument("--protobuf")
    args = ap.parse_args()
    if args.protobuf:
        sys.path.insert(0, args.protobuf)
    sys.path.insert(0, "/opt/esmini/scripts")
    OUT.mkdir(parents=True, exist_ok=True)
    graph = load_json(args.graph)
    hd = HdMapParams.from_config(DEFAULTS, args.seed, True)
    rows = {}
    for name, corners in (("with", True), ("without", False)):
        h = dataclasses.replace(hd, cross_section=dataclasses.replace(hd.cross_section, corner_roads=corners))
        b = OpenDriveBuilder(junction_margin=DEFAULTS["opendrive"]["junction_margin"], hd=h)
        b.build(load_json(args.graph) if not corners else graph)
        path = OUT / f"{Path(args.graph).stem}_{name}_corners.xodr"
        b.write(path)
        loads = sorted(float(subprocess.run([sys.executable, "-c", TIMER, str(path)], capture_output=True,
                                            text=True, check=True).stdout.split()[-1]) for _ in range(3))
        rows[name] = {"load": loads, **osi_counts(path), "corner_lane_m": lane_metres(path, True),
                      "other_lane_m": lane_metres(path, False)}
        r = rows[name]
        print(f"{name} corner roads: RM_Init best {loads[0]:.1f} s (median {loads[1]:.1f}, worst {loads[2]:.1f}); "
              f"{r['lanes']} OSI lanes, {r['centre_points']} centre-line and {r['boundary_points']} boundary points; "
              f"lane metres: corner {r['corner_lane_m']:.0f}, other {r['other_lane_m']:.0f}", flush=True)
    w, o = rows["with"], rows["without"]
    extra = w["centre_points"] - o["centre_points"]
    print(f"corner roads: {extra} more centre-line points over {w['corner_lane_m']:.0f} lane metres = "
          f"{extra / max(w['corner_lane_m'], 1):.2f} per m; elsewhere {o['centre_points'] / o['other_lane_m']:.2f} per m; "
          f"load {w['load'][0] - o['load'][0]:.1f} s more")


if __name__ == "__main__":
    main()
