"""Matplotlib previews of map layers, optionally with a RoadGraph on top."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import matplotlib
import numpy as np
from matplotlib.axes import Axes
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.colors import Colormap, LightSource, ListedColormap, Normalize
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from roadgen.graph.model import RoadGraph
from roadgen.maps.model import TERRAIN_CLASSES, UNITS, MapLayer
from roadgen.maps.terrain import TERRAIN_COLORS

# Greens to snow; the blue low end of "terrain" is left out because water
# comes from the terrain map (elevation only lies flat there), not from low elevation.
ELEVATION_CMAP = ListedColormap(matplotlib.colormaps["terrain"](np.linspace(0.25, 1.0, 256)))
POPULATION_CMAP = "YlOrRd"
CONTOURS = 10  # elevation contour lines per map


def plot_map(
    layer: MapLayer,
    path: Union[str, Path],
    graph: Optional[RoadGraph] = None,
    dpi: int = 150,
) -> None:
    """Render layer to an image file, with graph's roads drawn over it if given."""
    fig = Figure(figsize=(8, 8))
    ax = fig.add_subplot()
    x0, x1, y0, y1 = layer.grid.bounds
    extent = (x0, x1, y0, y1)

    if layer.kind == "terrain":
        classes = sorted(TERRAIN_CLASSES)
        cmap = ListedColormap([TERRAIN_COLORS[k] for k in classes])
        ax.imshow(layer.data, origin="lower", extent=extent, cmap=cmap,
                  vmin=-0.5, vmax=len(classes) - 0.5, interpolation="nearest")
        shares = layer.fractions()
        ax.legend(handles=[Patch(color=TERRAIN_COLORS[k],
                                 label=f"{TERRAIN_CLASSES[k]} {shares[TERRAIN_CLASSES[k]]:.0%}")
                           for k in classes], loc="upper right", fontsize=8)
    elif layer.kind == "elevation":
        z = layer.data.astype(float)
        lo, hi = float(z.min()), float(z.max())
        # Hill shading makes relief readable; vert_exag in map units.
        shaded = LightSource(azdeg=315, altdeg=45).shade(
            z, cmap=ELEVATION_CMAP, vmin=lo, vmax=hi if hi > lo else lo + 1, blend_mode="soft",
            vert_exag=1.0, dx=layer.grid.cell_size, dy=layer.grid.cell_size)
        ax.imshow(shaded, origin="lower", extent=extent)
        if hi > lo:
            ax.contour(z, levels=CONTOURS, origin="lower", extent=extent, colors="black",
                       linewidths=0.3, alpha=0.5)
        _colorbar(fig, ax, ELEVATION_CMAP, lo, hi, f"elevation [{UNITS['elevation']}]")
    else:
        im = ax.imshow(layer.data, origin="lower", extent=extent, cmap=POPULATION_CMAP)
        fig.colorbar(im, ax=ax, shrink=0.8, label=f"population density [{UNITS['population']}]")

    if graph is not None:
        segments = [[(graph.nodes[e.from_node].x, graph.nodes[e.from_node].y),
                     (graph.nodes[e.to_node].x, graph.nodes[e.to_node].y)]
                    for e in graph.edges.values()
                    if e.from_node in graph.nodes and e.to_node in graph.nodes]
        ax.add_collection(LineCollection(segments, colors="black", linewidths=0.8, zorder=2))

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(f"{layer.kind} ({layer.metadata.get('generator', '?')}): {layer.summary()}",
                 fontsize=9)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")


def _colorbar(fig: Figure, ax: Axes, cmap: Colormap, lo: float, hi: float, label: str) -> None:
    fig.colorbar(ScalarMappable(Normalize(lo, hi if hi > lo else lo + 1), cmap), ax=ax,
                 shrink=0.8, label=label)
