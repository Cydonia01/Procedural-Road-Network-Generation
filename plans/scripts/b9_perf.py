"""Stage B9: performance with HD maps against B0, best of three with the spread.

  python plans/scripts/b9_perf.py [RUNS] [--json OUT.json] [-- EXTRA PIPELINE FLAGS]
  e.g. python plans/scripts/b9_perf.py 3 --json plans/baselines/2026-10-31-stage-b-b9-perf.json

B0 needs no old checkout: with HD maps off the builder's output is byte-identical to
B0's (tests/test_hdmap_identity.py), so `pipeline` without --hd builds B0's network.
For `city` (seed 7) and `mixed_city` (seed 1) on maps, with HD off and on, RUNS times
each (default 3, one after the other):
- `pipeline` without the drive-check: wall time and its timed steps (generate, fit
  cross-sections, build, write, validate, plot lanes) from its log;
- `.xodr` size;
- `scenario` (50 vehicles, 30 s, seed 1) once, then `simulate` RUNS times: esmini's
  load time, stepping real-time factor and peak memory from its log.
Prints best, median and worst of each. Runs are not recorded (--no-history). Run in
the dev container, with nothing else running.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

OUT = Path("out/b9/perf")
NETWORKS = (("city", 7), ("mixed_city", 1))
STEP = re.compile(r"INFO roadgen\.cli: ([a-z][a-z -]+(?: lsystem)?): ([0-9.]+) s$")
SIM = re.compile(r"load ([0-9.]+) s, stepping ([0-9.]+) s \(([0-9.]+)x real time\), peak memory ([0-9]+) MB")


def cli(*args: str) -> tuple[float, str]:
    t = time.perf_counter()
    out = subprocess.run([sys.executable, "-m", "cli", "--no-history", *args], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{args}: {out.stderr[-2000:]}")
    return time.perf_counter() - t, out.stderr + out.stdout


def spread(values: list[float]) -> dict:
    v = sorted(values)
    return {"best": round(v[0], 2), "median": round(v[len(v) // 2], 2), "worst": round(v[-1], 2)}


def main() -> None:
    args = sys.argv[1:]
    extra = args[args.index("--") + 1:] if "--" in args else []
    args = args[:args.index("--")] if "--" in args else args
    json_out = Path(args[args.index("--json") + 1]) if "--json" in args else None
    runs = int(args[0]) if args and args[0].isdigit() else 3
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, seed in NETWORKS:
        for hd in (False, True):
            key = f"{name}, HD {'on' if hd else 'off (= B0)'}"
            prefix = OUT / f"{name}_{'hd' if hd else 'b0'}"
            walls, steps = [], {}
            for _ in range(runs):
                wall, log = cli("pipeline", "lsystem", "--preset", name, "--seed", str(seed), "--maps",
                                "--allow-traps", "--hd" if hd else "--no-hd", *extra, "-o", str(prefix))
                walls.append(wall)
                for line in log.splitlines():
                    m = STEP.search(line)
                    if m:
                        steps.setdefault(m.group(1), []).append(float(m.group(2)))
            xodr = prefix.with_suffix(".xodr")
            cli("scenario", str(xodr), "--vehicles", "50", "--duration", "30", "--seed", "1", "-o",
                str(prefix.with_suffix(".xosc")))
            sims = []
            for _ in range(runs):
                _, log = cli("simulate", str(prefix.with_suffix(".xosc")))
                m = SIM.search(log)
                sims.append(tuple(map(float, m.groups())))
            report[key] = {
                "pipeline_s": spread(walls),
                "steps_s": {k: spread(v) for k, v in steps.items()},
                "xodr_mb": round(xodr.stat().st_size / 1e6, 2),
                "esmini_load_s": spread([s[0] for s in sims]),
                "real_time_factor": spread([s[2] for s in sims]),
                "peak_memory_mb": spread([s[3] for s in sims]),
            }
            r = report[key]
            print(f"{key}: pipeline {r['pipeline_s']}, {r['xodr_mb']} MB, esmini load {r['esmini_load_s']}, "
                  f"real time x{r['real_time_factor']}, peak {r['peak_memory_mb']} MB", flush=True)
            for k, v in r["steps_s"].items():
                print(f"    {k}: {v}", flush=True)
    if json_out:
        json_out.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
