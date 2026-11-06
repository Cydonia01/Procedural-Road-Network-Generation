"""Ground poke-through (stage R4): road-surface samples the ground mesh covers, for
saved pipeline outputs (graph JSON + elevation / terrain maps next to it).

    python plans/scripts/poke_through.py out/<hash>/city ...   # prefix without .json
"""

from __future__ import annotations

import sys
import time

from roadgen.graph.io import load_json
from roadgen.maps.model import load_npz
from roadgen.scene.ground import GroundParams, ground_heights, poke_through

for prefix in sys.argv[1:]:
    g = load_json(prefix + ".json")
    elevation, terrain = load_npz(prefix + "_elevation.npz"), load_npz(prefix + "_terrain.npz")
    t = time.perf_counter()
    x, y, z = ground_heights(elevation, g, GroundParams(), terrain)
    n, bad, worst = poke_through(x, y, z, g)
    print(f"{prefix}: {bad} of {n} samples covered, worst {worst:.2f} m ({time.perf_counter() - t:.1f} s)")
