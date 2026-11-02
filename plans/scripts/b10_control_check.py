"""Stage B10: the control-mix exit criterion on the example set.

  python plans/scripts/b10_control_check.py OUTDIR [NAME ...]
  e.g. python plans/scripts/b10_control_check.py out/stage_b/b10 city mixed_city

From each network's graph (<name>.json) and sidecar (<name>.roadmap.json): every
at-grade junction where an avenue or a highway crosses or meets another avenue or
highway should have lights. That is: three or more arms, and after the two highest
arms (the road through) the next one is at least an avenue. A road changing class
(highway to avenue) where a street joins does not count, nor do roundabouts or ramp
junctions (their own control). Prints, per network, how many such junctions there
are, how many have lights, and the others with their control and arm classes; then
the junctions per control kind and the approach roles (yield and stop are roles of
`priority` junctions).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RANK = {"street": 0, "avenue": 1, "highway": 2}


def check(out: Path, name: str) -> None:
    graph = json.loads((out / f"{name}.json").read_text())
    side = json.loads((out / f"{name}.roadmap.json").read_text())
    classes: dict[int, list[str]] = {}
    for e in graph["edges"]:
        for n in (e["from_node"], e["to_node"]):
            classes.setdefault(n, []).append(e["road_class"])
    want = have = 0
    others = []
    for j in side.get("junctions", []):
        arms = Counter(classes.get(j["node"], []))
        ranks = sorted((RANK[c] for c in classes.get(j["node"], [])), reverse=True)
        if len(ranks) < 3 or ranks[2] < RANK["avenue"] or j["control"] in ("roundabout", "none"):
            continue
        want += 1
        if j["control"] == "signal":
            have += 1
        else:
            others.append((j["node"], j["control"], dict(arms)))
    kinds = Counter(j["control"] for j in side.get("junctions", []))
    roles = Counter(a["role"] for j in side.get("junctions", []) for a in j["approaches"])
    print(f"{name}: avenue x avenue / highway x avenue junctions {want}, with lights {have}; "
          f"controls {dict(kinds)}; approach roles {dict(roles)}")
    for node, control, arms in others:
        print(f"  node {node}: {control}, arms {arms}")


def main() -> None:
    out = Path(sys.argv[1])
    names = sys.argv[2:] or [json.loads(line)["name"] for line in (out / "summary.jsonl").read_text().splitlines()]
    for name in names:
        check(out, name)


if __name__ == "__main__":
    main()
