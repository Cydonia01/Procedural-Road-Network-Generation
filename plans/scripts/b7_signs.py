"""Stage B7: crosswalks and signs of the example set, against B6.

  python plans/scripts/b7_signs.py OLD_DIR NEW_DIR [--json OUT.json]
  e.g. python plans/scripts/b7_signs.py out/stage_b/b6 out/stage_b/b7 \
           --json plans/baselines/2026-10-30-stage-b-after-b7-hd.json

Both directories come from stage_b_examples.sh. Per network: status,
pipeline time and .xodr size (old, new, growth); crosswalks (zebras, at
lights) and their share of junction arms with control; signs by code and
speed signs per km of road; curve warnings against the advisory corners of
the sidecar; 306 / 307 and the 301s left; junctions with lights (old, new)
and those with a pedestrian phase.
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


def signs(folder: Path, name: str) -> dict:
    xodr = folder / f"{name}.xodr"
    root = etree.parse(str(xodr)).getroot()
    side = json.loads(xodr.with_suffix(".roadmap.json").read_text())
    normal = [r for r in root.findall("road") if r.get("junction", "-1") == "-1"]
    km = sum(float(r.get("length")) for r in normal) / 1000.0
    codes = Counter(s.get("type") + (f"-{s.get('subtype')}" if s.get("subtype") not in ("-1", "", None) else "")
                    for s in root.iter("signal"))
    walks = [o for o in root.iter("object") if o.get("type") == "crosswalk"]
    at_lights = sum(len(o.findall("markings/marking")) == 2 for o in walks)
    controlled = [j for j in side.get("junctions", []) if j["control"] != "none"]
    arms = sum(len(j["approaches"]) for j in controlled)
    advisory = sum(c["advisory_speed"] is not None for c in side.get("corners", []))
    plans = side.get("signal_plans", [])
    return {
        "road_km": round(km, 2),
        "crosswalks": len(walks), "zebras": len(walks) - at_lights, "at_lights": at_lights,
        "crosswalk_share_of_controlled_arms": round(len(walks) / arms, 3) if arms else None,
        "signs": dict(sorted(codes.items())),
        "speed_signs_per_km": round(codes.get("274", 0) / km, 2) if km else None,
        "advisory_corners": advisory,
        "curve_warnings": sum(v for k, v in codes.items() if k.startswith(("103", "105"))),
        "junctions_with_lights": len(plans),
        "pedestrian_phases": sum(any(p["kind"] == "pedestrian" for p in plan["phases"]) for plan in plans),
    }


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
        old_lights = len(json.loads((args.old / f"{name}.roadmap.json").read_text()).get("signal_plans", []))
        out[name] = {"status": row["status"], "pipeline_s": [before.get("pipeline_s"), row["pipeline_s"]],
                     "xodr_mb": [round(b0 / 1e6, 2), round(b1 / 1e6, 2)],
                     "growth": round(b1 / b0 - 1, 4) if b0 else None, "junctions_with_lights_before": old_lights,
                     **signs(args.new, name)}
        o = out[name]
        print(f"{name:20s} {o['status']:8s} {o['xodr_mb']} MB (+{100 * (o['growth'] or 0):.1f} %) "
              f"crosswalks {o['crosswalks']} ({o['at_lights']} at lights, {o['crosswalk_share_of_controlled_arms']} "
              f"of arms), lights {old_lights}->{o['junctions_with_lights']} ({o['pedestrian_phases']} ped.), "
              f"274/km {o['speed_signs_per_km']}, curves {o['curve_warnings']}/{o['advisory_corners']}, "
              f"signs {o['signs']}")
    if args.json:
        args.json.write_text(json.dumps({"old": str(args.old), "new": str(args.new), "networks": out}, indent=2)
                             + "\n")


if __name__ == "__main__":
    main()
