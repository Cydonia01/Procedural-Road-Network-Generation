"""Small lsystem grids for tests: n_x avenues (along y) by n_y streets (along x),
grown from a corner with branch_prob 1, 250 m x 80 m apart."""

from __future__ import annotations

from typing import Any

from roadgen.generators.lsystem import LSystemGenerator, LSystemParams, grid_extent
from roadgen.graph.model import RoadGraph

SPACING = (250.0, 80.0)


def grid_params(n_x: int, n_y: int, preset: str = "grid", **kw: Any) -> LSystemParams:
    block = kw.pop("block_size", SPACING)
    return LSystemParams.preset(preset, extent=grid_extent(n_x, n_y, block), block_size=block,
                                start=(0.0, 0.0), branch_prob=1.0, **kw)


def small_grid(n_x: int, n_y: int, seed: int = 0, preset: str = "grid", **kw: Any) -> RoadGraph:
    return LSystemGenerator().generate(grid_params(n_x, n_y, preset, **kw), seed)


def grid_cli(n_x: int, n_y: int) -> list[str]:
    """CLI arguments for the same grid: `generate lsystem *grid_cli(3, 3)`."""
    w, h = grid_extent(n_x, n_y, SPACING)
    return ["--preset", "grid", "--set", f"extent=[{w}, {h}]"]


# lsystem grows on input maps: no population limits, and dead ends where water or
# vegetation cut a road off are fine.
ON_MAPS = ["--set", "street_min_density=0", "--set", "highways=false", "--allow-traps"]


def node_at(g: RoadGraph, x: float, y: float) -> int:
    return next(n.id for n in g.nodes.values() if abs(n.x - x) < 1e-6 and abs(n.y - y) < 1e-6)
