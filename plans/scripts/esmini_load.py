"""Stage B (after B3): how long esmini takes to load .xodr files, and what in the corner
roads costs it.

  python plans/scripts/esmini_load.py FILE.xodr ...           # esmini --headless, a scenario
                                                               # without vehicles, best of 3
  python plans/scripts/esmini_load.py --rm FILE.xodr ...      # esmini's RoadManager only (RM_Init)
  python plans/scripts/esmini_load.py --variants FILE.xodr    # write variants of FILE without or
                                                               # with simplified corner roads to
                                                               # out/esmini_load/ and time RM_Init

Variants: no corner roads; no curb heights; constant widths and no lane offset;
no lane links; no junction connections; one lane each; straight lines.
Run in the dev container.
"""
import copy
import subprocess
import sys
import time
from pathlib import Path

from lxml import etree

from roadgen.sim import drive_check
from roadgen.sim.esmini import esmini_binary
from roadgen.sim.screenshot import still_xosc

OUT = Path("out/esmini_load")


def load_time(path: Path, runs: int = 3) -> float:
    xosc = still_xosc(path, OUT / f"{path.parent.name}_{path.stem}_still.xosc", 0.1)
    times = []
    for _ in range(runs):
        t0 = time.time()
        subprocess.run([str(esmini_binary("esmini")), "--osc", str(xosc.resolve()), "--headless",
                        "--fixed_timestep", "0.1"], capture_output=True, check=False)
        times.append(time.time() - t0)
    return min(times)


def rm_init_time(path: Path) -> float:
    lib = drive_check._load_rm()
    t0 = time.time()
    lib.RM_Init(str(path).encode())
    t = time.time() - t0
    lib.RM_Close()
    return t


def variants(src: Path) -> list[Path]:
    root0 = etree.parse(str(src)).getroot()
    corners = {r.get("id") for r in root0.findall("road") if "_corner_" in (r.get("name") or "")}
    print(f"{src}: {len(corners)} corner roads")

    def corner_roads(root):
        return [r for r in root.findall("road") if r.get("id") in corners]

    def corner_connections(root):
        return [(j, c) for j in root.findall("junction") for c in j.findall("connection")
                if c.get("connectingRoad") in corners]

    out = []

    def save(root, name):
        path = OUT / f"{src.stem}_{name}.xodr"
        etree.ElementTree(root).write(str(path), xml_declaration=True, encoding="utf-8")
        out.append(path)

    r = copy.deepcopy(root0)
    for road in corner_roads(r):
        r.remove(road)
    for j, c in corner_connections(r):
        j.remove(c)
    save(r, "no_corners")

    r = copy.deepcopy(root0)
    for road in corner_roads(r):
        for h in list(road.iter("height")):
            h.getparent().remove(h)
    save(r, "no_heights")

    r = copy.deepcopy(root0)
    for road in corner_roads(r):
        for w in road.iter("width"):
            for k in "bcd":
                w.set(k, "0")
        for lo in road.findall("lanes/laneOffset"):
            lo.getparent().remove(lo)
    save(r, "constant_widths")

    r = copy.deepcopy(root0)
    for road in corner_roads(r):
        for lane in road.iter("lane"):
            link = lane.find("link")
            if link is not None:
                lane.remove(link)
    for _, c in corner_connections(r):
        for ll in c.findall("laneLink"):
            c.remove(ll)
    save(r, "no_lane_links")

    r = copy.deepcopy(root0)
    for j, c in corner_connections(r):
        j.remove(c)
    save(r, "no_connections")

    r = copy.deepcopy(root0)
    for road in corner_roads(r):
        right = road.find("lanes/laneSection/right")
        for lane in list(right)[1:]:
            right.remove(lane)
    for _, c in corner_connections(r):
        for ll in c.findall("laneLink")[1:]:
            c.remove(ll)
    save(r, "one_lane")

    r = copy.deepcopy(root0)
    for road in corner_roads(r):
        pv = road.find("planView")
        g0 = pv.find("geometry")
        for g in pv.findall("geometry"):
            pv.remove(g)
        line = etree.SubElement(pv, "geometry", s="0", x=g0.get("x"), y=g0.get("y"), hdg=g0.get("hdg"),
                                length=road.get("length"))
        etree.SubElement(line, "line")
    save(r, "straight")
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:]
    if args and args[0] == "--variants":
        for path in [Path(args[1]), *variants(Path(args[1]))]:
            print(f"{path.name}: RM_Init {rm_init_time(path):.1f} s", flush=True)
        return
    rm = bool(args) and args[0] == "--rm"
    for p in map(Path, args[1:] if rm else args):
        t = rm_init_time(p) if rm else load_time(p)
        print(f"{p.parent.name}/{p.name}: {p.stat().st_size / 1e6:.1f} MB, "
              f"{'RM_Init' if rm else 'load'} {t:.1f} s", flush=True)


if __name__ == "__main__":
    main()
