"""Stage B9 (prompt 12): where the .xodr write time goes.

  python plans/scripts/b9_write_profile.py GRAPH.json SEED [RUNS]
  e.g. python plans/scripts/b9_write_profile.py out/stage_b/b8r/city.json 7

Builds the graph (as written by the pipeline: annotated and fitted) with HD maps on
(seeded like the pipeline run), then times OpenDriveBuilder.write RUNS times (default
3; best, median, worst) and profiles one more write with cProfile: the 20 functions
with most cumulative time, and the builder's own write steps. Run in the dev container.
"""
from __future__ import annotations

import cProfile
import pstats
import sys
import time
from pathlib import Path

from roadgen.config import DEFAULTS
from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams

OUT = Path("out/b9/write")


def main() -> None:
    graph = load_json(sys.argv[1])
    seed = int(sys.argv[2])
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    OUT.mkdir(parents=True, exist_ok=True)
    b = OpenDriveBuilder(junction_margin=DEFAULTS["opendrive"]["junction_margin"],
                         hd=HdMapParams.from_config(DEFAULTS, seed, True))
    t = time.perf_counter()
    b.build(graph)
    print(f"build {time.perf_counter() - t:.1f} s")
    times = []
    for k in range(runs):
        b = OpenDriveBuilder(junction_margin=DEFAULTS["opendrive"]["junction_margin"],
                             hd=HdMapParams.from_config(DEFAULTS, seed, True))
        b.build(graph)
        t = time.perf_counter()
        b.write(OUT / "net.xodr")
        times.append(time.perf_counter() - t)
    times.sort()
    print(f"write best {times[0]:.2f} s, median {times[len(times) // 2]:.2f}, worst {times[-1]:.2f}")
    b = OpenDriveBuilder(junction_margin=DEFAULTS["opendrive"]["junction_margin"],
                         hd=HdMapParams.from_config(DEFAULTS, seed, True))
    b.build(graph)
    prof = cProfile.Profile()
    prof.enable()
    b.write(OUT / "net.xodr")
    prof.disable()
    stats = pstats.Stats(prof).sort_stats("cumulative")
    stats.print_stats(20)
    stats.print_stats(r"builder\.py.*(_fill|_add|write|indent)")


if __name__ == "__main__":
    main()
