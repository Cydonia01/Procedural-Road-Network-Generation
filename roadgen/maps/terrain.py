"""Terrain maps: water / land / vegetation, as in Parish & Müller's
"land/water/vegetation map" (Section 1.2) and Chen et al.'s water and
park/forest maps.

``noise`` builds the map from three independent features:

1. lakes or sea: a domain-warped fBm field, optionally tilted by a linear
   ramp towards one side (a coast), thresholded at its water_fraction
   quantile so the share of water is exact before smoothing;
2. rivers: bands of water crossing the map, their centre line a sine
   meander made irregular by 1D fBm;
3. vegetation (parks, forest): the highest vegetation_fraction of another
   fBm field, on land only.

Terrain is the first map of the pipeline, so it reads no other map.

``image`` classifies each pixel of a color picture by its nearest class
color, or thresholds a grayscale picture into water and land.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.maps import noise
from roadgen.maps.base import MapGenerator, register_map
from roadgen.maps.image import read_raster, resample
from roadgen.maps.model import (
    LAND,
    TERRAIN_CLASSES,
    VEGETATION,
    WATER,
    GridSpec,
    MapLayer,
    MapSet,
)

log = logging.getLogger(__name__)

# Class colors for plots, and the default palette when reading images.
TERRAIN_COLORS: dict[int, tuple[float, float, float]] = {
    WATER: (0.23, 0.48, 0.78),
    LAND: (0.91, 0.87, 0.78),
    VEGETATION: (0.42, 0.62, 0.29),
}

_D = section(DEFAULTS, "maps", "terrain", "noise")
_DI = section(DEFAULTS, "maps", "terrain", "image")


@dataclass(frozen=True)
class NoiseTerrainParams:
    scale: float = _D["scale"]  # size of the largest water bodies [m]
    octaves: int = _D["octaves"]
    lacunarity: float = _D["lacunarity"]
    gain: float = _D["gain"]
    warp: float = _D["warp"]  # domain warp strength, in units of scale
    water_fraction: float = _D["water_fraction"]  # lakes / sea share of the map, before rivers
    coast_strength: float = _D["coast_strength"]  # 0 = scattered lakes, 1 = straight coast
    coast_angle_deg: Optional[float] = _D["coast_angle_deg"]  # direction of the sea; None = random
    smoothing: int = _D["smoothing"]  # 3x3 majority passes on lakes / sea
    rivers: int = _D["rivers"]
    river_width: float = _D["river_width"]  # [m]
    river_meander: float = _D["river_meander"]  # sideways amplitude of the meanders [m]
    river_wavelength: float = _D["river_wavelength"]  # length of one meander [m]
    vegetation_fraction: float = _D["vegetation_fraction"]  # share of the land
    vegetation_scale: float = _D["vegetation_scale"]  # size of parks / forests [m]

    def __post_init__(self) -> None:
        if min(self.scale, self.vegetation_scale, self.river_wavelength) <= 0:
            raise ValueError("scale, vegetation_scale and river_wavelength must be positive")
        if self.octaves < 1 or self.lacunarity <= 1 or not 0 < self.gain < 1:
            raise ValueError("need octaves >= 1, lacunarity > 1 and gain in (0, 1)")
        for name in ("water_fraction", "coast_strength", "vegetation_fraction"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.rivers < 0 or self.smoothing < 0 or self.warp < 0:
            raise ValueError("rivers, smoothing and warp must be >= 0")
        if self.rivers and self.river_width <= 0:
            raise ValueError("river_width must be positive")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "NoiseTerrainParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # Dry land: no water at all, some parks.
    "inland": {"water_fraction": 0.0, "rivers": 0, "vegetation_fraction": 0.12},
    # A few lakes scattered over the map.
    "lakes": {"water_fraction": 0.15, "coast_strength": 0.0, "rivers": 0},
    # Sea along one side, ragged coastline.
    "coastal": {"water_fraction": 0.3, "coast_strength": 0.8, "rivers": 0, "warp": 0.4},
    # One wide river through land, no lakes (e.g. a Paris- or London-like site).
    "river_city": {"water_fraction": 0.0, "rivers": 1, "river_width": 80.0,
                   "river_meander": 250.0, "vegetation_fraction": 0.1},
}
NoiseTerrainParams.PRESETS = PRESETS  # type: ignore[attr-defined]


@register_map("terrain", "noise")
class NoiseTerrainGenerator(MapGenerator[NoiseTerrainParams]):
    params_type = NoiseTerrainParams
    description = "fBm lakes / coast, meandering rivers and fBm vegetation patches"

    def generate(self, params: NoiseTerrainParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        p = params
        # One stream per feature: changing the rivers leaves lakes and parks alone.
        lake_rng, river_rng, veg_rng = map(np.random.default_rng, self.seed_sequence(seed).spawn(3))
        x, y = grid.cell_centers()

        water = np.zeros(grid.shape, dtype=bool)
        if p.water_fraction > 0:
            water = self._lakes(p, grid, x, y, lake_rng)
        for _ in range(p.rivers):
            water |= self._river(p, grid, x, y, river_rng)
        if p.rivers and p.river_width < 2 * grid.cell_size:
            log.warning("river_width %.1f m is under two cells; the river may break up",
                        p.river_width)

        data = np.where(water, WATER, LAND).astype(np.uint8)
        if p.vegetation_fraction > 0 and (~water).any():
            v = noise.fbm(x / p.vegetation_scale, y / p.vegetation_scale, veg_rng, 4)
            cut = np.quantile(v[~water], 1.0 - p.vegetation_fraction)
            data[~water & (v > cut)] = VEGETATION

        return MapLayer("terrain", grid, data, {
            "generator": self.name, "seed": seed, "params": asdict(p),
            "classes": {str(k): v for k, v in TERRAIN_CLASSES.items()},
        })

    @staticmethod
    def _lakes(p: NoiseTerrainParams, grid: GridSpec, x: np.ndarray, y: np.ndarray,
               rng: np.random.Generator) -> np.ndarray:
        wx, wy = noise.warp(x / p.scale, y / p.scale, rng, p.warp)
        field = noise.normalize(noise.fbm(wx, wy, rng, p.octaves, p.lacunarity, p.gain))
        angle = rng.uniform(0, 2 * math.pi)  # drawn always, so the stream stays aligned
        if p.coast_strength > 0:
            if p.coast_angle_deg is not None:
                angle = math.radians(p.coast_angle_deg)
            ramp = noise.normalize(x * math.cos(angle) + y * math.sin(angle))
            field = (1 - p.coast_strength) * field + p.coast_strength * (1 - ramp)
        water = field <= np.quantile(field, p.water_fraction)
        return noise.majority_filter(water, p.smoothing)

    @staticmethod
    def _river(p: NoiseTerrainParams, grid: GridSpec, x: np.ndarray, y: np.ndarray,
               rng: np.random.Generator) -> np.ndarray:
        """Band of water through a random point in the middle half, random direction."""
        x0, x1, y0, y1 = grid.bounds
        cx = x0 + (x1 - x0) * rng.uniform(0.25, 0.75)
        cy = y0 + (y1 - y0) * rng.uniform(0.25, 0.75)
        angle = rng.uniform(0, math.pi)
        c, s = math.cos(angle), math.sin(angle)
        along = (x - cx) * c + (y - cy) * s
        across = -(x - cx) * s + (y - cy) * c
        # Centre line: regular meanders, made irregular by 1D fBm of half the amplitude.
        t = along / p.river_wavelength
        wobble = noise.fbm(2 * t, np.full_like(t, rng.uniform(0, noise.PERIOD)), rng, 3)
        centre = p.river_meander * (np.sin(2 * math.pi * t + rng.uniform(0, 2 * math.pi))
                                    + 0.5 * wobble)
        return np.abs(across - centre) <= p.river_width / 2


@dataclass(frozen=True)
class ImageTerrainParams:
    path: str  # PNG / JPEG / .npy; first row is the north edge
    water_color: tuple[float, float, float] = _DI["water_color"]  # RGB in [0, 1]
    land_color: tuple[float, float, float] = _DI["land_color"]
    vegetation_color: tuple[float, float, float] = _DI["vegetation_color"]
    # Grayscale images are a water mask: water is darker (or brighter) than this.
    gray_threshold: float = _DI["gray_threshold"]
    water_is_dark: bool = _DI["water_is_dark"]


@register_map("terrain", "image")
class ImageTerrainGenerator(MapGenerator[ImageTerrainParams]):
    params_type = ImageTerrainParams
    description = "classify an image's pixels by nearest class color (water / land / vegetation)"

    def generate(self, params: ImageTerrainParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        img = resample(read_raster(params.path), grid.shape, "nearest")
        if img.ndim == 3 and np.allclose(img, img[..., :1], atol=1e-3):
            img = img[..., 0]  # gray saved as RGB
        if img.ndim == 2:
            water = img < params.gray_threshold if params.water_is_dark else img > params.gray_threshold
            data = np.where(water, WATER, LAND).astype(np.uint8)
        else:
            palette = np.array([params.water_color, params.land_color, params.vegetation_color])
            dist = ((img[..., None, :] - palette) ** 2).sum(axis=-1)
            data = np.array([WATER, LAND, VEGETATION], dtype=np.uint8)[dist.argmin(axis=-1)]
        return MapLayer("terrain", grid, data, {
            "generator": self.name, "seed": seed, "params": asdict(params),
            "classes": {str(k): v for k, v in TERRAIN_CLASSES.items()},
        })
