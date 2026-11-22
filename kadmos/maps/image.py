"""Reading external rasters (PNG/JPEG images, .npy arrays) onto a map grid.

The raster is stretched over the whole grid. Its first row is the north
edge, as in any image; MapLayer rows start in the south, so rows are flipped.
GeoTIFFs (e.g. SRTM elevation) are not read directly; convert them first,
e.g. ``gdal_translate -of PNG -ot UInt16 -scale in.tif out.png``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Union

import numpy as np

Resample = Literal["nearest", "bilinear"]


def read_raster(path: Union[str, Path]) -> np.ndarray:
    """Float array, south row first: (rows, cols) or (rows, cols, channels)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"map image not found: {path}")
    if path.suffix.lower() == ".npy":
        data = np.load(path, allow_pickle=False).astype(float)
    else:
        from matplotlib.image import imread  # PNG natively, other formats via Pillow

        data = imread(path).astype(float)
        if data.max() > 1.0:  # 8/16-bit integer images
            data /= 255.0 if data.max() <= 255 else 65535.0
    if data.ndim == 3 and data.shape[2] == 4:
        data = data[..., :3]  # drop alpha
    if data.ndim not in (2, 3):
        raise ValueError(f"{path}: expected a 2D raster, got shape {data.shape}")
    return np.flipud(data)


def grayscale(data: np.ndarray) -> np.ndarray:
    """Luminance of an RGB raster; 2D rasters pass through."""
    if data.ndim == 2:
        return data
    return data[..., :3] @ np.array([0.299, 0.587, 0.114])


def resample(data: np.ndarray, shape: tuple[int, int], method: Resample) -> np.ndarray:
    """Raster stretched to shape (rows, cols); extra channels are kept."""
    rows, cols = shape
    src_r, src_c = data.shape[:2]
    # Source coordinates of the target cell centers.
    fr = np.clip((np.arange(rows) + 0.5) * src_r / rows - 0.5, 0, src_r - 1)
    fc = np.clip((np.arange(cols) + 0.5) * src_c / cols - 0.5, 0, src_c - 1)
    if method == "nearest":
        return data[np.rint(fr).astype(int)[:, None], np.rint(fc).astype(int)[None, :]]
    r0, c0 = np.floor(fr).astype(int), np.floor(fc).astype(int)
    r1, c1 = np.minimum(r0 + 1, src_r - 1), np.minimum(c0 + 1, src_c - 1)
    tr, tc = fr - r0, fc - c0
    if data.ndim == 3:
        tr, tc = tr[:, None, None], tc[None, :, None]
    else:
        tr, tc = tr[:, None], tc[None, :]
    lo = data[r0][:, c0] * (1 - tc) + data[r0][:, c1] * tc
    hi = data[r1][:, c0] * (1 - tc) + data[r1][:, c1] * tc
    return lo * (1 - tr) + hi * tr
