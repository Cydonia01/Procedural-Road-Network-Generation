"""Fingerprints of lsystem networks that new features must leave unchanged.

Stage C adds parameters to lsystem (one-way patterns, ring road, pattern
fields, ...), all off by default: the networks of the existing presets must
stay exactly as they were. A fingerprint hashes the nodes, edges and metadata
of the graph JSON except metadata["params"], which lists every parameter and
so grows with each new one. On input maps it leaves out the heights too (node
z and radius_cap, edge profile and bridges, and the height steps' metadata):
align and drape follow the builder's fitted roads, which geometry fixes may
change, while the network the generator grows must not. The expected values
were recorded before stage C (41b8363), re-recorded in stage R1 (the road
hierarchy from strokes) and once more when the polish stage removed old
presets and moved hill_town to the contour pattern (2026-11-08).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.io import graph_to_dict, load_json
from roadgen.graph.model import RoadGraph

EXPECTED = Path(__file__).parent / "data" / "lsystem_identity.json"
# (name, preset or None for the defaults, seed, with input maps)
CASES = [
    ("default", None, 0, False),
    ("manhattan_like", "manhattan_like", 0, False),
    ("organic_town", "organic_town", 1, False),
    ("city_maps", "city", 1, True),
    ("hill_town_maps", "hill_town", 1, True),
]


HEIGHTS = {"z", "radius_cap", "profile", "bridges", "tunnels"}
HEIGHT_STEPS = {"drape", "align", "grade_separation"}  # metadata of the height steps (their params)


def fingerprint(graph: RoadGraph, heights: bool = True) -> str:
    data = graph_to_dict(graph)
    data["metadata"] = {k: v for k, v in data["metadata"].items() if k != "params"}
    if not heights:
        data["metadata"] = {k: v for k, v in data["metadata"].items() if k not in HEIGHT_STEPS}
        data["nodes"] = [{k: v for k, v in n.items() if k not in HEIGHTS} for n in data["nodes"]]
        data["edges"] = [{k: v for k, v in e.items() if k not in HEIGHTS} for e in data["edges"]]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def build(preset, seed: int, maps: bool) -> RoadGraph:
    if not maps:
        params = LSystemParams.preset(preset) if preset else LSystemParams()
        return LSystemGenerator().generate(params, seed)
    from cli import main  # the CLI builds the maps exactly as `pipeline --maps` does

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "net"
        # --no-hd: the network as grown (with HD maps, annotate and the cross-section fit change it)
        argv = ["--no-history", "pipeline", "lsystem", "--maps", "--allow-traps", "--no-hd",
                "--seed", str(seed), "-o", str(out)]
        if preset:
            argv[3:3] = ["--preset", preset]
        if main(argv) != 0:
            raise RuntimeError(f"pipeline failed: {argv}")
        return load_json(out.with_suffix(".json"))


def fingerprints() -> dict[str, str]:
    return {name: fingerprint(build(preset, seed, maps), heights=not maps) for name, preset, seed, maps in CASES}


if __name__ == "__main__":  # record the expected values (only before an intended change)
    EXPECTED.write_text(json.dumps(fingerprints(), indent=2) + "\n")
    print(EXPECTED.read_text())
