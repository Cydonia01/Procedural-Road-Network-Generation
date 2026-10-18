"""Population density maps [people/km^2], the sociostatistical input that
Parish & Müller's highways follow (they connect density peaks, Section 3.2.1).

``centers`` is a polycentric version of Clark's (1951) urban density model,
in which density falls exponentially with distance from the centre,
D(r) = D0 * exp(-r / b):

1. n_centers centres at random, at least min_center_distance apart; the
   first is the main centre (weight 1), the others get weights drawn from
   secondary_weight and a proportionally shorter decay length;
2. the density is the sum of their profiles (exponential, as in Clark, or
   gaussian for a flatter core);
3. multiplied by (1 + noise_amount * fBm) for neighbourhood-scale variation,
   plus a rural base_density everywhere;
4. on upstream maps, multiplied by how habitable each cell is: 0 on water,
   vegetation_factor in parks / forest, and falling linearly from 1 on flat
   ground to 0 at max_slope_deg. Centres are only placed where this is at
   least CENTER_MIN_HABITABILITY.

``image`` reads a grayscale density image (e.g. a GHSL or WorldPop tile)
and uses it as given.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.maps import noise
from roadgen.maps.base import MapGenerator, register_map
from roadgen.maps.image import grayscale, read_raster, resample
from roadgen.maps.model import VEGETATION, WATER, GridSpec, MapLayer, MapSet

log = logging.getLogger(__name__)

Profile = Literal["exponential", "gaussian"]
PROFILES = ("exponential", "gaussian")
PLACEMENT_TRIES = 200
CENTER_MIN_HABITABILITY = 0.5  # centres go where at least this share of people could live

_D = section(DEFAULTS, "maps", "population", "centers")
_DI = section(DEFAULTS, "maps", "population", "image")


@dataclass(frozen=True)
class CentersPopulationParams:
    n_centers: int = _D["n_centers"]
    peak_density: float = _D["peak_density"]  # main centre [people/km^2]
    decay_length: float = _D["decay_length"]  # main centre's b [m]
    profile: Profile = _D["profile"]
    secondary_weight: tuple[float, float] = _D["secondary_weight"]  # (min, max) relative to main
    min_center_distance: float = _D["min_center_distance"]  # [m]
    center_margin: float = _D["center_margin"]  # keep centres this share of the size off the edges
    noise_amount: float = _D["noise_amount"]  # relative variation, in [0, 1]
    noise_scale: float = _D["noise_scale"]  # size of the variation [m]
    base_density: float = _D["base_density"]  # rural floor [people/km^2]
    # --- on terrain / elevation maps; ignored without them ------------------------------
    vegetation_factor: float = _D["vegetation_factor"]  # density multiplier in parks / forest
    max_slope_deg: float = _D["max_slope_deg"]  # nobody lives on steeper ground; 0 = no limit

    def __post_init__(self) -> None:
        if self.n_centers < 0:
            raise ValueError("n_centers must be >= 0")
        if self.profile not in PROFILES:
            raise ValueError(f"unknown profile {self.profile!r}; available: {PROFILES}")
        if self.peak_density < 0 or self.base_density < 0:
            raise ValueError("densities must be >= 0")
        if min(self.decay_length, self.noise_scale) <= 0:
            raise ValueError("decay_length and noise_scale must be positive")
        lo, hi = self.secondary_weight
        if not 0 < lo <= hi <= 1:
            raise ValueError("secondary_weight must be (min, max) with 0 < min <= max <= 1")
        if not 0 <= self.center_margin < 0.5 or not 0 <= self.noise_amount <= 1:
            raise ValueError("center_margin must be in [0, 0.5) and noise_amount in [0, 1]")
        if self.min_center_distance < 0:
            raise ValueError("min_center_distance must be >= 0")
        if not 0 <= self.vegetation_factor <= 1 or not 0 <= self.max_slope_deg < 90:
            raise ValueError("vegetation_factor must be in [0, 1] and max_slope_deg in [0, 90)")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "CentersPopulationParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # One dense downtown, falling off steadily (a classic monocentric city).
    "monocentric": {"n_centers": 1, "peak_density": 20000.0, "decay_length": 900.0},
    # Several comparable centres (e.g. a metropolitan area that grew from towns).
    "polycentric": {"n_centers": 5, "peak_density": 12000.0, "decay_length": 500.0,
                    "secondary_weight": (0.5, 0.9), "min_center_distance": 600.0},
    # Low, uneven density with no clear centre.
    "sprawl": {"n_centers": 8, "peak_density": 3000.0, "decay_length": 400.0,
               "profile": "gaussian", "secondary_weight": (0.4, 1.0), "noise_amount": 0.6,
               "base_density": 300.0, "min_center_distance": 300.0},
}
CentersPopulationParams.PRESETS = PRESETS  # type: ignore[attr-defined]


@register_map("population", "centers")
class CentersPopulationGenerator(MapGenerator[CentersPopulationParams]):
    params_type = CentersPopulationParams
    description = "polycentric Clark density model: decaying centres, fBm variation, rural floor"

    def generate(self, params: CentersPopulationParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        p = params
        center_rng, noise_rng = map(np.random.default_rng, self.seed_sequence(seed).spawn(2))
        x, y = grid.cell_centers()
        habitable = habitability(p, grid, upstream)
        centers = self.place_centers(p, grid, center_rng, habitable)

        density = np.zeros(grid.shape)
        for cx, cy, weight in centers:
            r = np.hypot(x - cx, y - cy) / (p.decay_length * math.sqrt(weight))
            profile = np.exp(-r) if p.profile == "exponential" else np.exp(-0.5 * r * r)
            density += p.peak_density * weight * profile
        if p.noise_amount > 0:
            n = noise.fbm(x / p.noise_scale, y / p.noise_scale, noise_rng, 4)
            density *= np.clip(1.0 + p.noise_amount * n, 0.0, None)
        density += p.base_density

        meta: dict[str, Any] = {
            "generator": self.name, "seed": seed, "params": asdict(p), "unit": "people/km^2",
            "centers": [[float(cx), float(cy), float(w)] for cx, cy, w in centers],
        }
        if habitable is not None:
            density *= habitable.data
            meta["upstream"] = list(upstream.layers())  # type: ignore[union-attr]
        return MapLayer("population", grid, density.astype(np.float32), meta)

    @staticmethod
    def place_centers(p: CentersPopulationParams, grid: GridSpec, rng: np.random.Generator,
                      habitable: Optional[MapLayer] = None) -> list[tuple[float, float, float]]:
        """(x, y, weight) per centre; the main centre (weight 1) comes first."""
        x0, x1, y0, y1 = grid.bounds
        m = p.center_margin
        centers: list[tuple[float, float, float]] = []
        for k in range(p.n_centers):
            weight = 1.0 if k == 0 else float(rng.uniform(*p.secondary_weight))
            for _ in range(PLACEMENT_TRIES):
                cx = x0 + (x1 - x0) * rng.uniform(m, 1 - m)
                cy = y0 + (y1 - y0) * rng.uniform(m, 1 - m)
                if habitable is not None and habitable.sample(cx, cy) < CENTER_MIN_HABITABILITY:
                    continue
                if all(math.hypot(cx - ox, cy - oy) >= p.min_center_distance
                       for ox, oy, _ in centers):
                    centers.append((cx, cy, weight))
                    break
            else:
                log.info("population: placed only %d of %d centres %.0f m apart",
                            len(centers), p.n_centers, p.min_center_distance)
                break
        return centers


def habitability(p: CentersPopulationParams, grid: GridSpec,
                 upstream: Optional[MapSet]) -> Optional[MapLayer]:
    """Share of the density each cell keeps, in [0, 1]; None without upstream maps."""
    if upstream is None or not upstream.layers():
        return None
    h = np.ones(grid.shape)
    if upstream.terrain is not None:
        terrain = upstream.terrain.on_grid(grid)
        h[terrain == VEGETATION] = p.vegetation_factor
        h[terrain == WATER] = 0.0
    if upstream.elevation is not None and p.max_slope_deg > 0:
        slope = noise.slope_deg(upstream.elevation.on_grid(grid), grid.cell_size)
        h *= np.clip(1.0 - slope / p.max_slope_deg, 0.0, 1.0)
    # Stored as a (non-terrain) layer for its bilinear sample().
    return MapLayer("population", grid, h)


@dataclass(frozen=True)
class ImagePopulationParams:
    path: str  # grayscale PNG / JPEG / .npy; first row is the north edge
    max_density: float = _DI["max_density"]  # [people/km^2] at white
    min_density: float = _DI["min_density"]  # [people/km^2] at black


@register_map("population", "image")
class ImagePopulationGenerator(MapGenerator[ImagePopulationParams]):
    params_type = ImagePopulationParams
    description = "grayscale density image or .npy, scaled to [min_density, max_density]"

    def generate(self, params: ImagePopulationParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        v = resample(grayscale(read_raster(params.path)), grid.shape, "bilinear")
        density = params.min_density + v * (params.max_density - params.min_density)
        return MapLayer("population", grid, density.astype(np.float32), {
            "generator": self.name, "seed": seed, "params": asdict(params), "unit": "people/km^2",
        })
