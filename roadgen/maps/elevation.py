"""Elevation maps: height above datum [m], the "elevation map" of Parish &
Müller (Section 1.2) that steers roads along low slopes.

``fbm`` follows the fractal terrain of Musgrave et al. (1989), referenced
by Parish & Müller:

1. domain-warped fBm, optionally blended with a ridged multifractal for
   mountain crests (ridge = 0: rolling hills, 1: ridges only);
2. stretched to [0, 1] over the grid and raised to ``exponent``
   (> 1 flattens valleys and sharpens peaks, < 1 the reverse);
3. scaled to [min_elevation, max_elevation];
4. on a terrain map with water: land is lowered towards the water, from
   full height shore_width away down to shore_height above it, so lakes,
   rivers and the sea lie in valleys;
5. optional thermal erosion: wherever the slope to a neighbour exceeds the
   talus angle, part of the excess slides downhill; this rounds off the
   unnaturally steep slopes noise produces;
6. water cells are set flat to min_elevation (the water surface).

``image`` reads a grayscale heightmap (e.g. a converted SRTM tile) and uses
it as given, terrain or not.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.maps import noise
from roadgen.maps.base import MapGenerator, register_map
from roadgen.maps.image import grayscale, read_raster, resample
from roadgen.maps.model import WATER, GridSpec, MapLayer, MapSet

_D = section(DEFAULTS, "maps", "elevation", "fbm")
_DI = section(DEFAULTS, "maps", "elevation", "image")

_NEIGHBOURS = ((-1, 0), (1, 0), (0, -1), (0, 1))


@dataclass(frozen=True)
class FbmElevationParams:
    min_elevation: float = _D["min_elevation"]  # [m]
    max_elevation: float = _D["max_elevation"]  # [m]
    scale: float = _D["scale"]  # size of the largest hills [m]
    octaves: int = _D["octaves"]
    lacunarity: float = _D["lacunarity"]
    gain: float = _D["gain"]  # amplitude falloff per octave; higher = rougher
    warp: float = _D["warp"]  # domain warp strength, in units of scale
    ridge: float = _D["ridge"]  # blend of ridged multifractal, in [0, 1]
    exponent: float = _D["exponent"]  # redistribution of heights
    erosion_iterations: int = _D["erosion_iterations"]
    talus_deg: float = _D["talus_deg"]  # slopes steeper than this erode
    erosion_rate: float = _D["erosion_rate"]  # share of the excess moved per iteration
    # --- on a terrain map; ignored without one -----------------------------------------
    shore_width: float = _D["shore_width"]  # land reaches full height this far from water [m]
    shore_height: float = _D["shore_height"]  # land at the shore, above the water surface [m]

    def __post_init__(self) -> None:
        if self.max_elevation < self.min_elevation:
            raise ValueError("max_elevation must be >= min_elevation")
        if self.scale <= 0 or self.exponent <= 0:
            raise ValueError("scale and exponent must be positive")
        if self.octaves < 1 or self.lacunarity <= 1 or not 0 < self.gain < 1:
            raise ValueError("need octaves >= 1, lacunarity > 1 and gain in (0, 1)")
        if not 0.0 <= self.ridge <= 1.0 or self.warp < 0:
            raise ValueError("ridge must be in [0, 1] and warp >= 0")
        if self.erosion_iterations < 0 or not 0 < self.talus_deg < 90:
            raise ValueError("erosion_iterations must be >= 0 and talus_deg in (0, 90)")
        if not 0 < self.erosion_rate <= 0.5:
            raise ValueError("erosion_rate must be in (0, 0.5]")
        if self.shore_width < 0 or self.shore_height < 0:
            raise ValueError("shore_width and shore_height must be >= 0")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "FbmElevationParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # Nearly flat plain, a few metres of relief.
    "flat": {"max_elevation": 5.0, "octaves": 3, "erosion_iterations": 0},
    # Gentle hills, typical of a lowland city.
    "rolling": {"max_elevation": 40.0, "scale": 2000.0, "exponent": 1.2},
    # Pronounced hills and valleys (e.g. a San Francisco-like site).
    "hilly": {"max_elevation": 150.0, "scale": 1200.0, "gain": 0.55, "ridge": 0.3,
              "erosion_iterations": 30},
    # Ridges and steep valleys; most roads will have to follow the contours.
    "mountainous": {"max_elevation": 600.0, "scale": 2500.0, "gain": 0.55, "ridge": 0.8,
                    "exponent": 1.6, "erosion_iterations": 50, "talus_deg": 35.0},
}
FbmElevationParams.PRESETS = PRESETS  # type: ignore[attr-defined]


def thermal_erosion(z: np.ndarray, cell_size: float, iterations: int, talus_deg: float,
                    rate: float) -> np.ndarray:
    """Material above the talus slope slides to lower 4-neighbours; total height is kept."""
    z = z.astype(float).copy()
    talus = math.tan(math.radians(talus_deg)) * cell_size  # max height step between cells
    rows, cols = z.shape
    for _ in range(iterations):
        padded = np.pad(z, 1, mode="edge")  # border neighbours equal the cell: no flow out
        moves = []
        for dr, dc in _NEIGHBOURS:
            neighbour = padded[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
            moves.append(rate / len(_NEIGHBOURS) * np.maximum(z - neighbour - talus, 0.0))
        dz = -sum(moves)
        for (dr, dc), m in zip(_NEIGHBOURS, moves):
            # Cell (r, c) gives m[r, c] to (r + dr, c + dc).
            dz[max(dr, 0):rows + min(dr, 0), max(dc, 0):cols + min(dc, 0)] += \
                m[max(-dr, 0):rows + min(-dr, 0), max(-dc, 0):cols + min(-dc, 0)]
        z += dz
    return z


@register_map("elevation", "fbm")
class FbmElevationGenerator(MapGenerator[FbmElevationParams]):
    params_type = FbmElevationParams
    description = "fractal terrain: warped fBm + ridged multifractal, optional thermal erosion"

    def generate(self, params: FbmElevationParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        p = params
        rng = self.rng(seed)
        x, y = grid.cell_centers()
        wx, wy = noise.warp(x / p.scale, y / p.scale, rng, p.warp)
        h = noise.normalize(noise.fbm(wx, wy, rng, p.octaves, p.lacunarity, p.gain))
        if p.ridge > 0:
            r = noise.normalize(noise.ridged(wx, wy, rng, p.octaves, p.lacunarity, p.gain))
            h = (1 - p.ridge) * h + p.ridge * r
        h = noise.normalize(h) ** p.exponent
        water = _water(upstream, grid)
        if water is None:
            z = p.min_elevation + h * (p.max_elevation - p.min_elevation)
        else:
            z = self._on_shores(p, grid, h, water)
        if p.erosion_iterations:
            z = thermal_erosion(z, grid.cell_size, p.erosion_iterations, p.talus_deg,
                                p.erosion_rate)
        meta: dict[str, Any] = {"generator": self.name, "seed": seed, "params": asdict(p),
                                "unit": "m"}
        if water is not None:
            z = np.where(water, p.min_elevation, np.maximum(z, p.min_elevation + p.shore_height))
            meta["upstream"] = ["terrain"]
        return MapLayer("elevation", grid, z.astype(np.float32), meta)

    @staticmethod
    def _on_shores(p: FbmElevationParams, grid: GridSpec, h: np.ndarray,
                   water: np.ndarray) -> np.ndarray:
        """Heights with the land sloping down to shore_height at the water's edge."""
        low = p.min_elevation + min(p.shore_height, p.max_elevation - p.min_elevation)
        if p.shore_width > 0:
            d = noise.distance_to(water, grid.cell_size, p.shore_width) / p.shore_width
            h = h * d * d * (3 - 2 * d)  # smoothstep: flat at the shore and inland
        return low + h * (p.max_elevation - low)


def _water(upstream: Optional[MapSet], grid: GridSpec) -> Optional[np.ndarray]:
    """Water mask of the upstream terrain on grid; None without terrain or water."""
    if upstream is None or upstream.terrain is None:
        return None
    water = upstream.terrain.on_grid(grid) == WATER
    return water if water.any() else None


@dataclass(frozen=True)
class ImageElevationParams:
    path: str  # grayscale PNG / JPEG / .npy; first row is the north edge
    min_elevation: float = _DI["min_elevation"]  # [m] at black (or the raster's minimum)
    max_elevation: float = _DI["max_elevation"]  # [m] at white (or the raster's maximum)
    stretch: bool = _DI["stretch"]  # map the raster's own min..max (not 0..1) to the range


@register_map("elevation", "image")
class ImageElevationGenerator(MapGenerator[ImageElevationParams]):
    params_type = ImageElevationParams
    description = "grayscale heightmap image or .npy, scaled to [min_elevation, max_elevation]"

    def generate(self, params: ImageElevationParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        h = resample(grayscale(read_raster(params.path)), grid.shape, "bilinear")
        if params.stretch:
            h = noise.normalize(h)
        z = params.min_elevation + h * (params.max_elevation - params.min_elevation)
        return MapLayer("elevation", grid, z.astype(np.float32), {
            "generator": self.name, "seed": seed, "params": asdict(params), "unit": "m",
        })
