"""Networks shared between tests: each grown once per test session.

Growing a preset on input maps takes seconds, and several test files need the same
ones (the identity fingerprints, the city build, the style metrics). The cached
results are deep-copied on every call, so a test may change what it gets.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, Optional

import pytest

from roadgen.config import DEFAULTS, section
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.model import RoadGraph
from roadgen.maps.base import make_map
from roadgen.maps.model import MAP_KINDS, GridSpec, MapSet


def lsystem_params(preset: Optional[str] = None, **overrides: Any) -> LSystemParams:
    return LSystemParams.preset(preset, **overrides) if preset else LSystemParams(**overrides)


def pipeline_maps(params: LSystemParams, seed: int) -> MapSet:
    """The input maps `pipeline --maps` builds for lsystem (cli.generate_graph): the
    default generator of each kind the params read, on the default grid around their area,
    each on the maps before it."""
    gen = LSystemGenerator()
    grid = GridSpec.from_bounds(gen.map_bounds(params), DEFAULTS["maps"]["grid"]["margin"],
                                DEFAULTS["maps"]["grid"]["cell_size"])
    layers: dict = {}
    for kind in MAP_KINDS:
        if kind not in gen.map_kinds(params):
            continue
        name = DEFAULTS["maps"]["pipeline"][kind]
        layers[kind] = make_map(kind, name, grid, seed, defaults=section(DEFAULTS, "maps", kind, name),
                                upstream=MapSet(**layers))
    return MapSet(**layers)


@lru_cache(maxsize=None)
def _grown(preset: Optional[str], seed: int, maps: bool, overrides: tuple) -> tuple[RoadGraph, Optional[MapSet]]:
    params = lsystem_params(preset, **dict(overrides))
    mapset = pipeline_maps(params, seed) if maps else None
    graph = LSystemGenerator().generate(params, seed, mapset) if maps else LSystemGenerator().generate(params, seed)
    return graph, mapset


def grown(preset: Optional[str], seed: int, maps: bool = False, **overrides: Any) -> RoadGraph:
    """The network lsystem grows for a preset (None: the defaults), on the pipeline's maps
    or without; grown once per session, a copy each call."""
    return copy.deepcopy(_grown(preset, seed, maps, tuple(sorted(overrides.items())))[0])


def grown_maps(preset: Optional[str], seed: int, **overrides: Any) -> MapSet:
    """The maps the network of grown(preset, seed, maps=True, ...) grew on."""
    return copy.deepcopy(_grown(preset, seed, True, tuple(sorted(overrides.items())))[1])


@lru_cache(maxsize=None)
def rm_available() -> bool:
    """esmini's RoadManager library can be loaded (the drive-check needs it)."""
    try:
        from roadgen.sim.drive_check import _load_rm

        _load_rm()
        return True
    except (OSError, FileNotFoundError, RuntimeError):
        return False


# Tests that drive a network through esmini's RoadManager: skipped without it.
needs_rm = pytest.mark.skipif(not rm_available(), reason="esmini RoadManager library not found")
