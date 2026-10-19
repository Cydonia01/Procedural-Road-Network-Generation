"""Common interface and registry for map generators.

Parish & Müller (2001) drive road growth with input maps; this package makes
them procedurally (or loads them from images). There are three kinds, each
with its own set of generators, built as a linear pipeline:

    terrain -> elevation (on the terrain) -> population (on both)

- terrain: water / land / vegetation classes,
- elevation: height above datum [m]; water lies flat at the bottom,
- population: density [people/km^2]; nobody on water, fewer on steep slopes.

A generator receives the maps of the earlier kinds as ``upstream`` (any of
them may be missing) and must give exactly its standalone result when they
are. It never sees a RoadGraph, so any generator of one kind can still be
combined with any generator of another.
"""

from __future__ import annotations

import zlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, ClassVar, Generic, Optional, TypeVar, Union

import numpy as np

from roadgen.generators.base import load_params
from roadgen.maps.model import MAP_KINDS, GridSpec, MapKind, MapLayer, MapSet

P = TypeVar("P")
M = TypeVar("M", bound="type[MapGenerator]")


class MapGenerator(ABC, Generic[P]):
    """Turns a params dataclass, a grid, a seed and the upstream maps into a MapLayer.

    Implementations must be deterministic for (params, grid, seed, upstream)
    and may only read upstream layers of kinds before their own. Params
    classes may offer presets like the road generators (``PRESETS`` and a
    ``preset(name, **overrides)`` classmethod).
    """

    kind: ClassVar[MapKind]
    name: ClassVar[str]
    params_type: ClassVar[type]
    description: ClassVar[str] = ""

    @abstractmethod
    def generate(self, params: P, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer: ...

    def seed_sequence(self, seed: int) -> np.random.SeedSequence:
        """Seed salted with (kind, name), so equal seeds give unrelated maps of different kinds."""
        return np.random.SeedSequence([seed, zlib.crc32(f"{self.kind}/{self.name}".encode())])

    def rng(self, seed: int) -> np.random.Generator:
        return np.random.default_rng(self.seed_sequence(seed))


# --- registry --------------------------------------------------------------------------

_REGISTRY: dict[str, dict[str, type[MapGenerator]]] = {k: {} for k in MAP_KINDS}


def register_map(kind: MapKind, name: str) -> Callable[[M], M]:
    """Class decorator: make a map generator available as (kind, name)."""
    if kind not in _REGISTRY:
        raise ValueError(f"unknown map kind {kind!r}; available: {MAP_KINDS}")

    def wrap(cls: M) -> M:
        if name in _REGISTRY[kind] and _REGISTRY[kind][name] is not cls:
            raise ValueError(f"{kind} map generator {name!r} is already registered")
        cls.kind, cls.name = kind, name
        _REGISTRY[kind][name] = cls
        return cls
    return wrap


def _load_builtin() -> None:
    # Importing the package registers the built-in map generators.
    pass


def map_generator_names(kind: str) -> list[str]:
    _load_builtin()
    if kind not in _REGISTRY:
        raise ValueError(f"unknown map kind {kind!r}; available: {MAP_KINDS}")
    return sorted(_REGISTRY[kind])


def get_map_generator(kind: str, name: str) -> MapGenerator:
    names = map_generator_names(kind)
    if name not in names:
        raise ValueError(f"unknown {kind} map generator {name!r}; available: {names}")
    return _REGISTRY[kind][name]()


def make_map(
    kind: str,
    name: str,
    grid: GridSpec,
    seed: int,
    path: Optional[Union[str, Path]] = None,
    preset: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
    defaults: Optional[dict[str, Any]] = None,
    upstream: Optional[MapSet] = None,
) -> MapLayer:
    """Params (same layering as the road generators' load_params), then generate
    on the upstream maps; layers of this kind or later ones are dropped from it."""
    gen = get_map_generator(kind, name)
    params = load_params(gen.params_type, path, preset, overrides, defaults)
    if upstream is not None:
        earlier = MAP_KINDS[:MAP_KINDS.index(kind)]
        upstream = MapSet(**{k: v for k, v in upstream.layers().items() if k in earlier})
    return gen.generate(params, grid, seed, upstream)
