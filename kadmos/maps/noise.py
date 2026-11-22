"""Seedable, vectorized 2D noise for the map generators (numpy only).

- perlin: Perlin gradient noise [Perlin 1985], about [-1, 1].
- fbm: fractional Brownian motion, octaves of perlin summed with falling
  amplitude, about [-1, 1].
- ridged: Musgrave's ridged multifractal [Musgrave et al. 1989], [0, 1]:
  sharp crests, as in mountain ranges.
- warp: domain warping, i.e. sampling noise at coordinates displaced by
  other noise, which bends straight-looking features into organic ones.

Plus raster helpers the generators share: majority_filter, distance_to,
slope_deg.

Coordinates are in noise units (world metres / feature scale), so a map does
not change shape when only its cell size changes. All randomness comes from
the numpy Generator passed in.
"""

from __future__ import annotations

import math

import numpy as np

PERIOD = 256  # lattice period of perlin, in noise units
_GRADIENTS = np.array([(math.cos(a), math.sin(a)) for a in np.arange(8) * math.pi / 4])


def permutation(rng: np.random.Generator) -> np.ndarray:
    p = rng.permutation(PERIOD)
    return np.concatenate([p, p])


def _fade(t: np.ndarray) -> np.ndarray:
    return t * t * t * (t * (t * 6 - 15) + 10)


def perlin(x: np.ndarray, y: np.ndarray, perm: np.ndarray) -> np.ndarray:
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = x - x0, y - y0
    xi, yi = x0.astype(np.int64) & (PERIOD - 1), y0.astype(np.int64) & (PERIOD - 1)

    def corner(dx: int, dy: int) -> np.ndarray:
        h = perm[perm[(xi + dx) & (PERIOD - 1)] + ((yi + dy) & (PERIOD - 1))] & 7
        g = _GRADIENTS[h]
        return g[..., 0] * (fx - dx) + g[..., 1] * (fy - dy)

    u, v = _fade(fx), _fade(fy)
    n0 = corner(0, 0) + u * (corner(1, 0) - corner(0, 0))
    n1 = corner(0, 1) + u * (corner(1, 1) - corner(0, 1))
    # Unit gradients reach at most sqrt(2)/2 in 2D; rescale to about [-1, 1].
    return (n0 + v * (n1 - n0)) * math.sqrt(2.0)


def fbm(
    x: np.ndarray, y: np.ndarray, rng: np.random.Generator,
    octaves: int = 5, lacunarity: float = 2.0, gain: float = 0.5,
) -> np.ndarray:
    perm = permutation(rng)
    total = np.zeros(np.broadcast(x, y).shape)
    amp, freq, norm = 1.0, 1.0, 0.0
    for _ in range(octaves):
        ox, oy = rng.uniform(0, PERIOD, 2)  # decorrelates the octaves
        total += amp * perlin(x * freq + ox, y * freq + oy, perm)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def ridged(
    x: np.ndarray, y: np.ndarray, rng: np.random.Generator,
    octaves: int = 5, lacunarity: float = 2.0, gain: float = 0.5,
) -> np.ndarray:
    """Each octave is (1 - |noise|)^2, weighted by the previous octave."""
    perm = permutation(rng)
    total = np.zeros(np.broadcast(x, y).shape)
    weight = np.ones_like(total)
    amp, freq, norm = 1.0, 1.0, 0.0
    for _ in range(octaves):
        ox, oy = rng.uniform(0, PERIOD, 2)
        signal = (1.0 - np.abs(perlin(x * freq + ox, y * freq + oy, perm))) ** 2
        signal *= weight
        weight = np.clip(signal * 2.0, 0.0, 1.0)  # details only on the crests
        total += amp * signal
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def warp(
    x: np.ndarray, y: np.ndarray, rng: np.random.Generator, strength: float, octaves: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates displaced by up to about strength noise units."""
    if strength == 0:
        return x, y
    return (x + strength * fbm(x, y, rng, octaves),
            y + strength * fbm(x, y, rng, octaves))


def normalize(a: np.ndarray) -> np.ndarray:
    """Linearly stretched to [0, 1]; a constant array becomes all zeros."""
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo) if hi > lo else np.zeros_like(a)


def majority_filter(mask: np.ndarray, passes: int) -> np.ndarray:
    """3x3 majority vote per pass: removes speckles and smooths outlines."""
    for _ in range(passes):
        padded = np.pad(mask.astype(np.int8), 1, mode="edge")
        rows, cols = mask.shape
        votes = sum(padded[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
                    for dr in (-1, 0, 1) for dc in (-1, 0, 1))
        mask = votes >= 5
    return mask


def distance_to(mask: np.ndarray, cell_size: float, max_distance: float) -> np.ndarray:
    """Distance [m] from each cell to the nearest True cell, capped at max_distance.

    Chamfer distance (steps of 1 and sqrt(2) cells), within a few percent of
    the euclidean one. Without any True cell everything is max_distance.
    """
    d = np.where(mask, 0.0, np.inf)
    if mask.any():
        rows, cols = mask.shape
        steps = [(dr, dc, cell_size * math.hypot(dr, dc))
                 for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc]
        for _ in range(math.ceil(max_distance / cell_size) + 1):
            padded = np.pad(d, 1, mode="constant", constant_values=np.inf)
            d = np.minimum.reduce([d] + [padded[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols] + w
                                         for dr, dc, w in steps])
    return np.minimum(d, max_distance)


def slope_deg(z: np.ndarray, cell_size: float) -> np.ndarray:
    """Steepest slope of a height field [deg], per cell."""
    if min(z.shape) < 2:
        return np.zeros(z.shape)
    gy, gx = np.gradient(z.astype(float), cell_size)
    return np.degrees(np.arctan(np.hypot(gx, gy)))
