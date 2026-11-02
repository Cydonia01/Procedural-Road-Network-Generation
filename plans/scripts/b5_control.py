"""Stage B5: junction control of the example set, against B4.

  python plans/scripts/b5_control.py OLD_DIR NEW_DIR [--json OUT.json]
  e.g. python plans/scripts/b5_control.py out/stage_b/b4 out/stage_b/b5 \
           --json plans/baselines/2026-10-30-stage-b-after-b5-hd.json

Both directories come from stage_b_examples.sh. Per network: status,
pipeline time and .xodr size (old, new, growth), the control mix of the
junctions of three or more roads (sidecar "junctions"), approach roles,
signs by code, stop and yield lines, <priority> records and their share of
the file (bytes of the records / file size; the plan's rule: priorities off
by default if the file grows by more than 30 %), and the annotate counts.
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


def control(folder: Path, name: str) -> dict:
    xodr = folder / f"{name}.xodr"
    side = json.loads(xodr.with_suffix(".roadmap.json").read_text())
    root = etree.parse(str(xodr)).getroot()
    junctions = [j for j in side.get("junctions", []) if len(j["approaches"]) >= 3]
    mix = Counter(j["control"] for j in junctions)
    roles = Counter(a["role"] for j in junctions for a in j["approaches"])
    signs = Counter(s.get("type") for s in root.iter("signal"))
    lines = Counter(o.get("name") for o in root.iter("object") if o.get("type") == "roadMark")
    priorities = root.findall("junction/priority")
    record_bytes = sum(len(etree.tostring(p)) + 8 for p in priorities)  # + indentation
    graph = json.loads((folder / f"{name}.json").read_text())
    return {
        "junctions_3plus": len(junctions),
        "control_mix": {k: round(v / max(len(junctions), 1), 3) for k, v in sorted(mix.items())},
        "control_counts": dict(sorted(mix.items())),
        "approach_roles": dict(sorted(roles.items())),
        "signs": dict(sorted(signs.items())),
        "lines": dict(sorted(lines.items())),
        "priority_records": len(priorities),
        "priority_share_of_file": round(record_bytes / xodr.stat().st_size, 4),
        "annotate": graph.get("metadata", {}).get("annotate"),
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
                     "growth": round(b1 / b0 - 1, 4) if b0 else None, **control(args.new, name)}
        o = out[name]
        print(f"{name:20s} {o['status']:8s} {o['xodr_mb']} MB (+{100 * (o['growth'] or 0):.1f} %) "
              f"mix {o['control_counts']} signs {o['signs']} lines {o['lines']} "
              f"priorities {o['priority_records']} ({100 * o['priority_share_of_file']:.1f} % of file)")
    if args.json:
        args.json.write_text(json.dumps({"old": str(args.old), "new": str(args.new), "networks": out}, indent=2)
                             + "\n")


if __name__ == "__main__":
    main()
