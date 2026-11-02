"""Stage B9 (prompt 13): small .xodr files that reproduce the two esmini load findings.

  python plans/scripts/b9_esmini_repro.py GRAPH.json SEED OUTDIR [RUNS]
  e.g. python plans/scripts/b9_esmini_repro.py out/stage_b/b9/grid.json 0 out/b9/repro 5

RUNS 0 only writes the files (esmini_repro_city.sh uses that).

Builds the graph with HD maps three ways into OUTDIR:
- <name>_with_corners.xodr: as built (corner roads around the junction corners);
- <name>_without_corners.xodr: cross_section.corner_roads false, otherwise the same;
- <name>_reversed.xodr: as built, its <road> elements in reverse order;
and times esmini 3.9's RoadManager load (RM_Init) RUNS times each (default 5), each
in a fresh process: wall and CPU time per run, best, median and worst. Prints lane
metres of corner roads and of the rest for the cost per metre. Run in the dev
container.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

from lxml import etree

from roadgen.config import DEFAULTS
from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams

TIMER = """
import sys, time
from roadgen.sim import drive_check
lib = drive_check._load_rm()
t = time.perf_counter(); cpu = time.process_time()
assert lib.RM_Init(sys.argv[1].encode()) == 0
print(time.perf_counter() - t, time.process_time() - cpu)
"""


def lane_metres(path: Path) -> tuple[float, float]:
    corner = other = 0.0
    for r in etree.parse(str(path)).getroot().findall("road"):
        lanes = max((len(s.findall("*/lane")) for s in r.findall("lanes/laneSection")), default=0)
        if "_corner_" in (r.get("name") or ""):
            corner += lanes * float(r.get("length"))
        else:
            other += lanes * float(r.get("length"))
    return corner, other


def build(graph_path: str, seed: int, path: Path, corners: bool) -> Path:
    hd = HdMapParams.from_config(DEFAULTS, seed, True)
    hd = dataclasses.replace(hd, cross_section=dataclasses.replace(hd.cross_section, corner_roads=corners))
    b = OpenDriveBuilder(junction_margin=DEFAULTS["opendrive"]["junction_margin"], hd=hd)
    b.build(load_json(graph_path))
    b.write(path)
    path.with_suffix(".roadmap.json").unlink()
    return path


def reverse(src: Path, out: Path) -> Path:
    tree = etree.parse(str(src))
    root = tree.getroot()
    roads = root.findall("road")
    first = root.index(roads[0])
    for r in roads:
        root.remove(r)
    for k, r in enumerate(roads[::-1]):
        root.insert(first + k, r)
    tree.write(str(out), xml_declaration=True, encoding="utf-8")
    return out


def main() -> None:
    graph_path, seed, outdir = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    runs = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    outdir.mkdir(parents=True, exist_ok=True)
    name = Path(graph_path).stem
    files = [build(graph_path, seed, outdir / f"{name}_with_corners.xodr", True),
             build(graph_path, seed, outdir / f"{name}_without_corners.xodr", False)]
    files.append(reverse(files[0], outdir / f"{name}_reversed.xodr"))
    if runs == 0:
        return
    for path in files:
        times = []
        for _ in range(runs):
            out = subprocess.run([sys.executable, "-c", TIMER, str(path)], capture_output=True, text=True, check=True)
            times.append(tuple(map(float, out.stdout.split()[-2:])))
        wall = sorted(w for w, _ in times)
        corner, other = lane_metres(path)
        print(f"{path.name}: {path.stat().st_size / 1e6:.2f} MB, lane metres corner {corner:.0f} / other {other:.0f}; "
              f"RM_Init best {wall[0]:.2f} s, median {wall[len(wall) // 2]:.2f}, worst {wall[-1]:.2f}; "
              f"runs (wall / CPU): {', '.join(f'{w:.2f} / {c:.2f}' for w, c in times)}", flush=True)


if __name__ == "__main__":
    main()
