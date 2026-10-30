"""Stage B4: road marks of the example set, against an earlier phase.

  python plans/scripts/b4_marks.py OLD_DIR NEW_DIR [--json OUT.json]
  e.g. python plans/scripts/b4_marks.py out/stage_b/merge out/stage_b/b4 \
           --json plans/baselines/2026-10-30-stage-b-after-b4-hd.json

Both directories come from stage_b_examples.sh. Per network: status, pipeline
time, .xodr size, repaired edges (graph metadata "fit"), mark records by
type, and the length of each mark type per km of normal road (lane borders
and centre line together).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from lxml import etree


def summary(folder: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in (folder / "summary.jsonl").read_text().splitlines() if line.strip()]
    return {r["name"]: r for r in rows}


def marks(xodr: Path) -> dict:
    root = etree.parse(str(xodr)).getroot()
    records: Counter = Counter()
    length_km: Counter = Counter()
    road_km = 0.0
    for road in root.findall("road"):
        if road.get("junction", "-1") != "-1":
            continue
        road_km += float(road.get("length")) / 1000.0
        sections = road.findall("lanes/laneSection")
        bounds = [float(s.get("s")) for s in sections] + [float(road.get("length"))]
        for k, sec in enumerate(sections):
            sec_len = bounds[k + 1] - bounds[k]
            for lane in sec.iter("lane"):
                ms = lane.findall("roadMark")
                starts = [float(m.get("sOffset", "0")) for m in ms] + [sec_len]
                for m, a, b in zip(ms, starts, starts[1:]):
                    kind = m.get("type")
                    records[m.get("type")] += 1
                    length_km[kind] += (b - a) / 1000.0
    return {"records": dict(sorted(records.items())),
            "km_per_road_km": {k: round(v / road_km, 3) for k, v in sorted(length_km.items())}}


def repaired(folder: Path, name: str) -> int:
    path = folder / f"{name}.json"
    if not path.exists():
        return 0
    return json.loads(path.read_text()).get("metadata", {}).get("fit", {}).get("repaired_edges", 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    old, new = summary(args.old), summary(args.new)
    out = {}
    for name, row in new.items():
        before = old.get(name, {})
        out[name] = {
            "status": row["status"],
            "pipeline_s": [before.get("pipeline_s"), row["pipeline_s"]],
            "xodr_mb": [round(before.get("xodr_bytes", 0) / 1e6, 2), round(row["xodr_bytes"] / 1e6, 2)],
            "repaired_edges": [repaired(args.old, name), repaired(args.new, name)],
            "marks": marks(args.new / f"{name}.xodr"),
        }
        o = out[name]
        print(f"{name:22s} {row['status']:10s} pipeline {o['pipeline_s']} s  xodr {o['xodr_mb']} MB  "
              f"repaired {o['repaired_edges']}  {o['marks']['km_per_road_km']}")
    if args.json:
        args.json.write_text(json.dumps({"old": str(args.old), "new": str(args.new), "networks": out}, indent=2) + "\n")


if __name__ == "__main__":
    main()
