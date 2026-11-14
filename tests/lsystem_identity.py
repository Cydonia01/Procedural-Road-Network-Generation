"""Fingerprints of lsystem networks that new features must leave unchanged.

New lsystem parameters are off by default: the networks of the existing presets
must stay exactly as they were. A fingerprint hashes the nodes, edges and metadata
of the graph lsystem grows, except metadata["params"], which lists every parameter
and so grows with each new one. On input maps the network is grown on the maps
`pipeline --maps` builds (tests/shared.py), straight from the generator: align,
drape and the HD fit come after it and are not the generator's network. The
expected values were recorded before stage C (41b8363), re-recorded in stage R1
(the road hierarchy from strokes), when the polish stage removed old presets
(2026-11-08), and when the fingerprint moved from the pipeline's output to the
generator's (2026-11-14).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from roadgen.graph.io import graph_to_dict
from roadgen.graph.model import RoadGraph
from tests.shared import grown

EXPECTED = Path(__file__).parent / "data" / "lsystem_identity.json"
# (name, preset or None for the defaults, seed, with input maps)
CASES = [
    ("default", None, 0, False),
    ("manhattan_like", "manhattan_like", 0, False),
    ("organic_town", "organic_town", 1, False),
    ("city_maps", "city", 1, True),
    ("hill_town_maps", "hill_town", 1, True),
]


def fingerprint(graph: RoadGraph) -> str:
    data = graph_to_dict(graph)
    data["metadata"] = {k: v for k, v in data["metadata"].items() if k != "params"}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def build(preset: str | None, seed: int, maps: bool) -> RoadGraph:
    return grown(preset, seed, maps)


def fingerprints() -> dict[str, str]:
    return {name: fingerprint(build(preset, seed, maps)) for name, preset, seed, maps in CASES}


if __name__ == "__main__":  # record the expected values (only before an intended change)
    EXPECTED.write_text(json.dumps(fingerprints(), indent=2) + "\n")
    print(EXPECTED.read_text())
