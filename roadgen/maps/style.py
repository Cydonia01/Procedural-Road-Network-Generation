"""Style maps (stage C3): which street pattern each district of a city follows.

Parish & Müller (2001, sec. 3.2.2) drive their pattern rules with grey-scale
control maps; here one categorical map holds a pattern class per cell
(grid, radial, organic, contour, suburb; roadgen.generators.patterns), and
lsystem's field goal blends the patterns by blurring it.

- zones: domain-warped Voronoi districts about zone_size across. On a
  population map, a district's mean density sets its class: the densest
  core is a grid (radial around the main centre), the middle grid or
  organic, the sparse edge suburb; on an elevation map, districts steeper
  than contour_grade follow the contours. Without upstream maps the classes
  are drawn by shares. Every district also gets its own grid angle (a base
  angle plus angle_spread_deg), so districts meet at an angle as in real
  cities; sites, classes and angles are in the metadata.
- image: pixels classified by nearest palette color, like the terrain image.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from roadgen.config import DEFAULTS, section
from roadgen.maps import noise
from roadgen.maps.base import MapGenerator, register_map
from roadgen.maps.image import read_raster, resample
from roadgen.maps.model import (
    STYLE_CLASSES,
    STYLE_CONTOUR,
    STYLE_GRID,
    STYLE_ORGANIC,
    STYLE_RADIAL,
    STYLE_SUBURB,
    WATER,
    GridSpec,
    MapLayer,
    MapSet,
)

# Plot colors per class.
STYLE_COLORS: dict[int, str] = {
    STYLE_GRID: "#4c78a8", STYLE_RADIAL: "#e45756", STYLE_ORGANIC: "#54a24b",
    STYLE_CONTOUR: "#b279a2", STYLE_SUBURB: "#eeca3b",
}

_DZ = section(DEFAULTS, "maps", "style", "zones")
_DI = section(DEFAULTS, "maps", "style", "image")


@dataclass(frozen=True)
class ZonesStyleParams:
    zone_size: float = _DZ["zone_size"]  # [m] typical district size
    warp: float = _DZ["warp"]  # domain warp of district borders, in units of zone_size
    # Class shares (grid, radial, organic, contour, suburb) without a population map.
    shares: tuple[float, ...] = _DZ["shares"]
    core_density: float = _DZ["core_density"]  # [people/km^2] denser districts are grid
    suburb_density: float = _DZ["suburb_density"]  # [people/km^2] sparser districts are suburb
    organic_share: float = _DZ["organic_share"]  # share of middle districts that are organic
    radial_radius: float = _DZ["radial_radius"]  # [m] districts this close to the main centre are radial
    contour_grade: float = _DZ["contour_grade"]  # districts with a steeper mean grade follow contours
    angle_spread_deg: float = _DZ["angle_spread_deg"]  # std dev of the districts' grid angles

    def __post_init__(self) -> None:
        if self.zone_size <= 0 or self.warp < 0:
            raise ValueError("need zone_size > 0 and warp >= 0")
        if len(self.shares) != len(STYLE_CLASSES) or min(self.shares) < 0 or sum(self.shares) <= 0:
            raise ValueError(f"shares needs {len(STYLE_CLASSES)} values >= 0, not all 0")
        if not 0.0 <= self.organic_share <= 1.0 or self.suburb_density > self.core_density:
            raise ValueError("need organic_share in [0, 1] and suburb_density <= core_density")
        if min(self.radial_radius, self.contour_grade, self.angle_spread_deg) < 0:
            raise ValueError("radial_radius, contour_grade and angle_spread_deg must be >= 0")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "ZonesStyleParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # One grid orientation everywhere, organic and suburban edges: a planned American city.
    "planned": {"angle_spread_deg": 0.0, "organic_share": 0.15, "radial_radius": 0.0},
    # An old European centre: a radial core, organic middle, many orientations.
    "old_town": {"radial_radius": 900.0, "organic_share": 0.7, "angle_spread_deg": 35.0},
}
ZonesStyleParams.PRESETS = PRESETS  # type: ignore[attr-defined]


@register_map("style", "zones")
class ZonesStyleGenerator(MapGenerator[ZonesStyleParams]):
    params_type = ZonesStyleParams
    description = "districts (warped Voronoi) classed by population and slope, each with its grid angle"

    def generate(self, params: ZonesStyleParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        p = params
        site_rng, warp_rng, class_rng = map(np.random.default_rng, self.seed_sequence(seed).spawn(3))
        upstream = upstream or MapSet()
        x, y = grid.cell_centers()
        sites = _sites(grid, p.zone_size, site_rng)
        if p.warp > 0:
            s = p.zone_size
            x = x + p.warp * s * noise.fbm(x / s, y / s, warp_rng, 3)
            y = y + p.warp * s * noise.fbm(x / s + 31.7, y / s, warp_rng, 3)
        zone = cKDTree(sites).query(np.stack([x.ravel(), y.ravel()], axis=1))[1].reshape(grid.shape)

        land = np.ones(grid.shape, dtype=bool)
        if upstream.terrain is not None:
            land = upstream.terrain.on_grid(grid) != WATER
        density = upstream.population.on_grid(grid) if upstream.population is not None else None
        grade = None
        if upstream.elevation is not None:
            grade = np.tan(np.radians(noise.slope_deg(upstream.elevation.on_grid(grid).astype(float),
                                                      grid.cell_size)))
        main = _main_centre(upstream.population) if density is not None else None

        draws = class_rng.random(len(sites))  # one draw per site, used or not
        base = class_rng.uniform(0.0, 90.0)
        angles = base + class_rng.normal(0.0, p.angle_spread_deg, len(sites)) if p.angle_spread_deg \
            else np.full(len(sites), base)
        shares = np.cumsum(p.shares) / sum(p.shares)
        classes = np.empty(len(sites), dtype=np.uint8)
        for k, (sx, sy) in enumerate(sites):
            cells = (zone == k) & land
            if not cells.any():
                cells = zone == k
            if density is not None:
                d = float(density[cells].mean()) if cells.any() else 0.0
                near_main = main is not None and p.radial_radius > 0 and (
                    math.dist((sx, sy), main) <= p.radial_radius
                    or zone[_cell_of(grid, main)] == k)
                if near_main:
                    cls = STYLE_RADIAL
                elif grade is not None and cells.any() and float(grade[cells].mean()) >= p.contour_grade:
                    cls = STYLE_CONTOUR
                elif d >= p.core_density:
                    cls = STYLE_GRID
                elif d >= p.suburb_density:
                    cls = STYLE_ORGANIC if draws[k] < p.organic_share else STYLE_GRID
                else:
                    cls = STYLE_SUBURB
            else:
                cls = int(np.searchsorted(shares, draws[k], side="right"))
                if grade is not None and cells.any() and float(grade[cells].mean()) >= p.contour_grade:
                    cls = STYLE_CONTOUR
            classes[k] = min(cls, len(STYLE_CLASSES) - 1)
        data = classes[zone]
        radial = [[float(sx), float(sy)] for (sx, sy), c in zip(sites, classes) if c == STYLE_RADIAL]
        if main is not None and STYLE_RADIAL in classes:
            radial = [[float(main[0]), float(main[1])]]
        return MapLayer("style", grid, data.astype(np.uint8), {
            "generator": self.name, "seed": seed, "params": asdict(p),
            "classes": {str(k): v for k, v in STYLE_CLASSES.items()},
            "sites": [[float(sx), float(sy), int(c), round(float(a), 3)]
                      for (sx, sy), c, a in zip(sites, classes, angles)],
            "radial_centers": radial,
            "upstream": list(upstream.layers()),
        })


def _sites(grid: GridSpec, size: float, rng: np.random.Generator) -> np.ndarray:
    """One district site per size x size cell over the grid, at a random spot in it."""
    x0, x1, y0, y1 = grid.bounds
    nx, ny = max(1, round((x1 - x0) / size)), max(1, round((y1 - y0) / size))
    i, j = np.meshgrid(np.arange(nx), np.arange(ny))
    u = rng.uniform(0.15, 0.85, (2, ny, nx))
    sx = x0 + (i + u[0]) * (x1 - x0) / nx
    sy = y0 + (j + u[1]) * (y1 - y0) / ny
    return np.stack([sx.ravel(), sy.ravel()], axis=1)


def _main_centre(population: MapLayer) -> tuple[float, float]:
    """The population map's main centre (its metadata), or its densest cell."""
    centers = population.metadata.get("centers")
    if centers:
        return float(centers[0][0]), float(centers[0][1])
    r, c = np.unravel_index(int(np.argmax(population.data)), population.data.shape)
    x, y = population.grid.cell_centers()
    return float(x[r, c]), float(y[r, c])


def _cell_of(grid: GridSpec, pt: tuple[float, float]) -> tuple[int, int]:
    rows, cols = grid.shape
    c = int(np.clip((pt[0] - grid.origin[0]) // grid.cell_size, 0, cols - 1))
    r = int(np.clip((pt[1] - grid.origin[1]) // grid.cell_size, 0, rows - 1))
    return r, c


@dataclass(frozen=True)
class ImageStyleParams:
    path: str  # PNG / JPEG / .npy; first row is the north edge
    # RGB in [0, 1] per class: grid, radial, organic, contour, suburb; pixels take the nearest.
    colors: tuple[tuple[float, float, float], ...] = _DI["colors"]

    def __post_init__(self) -> None:
        if len(self.colors) != len(STYLE_CLASSES):
            raise ValueError(f"colors needs {len(STYLE_CLASSES)} RGB triples")


@register_map("style", "image")
class ImageStyleGenerator(MapGenerator[ImageStyleParams]):
    params_type = ImageStyleParams
    description = "classify an image's pixels by nearest class color (grid / radial / organic / contour / suburb)"

    def generate(self, params: ImageStyleParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        img = resample(read_raster(params.path), grid.shape, "nearest")
        if img.ndim == 2:
            img = np.repeat(img[..., None], 3, axis=2)
        palette = np.array(params.colors)
        data = ((img[..., None, :3] - palette) ** 2).sum(axis=-1).argmin(axis=-1).astype(np.uint8)
        return MapLayer("style", grid, data, {
            "generator": self.name, "seed": seed, "params": asdict(params),
            "classes": {str(k): v for k, v in STYLE_CLASSES.items()},
        })
