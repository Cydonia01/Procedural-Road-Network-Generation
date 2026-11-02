"""Stage B6: traffic lights of the example set, against B5.

  python plans/scripts/b6_lights.py OLD_DIR NEW_DIR [--json OUT.json]
  e.g. python plans/scripts/b6_lights.py out/stage_b/b5 out/stage_b/b6 \
           --json plans/baselines/2026-10-30-stage-b-after-b6-hd.json

Both directories come from stage_b_examples.sh. Per network: status,
pipeline time and .xodr size (old, new, growth); junctions with lights,
lights (roadside, overhead), controllers; plan cycles, phase counts and
green times; and, where the example set wrote one (city, mixed_city: 50
cars, 30 s), the .xosc's size, controllers, light events and actions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from lxml import etree


def summary(folder: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in (folder / "summary.jsonl").read_text().splitlines() if line.strip()]
    return {r["name"]: r for r in rows}


def lights(folder: Path, name: str) -> dict:
    xodr = folder / f"{name}.xodr"
    root = etree.parse(str(xodr)).getroot()
    side = json.loads(xodr.with_suffix(".roadmap.json").read_text())
    plans = side.get("signal_plans", [])
    heads = [s for s in root.iter("signal") if s.get("dynamic") == "yes"]
    out = {
        "junctions_with_lights": len(plans),
        "lights": len(heads),
        "overhead_lights": sum(float(s.get("zOffset")) > 5.0 for s in heads),
        "controllers": len(root.findall("controller")),
        "junction_controllers": len(root.findall("junction/controller")),
    }
    if plans:
        cycles = np.array([p["cycle"] for p in plans])
        greens = np.array([ph["duration"] for p in plans for ph in p["phases"] if ph["kind"] == "green"])
        groups = np.array([len(p["groups"]) for p in plans])
        out.update({"cycle_s": {"min": float(cycles.min()), "median": float(np.median(cycles)),
                                "max": float(cycles.max())},
                    "phase_groups": {str(k): int((groups == k).sum()) for k in sorted(set(groups.tolist()))},
                    "green_s": {"min": float(greens.min()), "median": float(np.median(greens)),
                                "max": float(greens.max())}})
    xosc = folder / f"{name}.xosc"
    if xosc.exists():
        sroot = etree.parse(str(xosc)).getroot()
        out["xosc"] = {"mb": round(xosc.stat().st_size / 1e6, 3),
                       "controllers": len(sroot.findall("RoadNetwork/TrafficSignals/TrafficSignalController")),
                       "events": len(sroot.findall(".//Story[@name='lights']//Event")),
                       "actions": len(sroot.findall(".//TrafficSignalStateAction"))}
    return out


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
        b0, b1 = before.get("xodr_bytes", 0), row["xodr_bytes"]
        out[name] = {"status": row["status"], "pipeline_s": [before.get("pipeline_s"), row["pipeline_s"]],
                     "xodr_mb": [round(b0 / 1e6, 2), round(b1 / 1e6, 2)],
                     "growth": round(b1 / b0 - 1, 4) if b0 else None, **lights(args.new, name)}
        o = out[name]
        print(f"{name:20s} {o['status']:8s} {o['xodr_mb']} MB (+{100 * (o['growth'] or 0):.2f} %) "
              f"lights at {o['junctions_with_lights']} junctions: {o['lights']} ({o['overhead_lights']} overhead), "
              f"cycle {o.get('cycle_s')}, groups {o.get('phase_groups')}, xosc {o.get('xosc')}")
    if args.json:
        args.json.write_text(json.dumps({"old": str(args.old), "new": str(args.new), "networks": out}, indent=2)
                             + "\n")


if __name__ == "__main__":
    main()
