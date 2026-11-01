"""Stage B10: the final measurement of the example set, in the format of B0's baseline.

  python plans/scripts/b10_baseline.py OUTDIR BASELINE.json [CODE]
  e.g. python plans/scripts/b10_baseline.py out/stage_b/b10 plans/baselines/2026-11-01-stage-b-after-hd.json

Reads what `stage_b_examples.sh OUTDIR` wrote (summary.jsonl, <name>.metrics.json,
<name>.run.log) and adds the network metrics of each <name>.json (`metrics --graph`,
run here). Per network: status, pipeline time, .xodr size, the simulate numbers
(load, stepping, peak memory, errors and warnings), the HD-map metrics, the geometry
counts and lateral acceleration, and the network metrics. Run in the dev container.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

from roadgen.config import DEFAULTS, section
from roadgen.graph.io import load_json
from roadgen.metrics import network

SIM = re.compile(r"simulated in ([\d.]+) s wall .*?, (\d+) errors, (\d+) warnings")
LOAD = re.compile(r"load ([\d.]+) s, stepping [\d.]+ s \(([\d.]+)x real time\), peak memory (\d+) MB")
DRIVE = re.compile(r"drive-check: RoadManager load ([\d.]+) s")


def simulate(log: str) -> dict | None:
    sim, load = SIM.search(log), LOAD.search(log)
    if not (sim and load):
        return None
    return {"load_s": float(load[1]), "stepping_rtf": float(load[2]), "wall_s": float(sim[1]),
            "peak_mb": int(load[3]), "errors": int(sim[2]), "warnings": int(sim[3])}


def main() -> None:
    out, target = Path(sys.argv[1]), Path(sys.argv[2])
    code = sys.argv[3] if len(sys.argv) > 3 else "dev/stage-b, B10 (HD maps on by default)"
    params = network.NetworkMetricsParams(**section(DEFAULTS, "network_metrics"))
    networks = []
    for line in (out / "summary.jsonl").read_text().splitlines():
        rec = json.loads(line)
        name = rec["name"]
        log = (out / f"{name}.run.log").read_text()
        rec["simulate"] = simulate(log)
        drive = DRIVE.search(log)
        rec["drive_check_load_s"] = float(drive[1]) if drive else None
        m = json.loads((out / f"{name}.metrics.json").read_text())["networks"][0]
        rec["hdmap"] = m["hdmap"]
        rec["geometry"] = {"counts": m["counts"], "lateral_acceleration": m["lateral_acceleration"]}
        graph = load_json(out / f"{name}.json")
        rec["fit"] = graph.metadata.get("fit")
        rec["network"] = network.network_metrics(graph, params)
        networks.append(rec)
    target.write_text(json.dumps({
        "recorded": datetime.date.today().isoformat(), "code": code,
        "command": f"bash plans/scripts/stage_b_examples.sh {out}", "networks": networks,
    }, indent=2) + "\n")
    print(f"wrote {target}: {len(networks)} networks")


if __name__ == "__main__":
    main()
