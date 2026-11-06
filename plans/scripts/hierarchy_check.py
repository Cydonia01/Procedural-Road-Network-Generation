"""Lane jumps and class changes at two-road nodes and along straight continuations
of lsystem presets without maps (stage R1 check).

    python plans/scripts/hierarchy_check.py [preset ...]
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.io import save_json

sys.path.insert(0, str(Path(__file__).parent))
from refinement_probe import probe


def check(preset: str, seed: int = 1, **overrides) -> dict:
    t = time.perf_counter()
    g = LSystemGenerator().generate(LSystemParams.preset(preset, **overrides), seed)
    dt = time.perf_counter() - t
    two = jumps2 = 0
    for n in g.nodes:
        es = g.incident_edges(n)
        if len(es) == 2:
            two += 1
            a, b = es
            jumps2 += (a.total_lanes != b.total_lanes) or (a.road_class != b.road_class)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "g.json"
        save_json(g, path)
        out = probe(str(path))
    out.update(file=preset, seconds=round(dt, 2), two_road_nodes=two, two_road_changes=jumps2)
    return out


if __name__ == "__main__":
    for name in sys.argv[1:] or ["grid", "manhattan_like", "organic_town", "paris", "suburb", "skewed_grid"]:
        print(check(name))
