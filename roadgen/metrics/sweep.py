"""Build-failure sweep: how many generated networks the OpenDRIVE builder rejects.

For each seed, the input maps are generated as `pipeline --maps` does (the
generators in maps.pipeline, each on the ones before it, with the same seed),
a network is grown on them, and the builder is run on it. Networks are not
draped: heights do not change whether a network can be built.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import get_generator, load_params
from roadgen.maps.base import make_map
from roadgen.maps.model import MAP_KINDS, GridSpec, MapSet
from roadgen.opendrive.builder import OpenDriveBuilder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    seed: int
    nodes: int
    edges: int
    error: Optional[str] = None  # builder error; None = built

    @property
    def ok(self) -> bool:
        return self.error is None


def build_one(generator: str, preset: Optional[str], seed: int, cfg: dict = DEFAULTS,
              junction_margin: Optional[float] = None) -> SweepResult:
    """Maps, network and OpenDRIVE build for one seed."""
    gen = get_generator(generator)
    params = load_params(gen.params_type, preset=preset,
                         defaults=section(cfg, "generators", generator))
    maps = None
    if gen.uses_maps:
        grid_cfg = cfg["maps"]["grid"]
        grid = GridSpec.from_bounds(gen.map_bounds(params), grid_cfg["margin"], grid_cfg["cell_size"])
        layers: dict[str, Any] = {}
        for kind in MAP_KINDS:
            name = cfg["maps"]["pipeline"][kind]
            layers[kind] = make_map(kind, name, grid, seed, defaults=section(cfg, "maps", kind, name),
                                    upstream=MapSet(**layers))
        maps = MapSet(**layers)
    graph = gen.generate(params, seed, maps=maps) if maps else gen.generate(params, seed)
    margin = cfg["opendrive"]["junction_margin"] if junction_margin is None else junction_margin
    try:
        OpenDriveBuilder(junction_margin=margin).build(graph)
        error = None
    except ValueError as e:
        error = str(e)
    return SweepResult(seed, len(graph.nodes), len(graph.edges), error)


def build_sweep(generator: str, preset: Optional[str], seeds: Sequence[int],
                cfg: dict = DEFAULTS) -> list[SweepResult]:
    results = []
    for seed in seeds:
        r = build_one(generator, preset, seed, cfg)
        (log.info if r.ok else log.warning)(
            "seed %d: %d nodes, %d edges, %s", seed, r.nodes, r.edges, "built" if r.ok else r.error)
        results.append(r)
    return results
