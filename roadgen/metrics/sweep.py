"""Build-failure sweep: how many generated networks the OpenDRIVE builder rejects.

For each seed, the input maps are generated as `pipeline --maps` does (the
generators in maps.pipeline, each on the ones before it, with the same seed),
a network is grown on them, and the builder is run on it. Networks are not
draped: heights do not change whether a network can be built.

With HD maps (hd given and enabled) each network goes through what `pipeline --hd`
does before the build: `annotate` from the population (and style) map, then the
cross-section fit (repair.fit_cross_sections); the build then has side lanes,
marks, control and lights, seeded with the network's seed. jobs > 1 builds seeds in
that many processes; results stay in seed order.
"""

from __future__ import annotations

import dataclasses
import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import get_generator, load_params
from roadgen.generators.repair import fit_cross_sections
from roadgen.geometry.design import DesignParams
from roadgen.maps.annotate import AnnotateParams, annotate
from roadgen.maps.base import make_map
from roadgen.maps.model import MAP_KINDS, GridSpec, MapSet
from roadgen.metrics.network import network_metrics
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    seed: int
    nodes: int
    edges: int
    error: Optional[str] = None  # builder error; None = built
    network: Optional[dict[str, Any]] = None  # headline network metrics of the graph

    @property
    def ok(self) -> bool:
        return self.error is None


def build_one(generator: str, preset: Optional[str], seed: int, cfg: dict = DEFAULTS,
              junction_margin: Optional[float] = None, hd: Optional[HdMapParams] = None) -> SweepResult:
    """Maps, network and OpenDRIVE build for one seed (with HD maps if hd is enabled)."""
    gen = get_generator(generator)
    params = load_params(gen.params_type, preset=preset,
                         defaults=section(cfg, "generators", generator))
    maps = None
    if gen.uses_maps:
        grid_cfg = cfg["maps"]["grid"]
        grid = GridSpec.from_bounds(gen.map_bounds(params), grid_cfg["margin"], grid_cfg["cell_size"])
        layers: dict[str, Any] = {}
        kinds = gen.map_kinds(params) or MAP_KINDS
        for kind in (k for k in MAP_KINDS if k in kinds):
            name = cfg["maps"]["pipeline"][kind]
            layers[kind] = make_map(kind, name, grid, seed, defaults=section(cfg, "maps", kind, name),
                                    upstream=MapSet(**layers))
        maps = MapSet(**layers)
    graph = gen.generate(params, seed, maps=maps) if maps else gen.generate(params, seed)
    margin = cfg["opendrive"]["junction_margin"] if junction_margin is None else junction_margin
    try:
        if hd is not None and hd.enabled:
            hd = dataclasses.replace(hd, seed=seed)
            if maps is not None and maps.population is not None:
                control = section(cfg, "junction_control")
                graph = annotate(graph, maps.population, maps.style, AnnotateParams(**section(cfg, "annotate")),
                                 control["signal_density"], control["stop_density"])
            fit_cross_sections(graph, DesignParams(**section(cfg, "design")), margin, hd.active_cross_section)
            OpenDriveBuilder(junction_margin=margin, hd=hd).build(graph)
        else:
            OpenDriveBuilder(junction_margin=margin, hd=HdMapParams(enabled=False)).build(graph)
        error = None
    except ValueError as e:
        error = str(e)
    m = network_metrics(graph)
    headline = {k: m[k] for k in ("cnr", "skewed_share", "offset_t_pairs", "orientation_entropy",
                                  "orientation_order", "legs", "features", "naturalness")}
    return SweepResult(seed, len(graph.nodes), len(graph.edges), error, headline)


def build_sweep(generator: str, preset: Optional[str], seeds: Sequence[int],
                cfg: dict = DEFAULTS, hd: Optional[HdMapParams] = None, jobs: int = 1) -> list[SweepResult]:
    args = [(generator, preset, seed, cfg, None, hd) for seed in seeds]
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as pool:
            results = list(pool.map(_build_one, args))
    else:
        results = [_build_one(a) for a in args]
    for r in results:
        (log.info if r.ok else log.warning)(
            "seed %d: %d nodes, %d edges, %s", r.seed, r.nodes, r.edges, "built" if r.ok else r.error)
    return results


def _build_one(args: tuple) -> SweepResult:
    return build_one(*args)
