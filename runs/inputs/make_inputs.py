"""Writes the example input images used by runs/examples.sh (deterministic).

    docker compose run --rm -T dev python runs/inputs/make_inputs.py
"""

from pathlib import Path

import numpy as np
from matplotlib.image import imsave

HERE = Path(__file__).parent
N = 300  # pixels per side; first row is the north edge
y, x = np.mgrid[0:N, 0:N] / (N - 1)

# Heightmap: two hills and a valley running north-south, 0..1 gray.
z = (np.exp(-((x - 0.3) ** 2 + (y - 0.35) ** 2) / 0.03)
     + 0.7 * np.exp(-((x - 0.75) ** 2 + (y - 0.7) ** 2) / 0.05)
     + 0.15 * np.sin(6 * x) * np.cos(4 * y))
z = (z - z.min()) / (z.max() - z.min())
imsave(HERE / "heightmap.png", z, cmap="gray", vmin=0, vmax=1)

# Terrain: land with a lake in the north-west, a river along x = 0.62 and a park.
land, water, veg = (0.91, 0.87, 0.78), (0.23, 0.48, 0.78), (0.42, 0.62, 0.29)
rgb = np.empty((N, N, 3))
rgb[:] = land
rgb[(x - 0.22) ** 2 + (y - 0.2) ** 2 < 0.012] = water
rgb[np.abs(x - 0.62 - 0.04 * np.sin(9 * y)) < 0.018] = water
rgb[(x - 0.35) ** 2 + (y - 0.72) ** 2 < 0.008] = veg
imsave(HERE / "terrain.png", rgb)
