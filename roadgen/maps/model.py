"""Raster map layers: a regular grid in world coordinates and the values on it.

A MapLayer stores one value per grid cell, row 0 at the grid's lower y
(south), column 0 at its lower x (west), so ``data[row, col]`` lines up with
``imshow(origin="lower")``. Values sit at cell centers.

Layers are written as ``.npz`` (array + JSON metadata), next to a PNG preview.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Union

import numpy as np

from roadgen.graph.model import RoadGraph

MapKind = Literal["terrain", "elevation", "population", "style"]
# Also the order maps are built in: each kind's generator may read the kinds before it.
MAP_KINDS: tuple[str, ...] = ("terrain", "elevation", "population", "style")
SCHEMA_VERSION = 1
MAX_CELLS = 16_000_000  # 4000 x 4000; guards against a cell_size typo

# Terrain classes, after Parish & Müller's land/water/vegetation map.
WATER, LAND, VEGETATION = 0, 1, 2
TERRAIN_CLASSES: dict[int, str] = {WATER: "water", LAND: "land", VEGETATION: "vegetation"}
# Style classes (stage C3): the street pattern of each district, in the order of
# roadgen.generators.patterns.PATTERNS.
STYLE_GRID, STYLE_RADIAL, STYLE_ORGANIC, STYLE_CONTOUR, STYLE_SUBURB = range(5)
STYLE_CLASSES: dict[int, str] = {STYLE_GRID: "grid", STYLE_RADIAL: "radial", STYLE_ORGANIC: "organic",
                                 STYLE_CONTOUR: "contour", STYLE_SUBURB: "suburb"}
CLASSES: dict[str, dict[int, str]] = {"terrain": TERRAIN_CLASSES, "style": STYLE_CLASSES}

UNITS: dict[str, str] = {
    "terrain": "class",
    "elevation": "m",
    "population": "people/km^2",
    "style": "class",
}

PathLike = Union[str, Path]
Bounds = tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass(frozen=True)
class GridSpec:
    """Axis-aligned grid: lower-left corner, size [m] and square cell size [m]."""

    origin: tuple[float, float]
    size: tuple[float, float]
    cell_size: float

    def __post_init__(self) -> None:
        if self.cell_size <= 0 or min(self.size) <= 0:
            raise ValueError("grid size and cell_size must be positive")
        rows, cols = self.shape
        if rows * cols > MAX_CELLS:
            raise ValueError(
                f"grid of {cols} x {rows} cells exceeds {MAX_CELLS}; increase cell_size"
            )

    @property
    def shape(self) -> tuple[int, int]:
        """(rows, cols): cells along y, cells along x."""
        return (max(1, math.ceil(self.size[1] / self.cell_size - 1e-9)),
                max(1, math.ceil(self.size[0] / self.cell_size - 1e-9)))

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(x0, x1, y0, y1) of the cells actually covered (size rounded up to cells)."""
        rows, cols = self.shape
        x0, y0 = self.origin
        return x0, x0 + cols * self.cell_size, y0, y0 + rows * self.cell_size

    def cell_centers(self) -> tuple[np.ndarray, np.ndarray]:
        """World x and y of every cell center, each of shape ``shape``."""
        rows, cols = self.shape
        x = self.origin[0] + (np.arange(cols) + 0.5) * self.cell_size
        y = self.origin[1] + (np.arange(rows) + 0.5) * self.cell_size
        return np.meshgrid(x, y)

    @classmethod
    def from_bounds(cls, bounds: Bounds, margin: float, cell_size: float) -> "GridSpec":
        """Grid covering bounds (x0, y0, x1, y1) plus margin, aligned to whole cells."""
        bx0, by0, bx1, by1 = bounds
        x0 = math.floor((bx0 - margin) / cell_size) * cell_size
        y0 = math.floor((by0 - margin) / cell_size) * cell_size
        x1 = math.ceil((bx1 + margin) / cell_size) * cell_size
        y1 = math.ceil((by1 + margin) / cell_size) * cell_size
        return cls((x0, y0), (max(x1 - x0, cell_size), max(y1 - y0, cell_size)), cell_size)

    @classmethod
    def around(cls, graph: RoadGraph, margin: float, cell_size: float) -> "GridSpec":
        """Grid covering the graph's nodes plus margin, aligned to whole cells."""
        if not graph.nodes:
            raise ValueError("cannot fit a map grid to an empty graph")
        xs = [n.x for n in graph.nodes.values()]
        ys = [n.y for n in graph.nodes.values()]
        return cls.from_bounds((min(xs), min(ys), max(xs), max(ys)), margin, cell_size)

    def covers(self, bounds: Bounds) -> bool:
        x0, x1, y0, y1 = self.bounds
        bx0, by0, bx1, by1 = bounds
        return x0 <= bx0 and y0 <= by0 and bx1 <= x1 and by1 <= y1


@dataclass
class MapLayer:
    """One raster map. terrain, style: uint8 class ids; elevation, population: float32."""

    kind: MapKind
    grid: GridSpec
    data: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in MAP_KINDS:
            raise ValueError(f"unknown map kind {self.kind!r}; available: {MAP_KINDS}")
        if self.data.shape != self.grid.shape:
            raise ValueError(f"data shape {self.data.shape} != grid shape {self.grid.shape}")

    @property
    def categorical(self) -> bool:
        return self.kind in CLASSES

    def sample(self, x: Any, y: Any) -> np.ndarray:
        """Values at world points: nearest cell for terrain, bilinear otherwise.

        Points outside the grid take the value of the nearest edge cell.
        """
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        rows, cols = self.grid.shape
        # Fractional index measured from the first cell center.
        fc = (x - self.grid.origin[0]) / self.grid.cell_size - 0.5
        fr = (y - self.grid.origin[1]) / self.grid.cell_size - 0.5
        if self.categorical:
            c = np.clip(np.rint(fc), 0, cols - 1).astype(int)
            r = np.clip(np.rint(fr), 0, rows - 1).astype(int)
            return self.data[r, c]
        return bilinear(self.data.astype(float), fc, fr)

    def on_grid(self, grid: GridSpec) -> np.ndarray:
        """Values at another grid's cell centers; the data itself if the grid is the same."""
        if grid == self.grid:
            return self.data
        return self.sample(*grid.cell_centers())

    def fractions(self) -> dict[str, float]:
        """Categorical maps (terrain, style) only: share of cells per class."""
        if not self.categorical:
            raise ValueError("fractions() is only defined for categorical maps (terrain, style)")
        return {name: float(np.mean(self.data == k)) for k, name in CLASSES[self.kind].items()}

    def summary(self) -> str:
        if self.categorical:
            return ", ".join(f"{k} {v:.0%}" for k, v in self.fractions().items())
        unit = UNITS[self.kind]
        return (f"min {self.data.min():.1f}, mean {self.data.mean():.1f}, "
                f"max {self.data.max():.1f} {unit}")


def bilinear(data: np.ndarray, fc: Any, fr: Any) -> np.ndarray:
    """data (rows, cols, ...) at fractional column / row indices (cell centers at
    whole numbers), clamped to the edge cells; extra axes of data are kept."""
    rows, cols = data.shape[:2]
    fc, fr = np.clip(fc, 0, cols - 1), np.clip(fr, 0, rows - 1)
    c0 = np.minimum(np.floor(fc).astype(int), max(cols - 2, 0))
    r0 = np.minimum(np.floor(fr).astype(int), max(rows - 2, 0))
    c1, r1 = np.minimum(c0 + 1, cols - 1), np.minimum(r0 + 1, rows - 1)
    tc, tr = fc - c0, fr - r0
    if data.ndim > 2:  # broadcast the weights over the extra axes
        tc, tr = (np.expand_dims(t, tuple(range(-data.ndim + 2, 0))) for t in (tc, tr))
    lo = data[r0, c0] * (1 - tc) + data[r0, c1] * tc
    hi = data[r1, c0] * (1 - tc) + data[r1, c1] * tc
    return lo * (1 - tr) + hi * tr


@dataclass(frozen=True)
class MapSet:
    """A consistent stack of input maps (handed to the next map kind or a road
    generator); any of them may be missing."""

    terrain: Optional[MapLayer] = None
    elevation: Optional[MapLayer] = None
    population: Optional[MapLayer] = None
    style: Optional[MapLayer] = None

    def __post_init__(self) -> None:
        for kind in MAP_KINDS:
            layer = getattr(self, kind)
            if layer is not None and layer.kind != kind:
                raise ValueError(f"MapSet.{kind} got a {layer.kind} map")

    def layers(self) -> dict[str, MapLayer]:
        return {k: getattr(self, k) for k in MAP_KINDS if getattr(self, k) is not None}


def save_npz(layer: MapLayer, path: PathLike) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "kind": layer.kind,
        "grid": asdict(layer.grid),
        "metadata": layer.metadata,
    }
    # np.savez appends .npz unless the name already ends with it.
    np.savez_compressed(path, data=layer.data, meta=np.array(json.dumps(meta)))


def load_npz(path: PathLike) -> MapLayer:
    with np.load(Path(path), allow_pickle=False) as f:
        meta = json.loads(str(f["meta"]))
        data = f["data"]
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {meta.get('schema_version')!r}, expected {SCHEMA_VERSION}"
        )
    g = meta["grid"]
    grid = GridSpec(tuple(g["origin"]), tuple(g["size"]), float(g["cell_size"]))
    return MapLayer(meta["kind"], grid, data, meta.get("metadata", {}))
