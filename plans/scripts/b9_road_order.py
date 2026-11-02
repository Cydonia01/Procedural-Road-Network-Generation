"""Stage B9 (prompt 12): does the order of <road> elements change esmini's load time?

  python plans/scripts/b9_road_order.py FILE.xodr [RUNS] [VARIANT ...]

esmini 3.9's RoadManager finds roads (and junctions) by id with a linear scan from
the start of its list (OpenDrive::GetRoadById), and while loading it evaluates
positions on every lane in 1 m steps (SetLaneOSIPoints) by road id. If those scans
weigh, the load depends on where the roads with most OSI points stand in the file.
Writes variants of FILE to out/b9/road_order/ and times RM_Init on each, RUNS
times (default 3), each in a fresh process:
- as_built;
- corner_first: the corner roads (name *_corner_*) before every other road;
- corner_last: after every other road;
- reversed: the whole road order reversed;
- by_work: roads by lanes x length, largest first.
Prints best, median and worst per variant, and every run's wall and CPU time.
VARIANT names limit the run to those variants. Run in the dev container.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from lxml import etree

OUT = Path("out/b9/road_order")
TIMER = """
import sys, time
from roadgen.sim import drive_check
lib = drive_check._load_rm()
t = time.perf_counter(); cpu = time.process_time()
assert lib.RM_Init(sys.argv[1].encode()) == 0
print(time.perf_counter() - t, time.process_time() - cpu)
"""


def work(road: etree._Element) -> float:
    lanes = max((len(s.findall("*/lane")) for s in road.findall("lanes/laneSection")), default=0)
    return lanes * float(road.get("length"))


def variant(src: Path, name: str, key) -> Path:
    tree = etree.parse(str(src))
    root = tree.getroot()
    roads = root.findall("road")
    first = roads[0].getparent().index(roads[0])
    for r in roads:
        root.remove(r)
    for k, r in enumerate(key(roads)):
        root.insert(first + k, r)
    out = OUT / f"{src.stem}_{name}.xodr"
    tree.write(str(out), xml_declaration=True, encoding="utf-8")
    return out


def corner(r: etree._Element) -> bool:
    return "_corner_" in (r.get("name") or "")


def main() -> None:
    src = Path(sys.argv[1])
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    only = set(sys.argv[3:])
    OUT.mkdir(parents=True, exist_ok=True)
    variants = {
        "as_built": lambda rs: rs,
        "corner_first": lambda rs: [r for r in rs if corner(r)] + [r for r in rs if not corner(r)],
        "corner_last": lambda rs: [r for r in rs if not corner(r)] + [r for r in rs if corner(r)],
        "reversed": lambda rs: rs[::-1],
        "by_work": lambda rs: sorted(rs, key=work, reverse=True),
    }
    for name, key in variants.items():
        if only and name not in only:
            continue
        path = variant(src, name, key)
        times = []
        for _ in range(runs):
            out = subprocess.run([sys.executable, "-c", TIMER, str(path)], capture_output=True, text=True, check=True)
            times.append(tuple(map(float, out.stdout.split()[-2:])))
        runs_text = ", ".join(f"{w:.1f} / {c:.1f}" for w, c in times)
        times.sort()
        wall = [w for w, _ in times]
        print(f"{name:13s} RM_Init best {wall[0]:.1f} s, median {wall[len(wall) // 2]:.1f}, worst {wall[-1]:.1f} "
              f"(CPU of best {times[0][1]:.1f} s); runs, wall / CPU [s]: {runs_text}", flush=True)


if __name__ == "__main__":
    main()
