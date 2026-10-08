"""Common interface and registry for procedural road network generators."""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, ClassVar, Generic, Optional, TypeVar, Union

import yaml

from roadgen.graph.model import NodeKind, RoadGraph

P = TypeVar("P")
G = TypeVar("G", bound="type[RoadNetworkGenerator]")


class RoadNetworkGenerator(ABC, Generic[P]):
    """A generator turns a params dataclass and a seed into a RoadGraph.

    Implementations must be deterministic: the same params and seed always
    produce an identical graph. They never touch OpenDRIVE. A params class
    may offer named presets via a ``preset(name, **overrides)`` classmethod
    and a ``PRESETS`` mapping.
    """

    name: ClassVar[str]
    params_type: ClassVar[type]
    description: ClassVar[str] = ""

    @abstractmethod
    def generate(self, params: P, seed: int) -> RoadGraph: ...


# --- registry --------------------------------------------------------------------------

_REGISTRY: dict[str, type[RoadNetworkGenerator]] = {}


def register(name: str) -> Callable[[G], G]:
    """Class decorator: make a generator available by name (CLI, configs)."""
    def wrap(cls: G) -> G:
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"generator {name!r} is already registered")
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return wrap


def _load_builtin() -> None:
    # Importing the package registers the built-in generators.
    import roadgen.generators  # noqa: F401


def generator_names() -> list[str]:
    _load_builtin()
    return sorted(_REGISTRY)


def get_generator(name: str) -> RoadNetworkGenerator:
    _load_builtin()
    if name not in _REGISTRY:
        raise ValueError(f"unknown generator {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def presets_of(params_type: type) -> list[str]:
    return sorted(getattr(params_type, "PRESETS", {}) or {})


def build_params(
    params_type: type,
    values: Optional[dict[str, Any]] = None,
    preset: Optional[str] = None,
) -> Any:
    """Params from a preset (optional) overridden by values; lists become tuples."""
    values = coerce_tuples(params_type, dict(values or {}))
    known = {f.name for f in dataclasses.fields(params_type)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"unknown {params_type.__name__} fields: {sorted(unknown)}")
    if preset:
        if not hasattr(params_type, "preset"):
            raise ValueError(f"{params_type.__name__} has no presets")
        return params_type.preset(preset, **values)
    return params_type(**values)


def load_params(
    params_type: type,
    path: Optional[Union[str, Path]] = None,
    preset: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> Any:
    """Params from an optional YAML file, preset and overrides (in that order).

    A YAML file may hold the fields directly, or ``preset: <name>`` plus a
    ``params:`` mapping. An explicit preset argument wins over the file's.
    """
    values: dict[str, Any] = {}
    if path:
        data = yaml.safe_load(Path(path).read_text()) or {}
        if "params" in data or "preset" in data:
            preset = preset or data.get("preset")
            values.update(data.get("params") or {})
        else:
            values.update(data)
    values.update(overrides or {})
    return build_params(params_type, values, preset)


def coerce_tuples(params_type: type, values: dict[str, Any]) -> dict[str, Any]:
    """YAML/JSON give lists where the dataclass expects (nested) tuples."""
    for f in dataclasses.fields(params_type):
        v = values.get(f.name)
        if isinstance(v, list) and "tuple" in str(f.type):
            values[f.name] = tuple(tuple(x) if isinstance(x, list) else x for x in v)
    return values


# --- helpers for generators ---------------------------------------------------------------


def kind_from_degree(degree: int) -> NodeKind:
    if degree == 1:
        return "dead_end"
    if degree >= 3:
        return "junction"
    return "auto"


def assign_node_kinds(graph: RoadGraph) -> None:
    degrees = {n: 0 for n in graph.nodes}
    for e in graph.edges.values():
        degrees[e.from_node] += 1
        degrees[e.to_node] += 1
    for node_id, node in graph.nodes.items():
        node.kind = kind_from_degree(degrees[node_id])
