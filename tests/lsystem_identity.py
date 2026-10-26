"""Fingerprints of lsystem networks that new features must leave unchanged.

Stage C adds parameters to lsystem (one-way patterns, ring road, pattern
fields, ...), all off by default: the networks of the existing presets must
stay exactly as they were. A fingerprint hashes the nodes, edges and metadata
of the graph JSON except metadata["params"], which lists every parameter and
so grows with each new one.
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
    ("grid", "grid", 0, False),
    ("organic_curved", "organic_curved", 1, False),
    ("grid_organic", "grid_organic", 2, False),
    ("city_maps", "city", 1, True),
    ("hill_town_maps", "hill_town", 1, True),
]


def fingerprint(graph: RoadGraph) -> str:
    data = graph_to_dict(graph)
    data["metadata"] = {k: v for k, v in data["metadata"].items() if k != "params"}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def build(preset, seed: int, maps: bool) -> RoadGraph:
    if not maps:
        params = LSystemParams.preset(preset) if preset else LSystemParams()
        return LSystemGenerator().generate(params, seed)
    from cli import main  # the CLI builds the maps exactly as `pipeline --maps` does

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "net"
        argv = ["--no-history", "pipeline", "lsystem", "--maps", "--allow-traps",
                "--seed", str(seed), "-o", str(out)]
        if preset:
            argv[3:3] = ["--preset", preset]
        if main(argv) != 0:
            raise RuntimeError(f"pipeline failed: {argv}")
        return load_json(out.with_suffix(".json"))


def fingerprints() -> dict[str, str]:
    return {name: fingerprint(build(preset, seed, maps)) for name, preset, seed, maps in CASES}


if __name__ == "__main__":  # record the expected values (only before an intended change)
    EXPECTED.write_text(json.dumps(fingerprints(), indent=2) + "\n")
    print(EXPECTED.read_text())
