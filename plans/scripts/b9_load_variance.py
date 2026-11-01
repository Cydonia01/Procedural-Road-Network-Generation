"""Stage B9 (prompt 12): how much esmini's RoadManager load varies on one file, and
whether address-space randomisation decides it.

  python plans/scripts/b9_load_variance.py FILE.xodr [RUNS] [--no-aslr]

Times RM_Init on FILE RUNS times (default 5), each in a fresh process, and prints
every run's wall and CPU time, then best, median and worst. With --no-aslr every
process runs under `setarch -R` (no address-space layout randomisation), so each
starts with the same memory layout: if the slow runs depend on addresses (heap
layout, uninitialised memory), they become all fast or all slow. Run in the dev
container.
"""
from __future__ import annotations

import subprocess
import sys

TIMER = """
import sys, time
from roadgen.sim import drive_check
lib = drive_check._load_rm()
t = time.perf_counter(); cpu = time.process_time()
assert lib.RM_Init(sys.argv[1].encode()) == 0
print(time.perf_counter() - t, time.process_time() - cpu)
"""


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--no-aslr"]
    no_aslr = "--no-aslr" in sys.argv
    path, runs = args[0], int(args[1]) if len(args) > 1 else 5
    prefix = ["setarch", "-R"] if no_aslr else []
    times = []
    for k in range(runs):
        out = subprocess.run(prefix + [sys.executable, "-c", TIMER, path], capture_output=True, text=True, check=True)
        wall, cpu = map(float, out.stdout.split()[-2:])
        times.append(wall)
        print(f"run {k + 1}: wall {wall:.1f} s, CPU {cpu:.1f} s", flush=True)
    times.sort()
    print(f"{path}{' (no ASLR)' if no_aslr else ''}: best {times[0]:.1f} s, median {times[len(times) // 2]:.1f}, "
          f"worst {times[-1]:.1f}")


if __name__ == "__main__":
    main()
