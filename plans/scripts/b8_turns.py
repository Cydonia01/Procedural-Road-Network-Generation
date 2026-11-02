"""Stage B8: lane use, pockets, bans, arrows and the junction-surface fill of the example
set, against the previous phase.

  python plans/scripts/b8_turns.py OLD_DIR NEW_DIR [--json OUT.json]
  e.g. python plans/scripts/b8_turns.py out/stage_b/b7z out/stage_b/b8 \
           --json plans/baselines/2026-10-31-stage-b-after-b8-hd.json

Both directories come from stage_b_examples.sh. Per network: status,
pipeline time and .xodr size (old, new, growth), repaired edges (graph
metadata "fit") and the turn pockets left out instead of a repair (prompt 11, "fit"
"dropped_pockets"); approaches with a left-turn pocket; banned turns by reason;
lane arrows; junctions with lights and with protected left phases; and the
junction surface no lane covers (holes inside a junction's hull, as in
b3_gaps.py) before and after the fill patches: the patches' count and area.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from lxml import etree
from shapely.geometry import Polygon


def summary(folder: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in (folder / "summary.jsonl").read_text().splitlines() if line.strip()]
    return {r["name"]: r for r in rows}


def fit(folder: Path, name: str, key: str) -> int:
    path = folder / f"{name}.json"
    return json.loads(path.read_text()).get("metadata", {}).get("fit", {}).get(key, 0) if path.exists() else 0


def patches(root: etree._Element) -> tuple[int, float]:
    """Count and area [m^2] of the junction-surface patches."""
    n, area = 0, 0.0
    for road in root.findall("road"):
        objs = [o for o in road.findall("objects/object") if o.get("type") == "patch"]
        if not objs:
            continue
        for o in objs:
            pts = [(float(c.get("u")), float(c.get("v"))) for c in o.findall("outlines/outline/cornerLocal")]
            n += 1
            area += Polygon(pts).area if len(pts) >= 3 else 0.0
    return n, area


def turns(folder: Path, name: str) -> dict:
    xodr = folder / f"{name}.xodr"
    root = etree.parse(str(xodr)).getroot()
    side = json.loads(xodr.with_suffix(".roadmap.json").read_text())
    uses = side.get("lane_use", [])
    arrows = Counter(o.get("name") for o in root.iter("object") if o.get("name", "").startswith("arrow"))
    plans = side.get("signal_plans", [])
    n, area = patches(root)
    return {
        "approaches": len(uses),
        "pocket_approaches": sum(bool(u["pockets"]) for u in uses),
        "bans": dict(Counter(b["reason"] for b in side.get("banned", []))),
        "ban_signs": dict(Counter(f"{s.get('type')}-{s.get('subtype')}" for s in root.iter("signal")
                                  if s.get("type") in ("209", "214"))),
        "arrows": sum(arrows.values()), "arrow_kinds": dict(sorted(arrows.items())),
        "junctions_with_lights": len(plans),
        "protected_left_plans": sum(any(p.get("turn") == "left" for p in x["phases"]) for x in plans),
        "fill_patches": n, "fill_area_m2": round(area, 1),
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
        out[name] = {"status": row["status"], "pipeline_s": [before.get("pipeline_s"), row["pipeline_s"]],
                     "xodr_mb": [round(b0 / 1e6, 2), round(b1 / 1e6, 2)],
                     "growth": round(b1 / b0 - 1, 4) if b0 else None,
                     "repaired_edges": [fit(args.old, name, "repaired_edges"), fit(args.new, name, "repaired_edges")],
                     "dropped_pockets": fit(args.new, name, "dropped_pockets"), **turns(args.new, name)}
        o = out[name]
        print(f"{name:20s} {o['status']:8s} {o['xodr_mb']} MB (+{100 * (o['growth'] or 0):.1f} %) repaired "
              f"{o['repaired_edges']} pockets {o['pocket_approaches']}/{o['approaches']} (left out {o['dropped_pockets']}) bans {o['bans']} "
              f"arrows {o['arrows']} lights {o['junctions_with_lights']} ({o['protected_left_plans']} protected) "
              f"fill {o['fill_patches']} / {o['fill_area_m2']} m2")
    if args.json:
        args.json.write_text(json.dumps({"old": str(args.old), "new": str(args.new), "networks": out}, indent=2)
                             + "\n")


if __name__ == "__main__":
    main()
