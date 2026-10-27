"""Command-line entry point: `python -m cli <command> ...`."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import re
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence, Union

import yaml

from roadgen.config import DEFAULTS, load_config, section, with_overrides
from roadgen.generators.base import (
    build_params,
    generator_names,
    get_generator,
    presets_of,
)
from roadgen.geometry.design import DesignParams
from roadgen.graph.io import load_json, save_json
from roadgen.graph.model import RoadGraph
from roadgen.graph.plot import plot_graph
from roadgen.history import RunStore, diff, load_spec, plain, run_hash
from roadgen.maps.base import get_map_generator, make_map, map_generator_names
from roadgen.maps.model import MAP_KINDS, GridSpec, MapLayer, MapSet, load_npz, save_npz
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.sim.esmini import esmini_binary
from roadgen.validate.clearance import check_clearance
from roadgen.validate.connectivity import check_traffic_connectivity
from roadgen.validate.continuity import check_continuity, check_curvature
from roadgen.validate.dynamics import check_dynamics
from roadgen.validate.schema import validate_schema
from roadgen.validate.topology import check_topology

log = logging.getLogger("roadgen.cli")

MAX_LISTED_WARNINGS = 5
DYNAMICS_CHECKS_JUNCTIONS = True  # junction turns get speeds from their curvature
DECK = 1.0  # [m] of grade_separation.clearance taken by the upper road's deck


def cmd_plot_graph(args: argparse.Namespace) -> int:
    graph = load_json(args.input)
    for err in graph.validate():
        log.warning("validation: %s", err)
    output = artifact(args, args.output, Path(args.input).with_suffix(".png"))
    plot_graph(graph, output, show_node_ids=args.node_ids)
    log.info("wrote %s", output)
    return 0


@contextmanager
def timed(label: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    log.info("%s: %.2f s", label, time.perf_counter() - start)


def check_traffic(graph: RoadGraph, allow_traps: bool) -> list[str]:
    """Traffic connectivity problems: errors, or only warnings with allow_traps."""
    with timed("traffic connectivity"):
        problems = check_traffic_connectivity(graph)
    for msg in problems:
        (log.warning if allow_traps else log.error)("traffic: %s", msg)
    if not problems:
        log.info("traffic: strongly connected, no traps")
    return [] if allow_traps else [f"traffic: {m}" for m in problems]


def junction_params(cfg: dict):
    """JunctionParams from the config's opendrive section (junction_margin is separate)."""
    from roadgen.opendrive.junctions import JunctionParams

    return JunctionParams(**{k: v for k, v in section(cfg, "opendrive").items() if k != "junction_margin"})


def build_and_validate(
    graph: RoadGraph, xodr_path: Path, junction_margin: float, xsd: Optional[str],
    cfg: Optional[dict] = None,
) -> list[str]:
    """Build and write the .xodr, then run every file validator; return all errors."""
    builder = OpenDriveBuilder(
        name=graph.metadata.get("name", "roadgen"), junction_margin=junction_margin,
        design=DesignParams(**section(cfg or DEFAULTS, "design")),
        junctions=junction_params(cfg or DEFAULTS),
    )
    with timed("build xodr"):
        try:
            builder.build(graph)
        except ValueError as e:  # e.g. an edge too short for the junctions at its ends
            log.error("build: %s", e)
            return [f"build: {e}"]
    with timed("write xodr"):
        sidecar = builder.write(xodr_path)
    log.info(
        "wrote %s (%d roads, %d junctions, %d connecting roads) and %s",
        xodr_path,
        len(set(builder.edge_to_road.values())),
        len(builder.node_to_junction),
        len(builder.movements),
        sidecar,
    )
    with timed("validate"):
        errors = check_topology(xodr_path)
        errors += check_continuity(xodr_path)
        curvature_errors, curvature_warnings = check_curvature(xodr_path)
        errors += curvature_errors
        a_lat_max = section(cfg or DEFAULTS, "design")["a_lat_max"]
        errors += check_dynamics(xodr_path, a_lat_max, connecting=DYNAMICS_CHECKS_JUNCTIONS)
        errors += check_clearance(xodr_path, section(cfg or DEFAULTS, "grade_separation")["clearance"] - DECK)
        errors += validate_schema(xodr_path, xsd) or []
    for warning in curvature_warnings[:MAX_LISTED_WARNINGS]:
        log.warning("%s", warning)
    if len(curvature_warnings) > MAX_LISTED_WARNINGS:
        log.warning("... %d curvature warnings in all (paramPoly3 junction turns)", len(curvature_warnings))
    for err in errors:
        log.error("%s", err)
    return errors


def cmd_build_xodr(args: argparse.Namespace) -> int:
    graph = load_json(args.input)
    check_traffic(graph, allow_traps=True)  # informational for hand-made graphs
    output = artifact(args, args.output, Path(args.input).with_suffix(".xodr"))
    errors = build_and_validate(graph, output, args.junction_margin, args.xsd, args.cfg)
    return 1 if errors else 0


def parse_set(items: Optional[Sequence[str]]) -> dict:
    """--set key=value pairs; values are parsed as YAML (numbers, lists, ...)."""
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"error: --set expects key=value, got {item!r}")
        out[key.strip()] = yaml.safe_load(value)
    return out


def spec_params(args: argparse.Namespace, *keys: str) -> tuple[dict, Optional[str]]:
    """(values, preset) a run's config sets in a params section, e.g. generators.lsystem."""
    sec = getattr(args, "spec", {}).get("config", {})
    for key in keys:
        sec = sec.get(key) or {}
    return {k: v for k, v in sec.items() if k != "preset"}, sec.get("preset")


def generator_params(args: argparse.Namespace):
    """Params: defaults, then the preset, then the values the run's config sets."""
    gen = get_generator(args.generator)
    values, preset = spec_params(args, "generators", args.generator)
    try:
        defaults = section(args.cfg, "generators", args.generator)
        return build_params(gen.params_type, values, preset, defaults)
    except TypeError as e:  # missing required fields
        raise SystemExit(f"error: {e}") from e
    except ValueError as e:
        raise SystemExit(f"error: {e}") from e


def artifact(args: argparse.Namespace, name: Optional[str], default: Union[str, Path]) -> Path:
    """Where a command writes a file. In a recorded run: <run folder>/<name>, where
    -o only names the file. Otherwise -o is a path; the default is default."""
    run_dir = getattr(args, "run_dir", None)
    if run_dir is None:
        return Path(name) if name else Path(default)
    if name and (Path(name).name != name or name in (".", "..")):
        raise SystemExit(f"error: {name!r}: outputs of a recorded run go into its folder; "
                         f"give a file name, not a path (or use --no-history)")
    return Path(run_dir) / (name or Path(default).name)


def default_output(args: argparse.Namespace) -> Path:
    preset = spec_params(args, "generators", args.generator)[1]
    return Path("out") / ("_".join(filter(None, [args.generator, preset])) + f"_s{args.seed}")


def generate_graph(args: argparse.Namespace) -> tuple[RoadGraph, dict[str, MapLayer]]:
    """The network, and the input maps requested by the map flags.

    A generator that reads maps (uses_maps) gets them, built on a grid around
    the area it reports. For any other generator the maps are only built
    afterwards, around the finished network, for reference.
    """
    gen = get_generator(args.generator)
    params = generator_params(args)
    chosen, files = map_requests(args, gen.map_kinds(params))
    layers: dict[str, MapLayer] = {}
    maps = None
    margin, cell = args.cfg["maps"]["grid"]["margin"], args.cfg["maps"]["grid"]["cell_size"]
    if (chosen or files) and gen.uses_maps:
        layers = input_maps(args, GridSpec.from_bounds(gen.map_bounds(params), margin, cell),
                            chosen, files)
        maps = MapSet(**layers)
    elif chosen or files:
        log.info("%s does not read input maps; building them around the network for reference",
                 args.generator)
    with timed(f"generate {args.generator}"):
        graph = gen.generate(params, args.seed, maps=maps) if maps else gen.generate(params, args.seed)
    log.info("generated %d nodes, %d edges", len(graph.nodes), len(graph.edges))
    if (chosen or files) and not maps:
        layers = input_maps(args, GridSpec.around(graph, margin, cell), chosen, files)
    if layers:
        graph = align_graph(graph, layers, args.cfg)
    if "elevation" in layers and not args.flat:
        graph = drape_graph(graph, layers, args.cfg)
    return separate_graph(graph, args.cfg), layers


def separate_graph(graph: RoadGraph, cfg: dict) -> RoadGraph:
    """Roads crossing without a junction raised over each other (unchanged if none do)."""
    from roadgen.geometry.grade_separation import GradeSeparationParams, find_crossings, separate

    if not find_crossings(graph):
        return graph
    with timed("grade separation"):
        return separate(graph, GradeSeparationParams(**section(cfg, "grade_separation")),
                        DesignParams(**section(cfg, "design")), cfg["opendrive"]["junction_margin"])


def align_graph(graph: RoadGraph, layers: dict[str, MapLayer], cfg: dict) -> RoadGraph:
    """Radius caps for bends whose fitted curve would leave legal ground."""
    from roadgen.maps.align import AlignParams, align

    with timed("align"):
        return align(graph, layers.get("terrain"), layers.get("elevation"),
                     AlignParams(**section(cfg, "align")), DesignParams(**section(cfg, "design")),
                     cfg["opendrive"]["junction_margin"])


def drape_graph(graph: RoadGraph, layers: dict[str, MapLayer], cfg: dict) -> RoadGraph:
    """Heights from the elevation (and terrain) map into the graph."""
    from roadgen.maps.drape import DrapeParams, drape

    with timed("drape"):
        return drape(graph, layers["elevation"], layers.get("terrain"),
                     DrapeParams(**section(cfg, "drape")), DesignParams(**section(cfg, "design")),
                     cfg["opendrive"]["junction_margin"])


def map_requests(args: argparse.Namespace, kinds: Optional[Sequence[str]] = None
                 ) -> tuple[dict[str, str], dict[str, str]]:
    """(kind -> generator name) from --maps / --terrain / ..., (kind -> .npz) from --map-file.

    A --map-file wins over a generator for the same kind. --maps builds only
    kinds (the generator's map_kinds; None = all); a flag for a kind always builds it.
    """
    files: dict[str, str] = {}
    for item in getattr(args, "map_file", None) or []:
        kind, sep, path = item.partition("=")
        if not sep or kind not in MAP_KINDS:
            raise SystemExit(f"error: --map-file expects <kind>=FILE.npz with kind in {MAP_KINDS}")
        files[kind] = path
    use_defaults = getattr(args, "maps", False)
    chosen = {
        kind: getattr(args, kind, None)
        or (args.cfg["maps"]["pipeline"][kind] if use_defaults and (kinds is None or kind in kinds) else None)
        for kind in MAP_KINDS if kind not in files
    }
    return {k: v for k, v in chosen.items() if v}, files


def input_maps(args: argparse.Namespace, grid: GridSpec, chosen: dict[str, str],
               files: dict[str, str]) -> dict[str, MapLayer]:
    """Loaded and generated map layers, built in MAP_KINDS order (terrain ->
    elevation -> population), each generated one on the layers before it.

    A loaded file is used as it is, but still feeds the kinds after it.
    """
    layers = {}
    for kind in MAP_KINDS:
        if kind in files:
            try:
                layer = load_npz(files[kind])
            except (OSError, ValueError, KeyError) as e:
                raise SystemExit(f"error: --map-file {kind}: {e}") from e
            if layer.kind != kind:
                raise SystemExit(f"error: --map-file {kind}: {files[kind]} holds a {layer.kind} map")
            layer.metadata = {**layer.metadata, "source": files[kind]}
            layers[kind] = layer
        elif kind in chosen:
            values, preset = spec_params(args, "maps", kind, chosen[kind])
            layers[kind] = build_map(kind, chosen[kind], grid, args.seed, args.cfg, preset=preset,
                                     overrides=values, upstream=MapSet(**layers))
    return layers


def maps_metadata(layers: dict[str, MapLayer], prefix: Optional[Path]) -> dict[str, dict]:
    """graph.metadata["maps"]: generator and file of each map."""
    out = {}
    for kind, layer in layers.items():
        entry = {"generator": layer.metadata.get("generator")}
        if prefix is not None:
            entry["file"] = f"{prefix.name}_{kind}.npz"
        if "source" in layer.metadata:
            entry["source"] = layer.metadata["source"]
        out[kind] = entry
    return out


def cmd_generate(args: argparse.Namespace) -> int:
    graph, layers = generate_graph(args)
    if layers:
        graph.metadata["maps"] = maps_metadata(layers, None)
    output = artifact(args, args.output, default_output(args).with_suffix(".json"))
    save_json(graph, output)
    log.info("wrote %s", output)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Generate, then write <prefix>.json/.png/.xodr and run all validators."""
    prefix = artifact(args, args.output, default_output(args))
    graph, layers = generate_graph(args)
    errors = graph.validate()
    for err in errors:
        log.error("graph: %s", err)
    errors += check_traffic(graph, args.allow_traps)
    for kind, layer in layers.items():  # PREFIX_<kind>.npz / .png with the roads on top
        write_map(layer, Path(f"{prefix}_{kind}"), graph)
    if layers:
        graph.metadata["maps"] = maps_metadata(layers, prefix)
    save_json(graph, prefix.with_suffix(".json"))
    with timed("plot"):
        plot_graph(graph, prefix.with_suffix(".png"))
    xodr = prefix.with_suffix(".xodr")
    errors += build_and_validate(graph, xodr, args.junction_margin, args.xsd, args.cfg)
    scenegraph = None
    if args.ground:
        if "elevation" not in layers:
            raise SystemExit("error: --ground needs an elevation map (--maps, --elevation or --map-file)")
        scenegraph = Path(f"{prefix}_scene.osgt")
        write_scene(graph, layers, scenegraph, args.cfg, args.seed, args.buildings,
                    args.junction_margin)
    elif args.buildings:
        from roadgen.scene.buildings import BuildingParams, export_buildings

        scenegraph = Path(f"{prefix}_buildings.osgt")
        # Buildings stand on the ground only if the roads do.
        ground = layers.get("elevation") if graph.has_elevation else None
        with timed("buildings"):
            params = BuildingParams(seed=args.seed, **section(args.cfg, "buildings"))
            n = export_buildings(graph, scenegraph, params, ground, layers.get("terrain"))
        log.info("wrote %s (%d buildings)", scenegraph, n)
    if args.drive_check and not errors:
        errors += ["drive-check failed"] if cmd_drive_check(argparse.Namespace(input=str(xodr))) else []
    if args.simulate and not errors:  # .xosc and esmini's outputs next to the .xodr
        errors += simulate_network(xodr, args, scenegraph)
    log.info("pipeline %s (%s)", "FAILED" if errors else "passed", prefix)
    return 1 if errors else 0


def write_scene(graph: RoadGraph, layers: dict[str, MapLayer], path: Path, cfg: dict,
                seed: int, buildings: bool, junction_margin: float) -> None:
    """Ground mesh (and buildings on it) as one .osgt for esmini's SceneGraphFile."""
    from roadgen.scene.buildings import BuildingParams, building_mesh
    from roadgen.scene.ground import GroundParams, ground_mesh
    from roadgen.scene.osgt import Mesh, write_osgt

    elevation, terrain = layers["elevation"], layers.get("terrain")
    with timed("scene"):
        meshes = [ground_mesh(elevation, terrain, graph, GroundParams(**section(cfg, "ground")),
                              junction_margin, DesignParams(**section(cfg, "design")))]
        n = 0
        if buildings:
            params = BuildingParams(seed=seed, **section(cfg, "buildings"))
            tris, normals, n = building_mesh(graph, params, elevation, terrain)
            meshes.append(Mesh("buildings", tris, normals, tuple(params.color)))
        write_osgt(path, meshes)
    log.info("wrote %s (ground%s)", path, f" + {n} buildings" if buildings else "")


def load_layers(items: Optional[list[str]], flag: str = "--map-file") -> dict[str, MapLayer]:
    """KIND=FILE.npz items -> {kind: layer}."""
    layers = {}
    for item in items or []:
        kind, sep, path = item.partition("=")
        if not sep or kind not in MAP_KINDS:
            raise SystemExit(f"error: {flag} expects <kind>=FILE.npz with kind in {MAP_KINDS}")
        try:
            layers[kind] = load_npz(path)
        except (OSError, ValueError, KeyError) as e:
            raise SystemExit(f"error: {flag} {kind}: {e}") from e
        if layers[kind].kind != kind:
            raise SystemExit(f"error: {flag} {kind}: {path} holds a {layers[kind].kind} map")
    return layers


def cmd_align(args: argparse.Namespace) -> int:
    layers = load_layers(args.map_file)
    if not {"terrain", "elevation"} & set(layers):
        raise SystemExit("error: align needs --map-file terrain=FILE.npz and/or elevation=FILE.npz")
    graph = align_graph(load_json(args.input), layers, args.cfg)
    output = artifact(args, args.output, Path(args.input).with_suffix(".aligned.json"))
    save_json(graph, output)
    log.info("wrote %s", output)
    return 0


def cmd_drape(args: argparse.Namespace) -> int:
    layers = load_layers(args.map_file)
    if "elevation" not in layers:
        raise SystemExit("error: drape needs --map-file elevation=FILE.npz")
    graph = drape_graph(load_json(args.input), layers, args.cfg)
    output = artifact(args, args.output, Path(args.input).with_suffix(".draped.json"))
    save_json(graph, output)
    log.info("wrote %s", output)
    return 0


def cmd_separate(args: argparse.Namespace) -> int:
    graph = separate_graph(load_json(args.input), args.cfg)
    output = artifact(args, args.output, Path(args.input).with_suffix(".separated.json"))
    save_json(graph, output)
    log.info("wrote %s (%d overpasses)", output, len(graph.metadata.get("overpasses", [])))
    return 0


def cmd_ground(args: argparse.Namespace) -> int:
    layers = load_layers(args.map_file)
    if "elevation" not in layers:
        raise SystemExit("error: ground needs --map-file elevation=FILE.npz")
    graph = load_json(args.graph) if args.graph else RoadGraph()
    write_scene(graph, layers, artifact(args, args.output, Path("out") / "scene.osgt"), args.cfg, args.seed,
                args.buildings and bool(graph.edges), args.junction_margin)
    return 0


def build_map(kind: str, name: str, grid: GridSpec, seed: int, cfg: dict,
              path: Optional[str] = None, preset: Optional[str] = None,
              overrides: Optional[dict] = None, upstream: Optional[MapSet] = None) -> MapLayer:
    try:
        with timed(f"{kind} map ({name})"):
            return make_map(kind, name, grid, seed, path, preset, overrides,
                            section(cfg, "maps", kind, name), upstream)
    except TypeError as e:  # missing required fields, e.g. an image's path
        raise SystemExit(f"error: {kind} map {name!r}: {e}") from e
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(f"error: {kind} map {name!r}: {e}") from e


def write_map(layer: MapLayer, prefix: Path, graph: Optional[RoadGraph] = None) -> None:
    """<prefix>.npz and a <prefix>.png preview."""
    from roadgen.maps.plot import plot_map

    save_npz(layer, prefix.with_suffix(".npz"))
    plot_map(layer, prefix.with_suffix(".png"), graph)
    log.info("wrote %s and .png (%s)", prefix.with_suffix(".npz"), layer.summary())


def cmd_map(args: argparse.Namespace) -> int:
    """Generate one map on the grid from --origin/--size/--cell-size (or config)."""
    g = args.cfg["maps"]["grid"]
    try:
        grid = GridSpec(tuple(args.origin or g["origin"]), tuple(args.size or g["size"]),
                        args.cell_size or g["cell_size"])
    except ValueError as e:
        raise SystemExit(f"error: {e}") from e
    values, preset = spec_params(args, "maps", args.kind, args.map_generator)
    layer = build_map(args.kind, args.map_generator, grid, args.seed, args.cfg,
                      preset=preset, overrides=values, upstream=upstream_maps(args))
    graph = load_json(args.graph) if args.graph else None
    name = "_".join(filter(None, [args.kind, args.map_generator, preset])) + f"_s{args.seed}"
    write_map(layer, artifact(args, args.output, Path("out") / name), graph)
    return 0


def upstream_maps(args: argparse.Namespace) -> MapSet:
    """`map --on KIND=FILE.npz`: the earlier maps of the pipeline to build on."""
    earlier = MAP_KINDS[:MAP_KINDS.index(args.kind)]
    items = getattr(args, "on", None) or []
    if any(item.partition("=")[0] not in earlier for item in items):
        raise SystemExit(f"error: --on expects <kind>=FILE.npz with kind in {earlier} "
                         f"(the maps before {args.kind})")
    return MapSet(**load_layers(items, "--on"))


def cmd_plot_map(args: argparse.Namespace) -> int:
    from roadgen.maps.plot import plot_map

    graph = load_json(args.graph) if args.graph else None
    output = artifact(args, args.output, Path(args.input).with_suffix(".png"))
    plot_map(load_npz(args.input), output, graph)
    log.info("wrote %s", output)
    return 0


def cmd_maps(args: argparse.Namespace) -> int:
    """List registered map generators per kind with their presets and parameters."""
    for kind in MAP_KINDS:
        print(f"[{kind}]")
        for name in map_generator_names(kind):
            gen = get_map_generator(kind, name)
            print(f"{name}: {gen.description}")
            presets = presets_of(gen.params_type)
            print(f"  presets: {', '.join(presets) if presets else '-'}")
            defaults = section(args.cfg, "maps", kind, name)
            for f in dataclasses.fields(gen.params_type):
                default = repr(defaults[f.name]) if f.name in defaults else "required"
                print(f"  {f.name} = {default}")
    return 0


def simulate_network(xodr: Path, args: argparse.Namespace, scenegraph: Optional[Path]) -> list[str]:
    """Short headless esmini run with random traffic; returns errors."""
    from roadgen.scenario.xosc_builder import ScenarioBuilder, ScenarioParams
    from roadgen.sim.runner import run_esmini

    xosc = xodr.with_suffix(".xosc")
    defaults = {**section(args.cfg, "scenario"), "seed": args.seed, "sim_duration": args.sim_duration}
    params = ScenarioParams(args.sim_vehicles, **defaults)
    try:
        ScenarioBuilder.from_files(xodr).write(params, xosc, scenegraph=scenegraph)
        result = run_esmini(
            xosc, timestep=args.cfg["esmini"]["timestep"], record=False, csv=False,
            timeout=args.cfg["pipeline"]["sim_timeout"],
        )
    except (FileNotFoundError, ValueError) as e:
        return [f"simulate: {e}"]
    log.info(
        "esmini: %d vehicles, %.1f s simulated in %.2f s (load %.2f s), %d errors",
        result.n_vehicles, result.sim_time, result.wall_time, result.load_time or 0.0, len(result.errors),
    )
    return [f"esmini: {e}" for e in result.errors]


def cmd_generators(args: argparse.Namespace) -> int:
    """List registered generators with their presets and parameters."""
    for name in generator_names():
        gen = get_generator(name)
        print(f"{name}: {gen.description}")
        presets = presets_of(gen.params_type)
        print(f"  presets: {', '.join(presets) if presets else '-'}")
        defaults = section(args.cfg, "generators", name)
        for f in dataclasses.fields(gen.params_type):
            default = repr(defaults[f.name]) if f.name in defaults else "required"
            print(f"  {f.name} = {default}")
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    try:
        odrviewer = esmini_binary("odrviewer")
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    cmd = [str(odrviewer), "--odr", args.input, "--density", str(args.density)]
    # Explicit size: without it the first window is 0x0, which VirtualGL
    # (gui-mac container) can't render into.
    cmd += ["--window", *map(str, args.cfg["esmini"]["window"])]
    if args.scenegraph:  # drawn next to the roads odrviewer still generates itself
        cmd += ["--model", args.scenegraph, "--enforce_generate_model"]
    if args.duration is not None:
        cmd += ["--duration", str(args.duration)]
    log.info("running %s", " ".join(cmd))
    return subprocess.run(cmd).returncode


def cmd_drive_check(args: argparse.Namespace) -> int:
    from roadgen.sim.drive_check import drive_lanes, drive_movements

    try:
        results = drive_movements(args.input)
        lanes = drive_lanes(args.input)
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    failed = [r for r in results if not r.ok]
    for r in results:
        (log.error if not r.ok else log.debug)("%s", r.describe())
    log.info("%d/%d movements drove through correctly", len(results) - len(failed), len(results))
    bad_lanes = [r for r in lanes if not r.ok]
    for r in lanes:
        (log.error if not r.ok else log.debug)("%s", r.describe())
    log.info("%d/%d road lanes drove end to end on the expected centre line",
             len(lanes) - len(bad_lanes), len(lanes))
    return 1 if failed or bad_lanes else 0


def cmd_metrics(args: argparse.Namespace) -> int:
    """Geometry metrics of .xodr files, network metrics of RoadGraph JSON files and/or a
    build-failure sweep; optional JSON report."""
    import json

    from roadgen.metrics import network
    from roadgen.metrics.geometry import (
        GeometryMetricsParams,
        geometry_metrics,
        summary_lines,
    )
    from roadgen.metrics.sweep import build_sweep

    if not args.inputs and not args.graph and not args.sweep:
        raise SystemExit("error: metrics needs .xodr files, --graph files and/or --sweep N")
    params = GeometryMetricsParams(**section(args.cfg, "metrics"))
    net_params = network.NetworkMetricsParams(**section(args.cfg, "network_metrics"))
    report: dict = {"networks": []}
    for path in args.inputs:
        with timed(f"metrics {path}"):
            m = geometry_metrics(path, params)
        for line in summary_lines(m):
            log.info("%s", line)
        report["networks"].append(m)
    if args.graph:
        report["graphs"] = []
    for path in args.graph or []:
        with timed(f"network metrics {path}"):
            m = {"graph": Path(path).name, **network.network_metrics(load_json(path), net_params)}
        for line in network.summary_lines(m):
            log.info("%s", line)
        report["graphs"].append(m)
    if args.sweep:
        seeds = range(args.first_seed, args.first_seed + args.sweep)
        with timed("build sweep"):
            results = build_sweep(args.generator, args.preset, seeds, args.cfg)
        failed = [r for r in results if not r.ok]
        log.info("build sweep (%s, preset %s): %d of %d networks failed to build",
                 args.generator, args.preset, len(failed), len(results))
        for key in ("cnr", "skewed_share", "orientation_entropy"):
            values = [r.network[key] for r in results if r.network]
            if values:
                log.info("build sweep: %s median %.3f (min %.3f, max %.3f)", key,
                         statistics.median(values), min(values), max(values))
        report["sweep"] = {
            "generator": args.generator, "preset": args.preset, "seeds": list(seeds),
            "failed": len(failed),
            "results": [dataclasses.asdict(r) for r in results],
        }
    if args.json:
        out = artifact(args, args.json, args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        log.info("wrote %s", out)
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    from roadgen.scenario.xosc_builder import ScenarioBuilder, ScenarioParams

    xodr = Path(args.input)
    graph_path = Path(args.graph) if args.graph else xodr.with_suffix(".json")
    graph = load_json(graph_path) if graph_path.is_file() else None
    params = ScenarioParams(
        n_vehicles=args.vehicles,
        seed=args.seed,
        sim_duration=args.duration,
        spawn_mode=args.mode,
        speed_range=(args.speed_min, args.speed_max),
        min_spawn_gap=args.min_gap,
    )
    try:
        builder = ScenarioBuilder.from_files(xodr, graph)
        builder.write(params, artifact(args, args.output, xodr.with_suffix(".xosc")),
                      scenegraph=args.scenegraph)
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        return 1
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from roadgen.sim.runner import run_esmini

    extra = ["--camera_mode", args.camera] if args.gui and args.camera else []
    try:
        result = run_esmini(
            args.input,
            headless=not args.gui,
            timestep=args.timestep,
            record=not args.no_record,
            csv=not args.no_csv,
            extra_args=extra,
            out_dir=getattr(args, "run_dir", None),  # default: next to the .xosc
            window=args.cfg["esmini"]["window"],
        )
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    for w in result.warnings:
        log.warning("esmini: %s", w)
    for e in result.errors:
        log.error("esmini: %s", e)
    log.info(
        "%d vehicles, %.1f s simulated in %.2f s wall (%.1fx real time), "
        "%d errors, %d warnings (+%d missing-asset warnings ignored); log: %s",
        result.n_vehicles, result.sim_time, result.wall_time, result.real_time_factor,
        len(result.errors), len(result.warnings), result.asset_warnings, result.log_path,
    )
    if result.load_time is not None:
        log.info("load %.2f s, stepping %.2f s (%.1fx real time), peak memory %.0f MB",
                 result.load_time, result.step_wall_time,
                 result.sim_time / result.step_wall_time if result.step_wall_time else 0.0,
                 result.peak_rss_mb)
    return 0 if result.ok else 1


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", dest="config_file", metavar="FILE",
        help="YAML file overriding default parameters (see roadgen/defaults.yaml)",
    )


def build_parser(config: Optional[dict] = None) -> argparse.ArgumentParser:
    """CLI parser whose defaults come from config (default: the packaged defaults)."""
    cfg = config if config is not None else load_config()
    parser = argparse.ArgumentParser(prog="cli", description="Road network tools")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    add_config_arg(parser)
    parser.add_argument("--note", help="description stored with this run in its run.yaml")
    parser.add_argument("--no-history", action="store_true",
                        help="don't record this run: no out/<hash>/ folder, -o is a path")
    parser.set_defaults(cfg=cfg)
    sub = parser.add_subparsers(dest="command", required=True)
    margin = cfg["opendrive"]["junction_margin"]
    scenario, esmini, pipeline = cfg["scenario"], cfg["esmini"], cfg["pipeline"]

    p = sub.add_parser("history", help="list recorded runs in out/<hash>/ (hash, note, command)")
    p.add_argument("--grep", metavar="TEXT", help="only runs whose note or command contains TEXT")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("replay", help="run a recorded command again from its hash "
                                      "(also: python -m cli HASH)")
    p.add_argument("hash", help="run hash or a unique prefix of it (4+ digits)")
    p.add_argument("--show", action="store_true",
                   help="print the command and the run's folder instead of running it")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("run", help="run a run config (out/<hash>/run.yaml, or one written by hand)")
    p.add_argument("file", help="YAML with command, args and config (see docs/cli-reference.md#run)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("plot-graph", help="render a RoadGraph JSON to an image")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("output", nargs="?", help="output image (default: <input>.png)")
    p.add_argument("--node-ids", action="store_true", help="label nodes with ids")
    p.set_defaults(func=cmd_plot_graph)

    p = sub.add_parser("build-xodr", help="convert a RoadGraph JSON to OpenDRIVE")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("output", nargs="?",
                   help="output .xodr, edge->road map goes next to it (default: <input>.xodr)")
    p.add_argument("--xsd", help="OpenDRIVE XSD (default: $OPENDRIVE_XSD)")
    p.add_argument(
        "--junction-margin", type=float, default=margin,
        help="extra clearance [m] between junction center region and roads (default %(default)s)",
    )
    p.set_defaults(func=cmd_build_xodr)

    p = sub.add_parser("generators", help="list registered generators, presets and parameters")
    p.set_defaults(func=cmd_generators)

    p = sub.add_parser("generate", help="generate a RoadGraph JSON")
    gen = p.add_subparsers(dest="generator", required=True)
    for name in generator_names():
        g = gen.add_parser(name, help=get_generator(name).description)
        _add_generator_args(g, name, cfg)
        g.add_argument("-o", "--output", help="output RoadGraph JSON (default: <name>[_<preset>]_s<seed>.json)")
        g.set_defaults(func=cmd_generate)

    p = sub.add_parser("pipeline", help="generate, plot, build .xodr and validate in one go")
    gen = p.add_subparsers(dest="generator", required=True)
    for name in generator_names():
        g = gen.add_parser(name, help=get_generator(name).description)
        _add_generator_args(g, name, cfg)
        g.add_argument(
            "-o", "--output",
            help="output prefix; writes <prefix>.json, .png, .xodr and .roadmap.json "
                 "(default: <name>[_<preset>]_s<seed>)",
        )
        g.add_argument("--xsd", help="OpenDRIVE XSD (default: $OPENDRIVE_XSD)")
        g.add_argument(
            "--allow-traps", action="store_true",
            help="report dead ends / one-way traps as warnings instead of errors",
        )
        g.add_argument(
            "--junction-margin", type=float, default=margin,
            help="extra clearance [m] between junction center region and roads (default %(default)s)",
        )
        g.add_argument(
            "--drive-check", action="store_true",
            help="also drive every junction movement with esmini's RoadManager",
        )
        g.add_argument("--buildings", action="store_true",
                       help="also export building boxes per block to <prefix>_buildings.osgt "
                            "(used as SceneGraphFile by --simulate)")
        g.add_argument("--ground", action="store_true",
                       help="also export a 3D ground mesh from the maps (with the --buildings on it) "
                            "to <prefix>_scene.osgt (used as SceneGraphFile by --simulate)")
        g.add_argument("--simulate", action="store_true",
                       help="also run a short headless esmini simulation with random traffic")
        _add_pipeline_map_args(g, cfg)
        g.add_argument("--sim-vehicles", type=int, default=pipeline["sim_vehicles"],
                       help="(default %(default)s)")
        g.add_argument("--sim-duration", type=float, default=pipeline["sim_duration"],
                       help="[s] (default %(default)s)")
        g.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("align", help="cap bend radii where a fitted curve would leave legal "
                                     "ground (water, steep grades)")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("--map-file", action="append", metavar="KIND=FILE.npz", required=True,
                   help="terrain and/or elevation map, repeatable")
    p.add_argument("-o", "--output", help="output RoadGraph JSON (default: <input>.aligned.json)")
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("drape", help="give a RoadGraph heights from an elevation map")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("--map-file", action="append", metavar="KIND=FILE.npz", required=True,
                   help="elevation map (required) and terrain map (bridges), repeatable")
    p.add_argument("-o", "--output", help="output RoadGraph JSON (default: <input>.draped.json)")
    p.set_defaults(func=cmd_drape)

    p = sub.add_parser("separate", help="raise roads that cross without a junction over each other "
                                        "(overpasses with ramps and bridge spans)")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("-o", "--output", help="output RoadGraph JSON (default: <input>.separated.json)")
    p.set_defaults(func=cmd_separate)

    p = sub.add_parser("ground", help="3D ground mesh (.osgt) from maps, levelled under a network")
    p.add_argument("output", nargs="?", help="output .osgt (default: scene.osgt)")
    p.add_argument("--map-file", action="append", metavar="KIND=FILE.npz", required=True,
                   help="elevation map (required) and terrain map (colors), repeatable")
    p.add_argument("--graph", help="draped RoadGraph JSON: ground is levelled under its roads")
    p.add_argument("--buildings", action="store_true", help="add the --graph's buildings")
    p.add_argument("--seed", type=int, default=cfg["seed"], help="building heights (default %(default)s)")
    p.add_argument(
        "--junction-margin", type=float, default=margin,
        help="as used for the .xodr [m] (default %(default)s)",
    )
    p.set_defaults(func=cmd_ground)

    p = sub.add_parser("maps", help="list map generators (terrain, elevation, population)")
    p.set_defaults(func=cmd_maps)

    p = sub.add_parser("map", help="generate one input map (.npz + .png)")
    kinds = p.add_subparsers(dest="kind", required=True)
    grid = cfg["maps"]["grid"]
    for kind in MAP_KINDS:
        k = kinds.add_parser(kind, help=f"generate a {kind} map")
        names = k.add_subparsers(dest="map_generator", required=True)
        for name in map_generator_names(kind):
            gen = get_map_generator(kind, name)
            m = names.add_parser(name, help=gen.description)
            m.add_argument("--seed", type=int, default=cfg["seed"], help="(default %(default)s)")
            m.add_argument("--params", help="YAML with parameter values (or preset: + params:)")
            m.add_argument("--preset", choices=presets_of(gen.params_type) or None,
                           help="start from a named parameter set")
            m.add_argument("--set", action="append", metavar="KEY=VALUE",
                           help="override one parameter (repeatable), e.g. --set water_fraction=0.3")
            m.add_argument("--origin", type=float, nargs=2, metavar=("X", "Y"),
                           help=f"lower-left corner [m] (default {grid['origin']})")
            m.add_argument("--size", type=float, nargs=2, metavar=("W", "H"),
                           help=f"[m] (default {grid['size']})")
            m.add_argument("--cell-size", type=float, help=f"[m] (default {grid['cell_size']})")
            m.add_argument("--graph", help="RoadGraph JSON to draw on the preview")
            if kind != MAP_KINDS[0]:
                earlier = MAP_KINDS[:MAP_KINDS.index(kind)]
                m.add_argument("--on", action="append", metavar="KIND=FILE.npz",
                               help=f"build on an earlier map of the pipeline (repeatable; "
                                    f"kind in {', '.join(earlier)}), e.g. --on terrain=out/coast.npz")
            m.add_argument("-o", "--output",
                           help="output prefix; writes <prefix>.npz and .png "
                                "(default: <kind>_<name>[_<preset>]_s<seed>)")
            m.set_defaults(func=cmd_map)

    p = sub.add_parser("plot-map", help="render a map .npz to an image")
    p.add_argument("input", help="map .npz written by map/pipeline")
    p.add_argument("output", nargs="?", help="output image (default: <input>.png)")
    p.add_argument("--graph", help="RoadGraph JSON to draw on top")
    p.set_defaults(func=cmd_plot_map)

    p = sub.add_parser("scenario", help="generate an OpenSCENARIO file with many vehicles")
    p.add_argument("input", help=".xodr written by build-xodr/pipeline (needs its .roadmap.json)")
    p.add_argument("-o", "--output", help="output .xosc (default: <input>.xosc)")
    p.add_argument("--vehicles", type=int, required=True)
    p.add_argument("--seed", type=int, default=cfg["seed"], help="(default %(default)s)")
    p.add_argument("--duration", type=float, default=scenario["sim_duration"],
                   help="simulated time [s] (default %(default)s)")
    p.add_argument(
        "--mode", choices=("random_lanes", "explicit_routes"), default=scenario["spawn_mode"],
        help="random junction choices, or shortest-path routes to random destinations "
             "(default %(default)s)",
    )
    p.add_argument("--speed-min", type=float, default=scenario["speed_range"][0],
                   help="[m/s] (default %(default)s)")
    p.add_argument("--speed-max", type=float, default=scenario["speed_range"][1],
                   help="[m/s] (default %(default)s)")
    p.add_argument("--min-gap", type=float, default=scenario["min_spawn_gap"],
                   help="min spawn gap in a lane [m] (default %(default)s)")
    p.add_argument("--graph", help="RoadGraph JSON (default: <input>.json if present)")
    p.add_argument("--scenegraph", help="3D model to show with the roads, e.g. <prefix>_buildings.osgt")
    p.set_defaults(func=cmd_scenario)

    p = sub.add_parser("simulate", help="run an .xosc in esmini and summarize the run")
    p.add_argument("input", help=".xosc file")
    p.add_argument("--gui", action="store_true", help="show the esmini window")
    p.add_argument(
        "--camera", default=esmini["camera"],
        help="camera mode with --gui (orbit, fixed, flex, flex-orbit, top, driver; "
             "default %(default)s)",
    )
    p.add_argument("--timestep", type=float, default=esmini["timestep"],
                   help="fixed timestep [s] (default %(default)s)")
    p.add_argument("--no-record", action="store_true", help="skip the .dat recording")
    p.add_argument("--no-csv", action="store_true", help="skip the .csv log")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("view", help="open an .xodr in esmini's odrviewer")
    p.add_argument("input", help=".xodr file")
    p.add_argument("--density", type=float, default=esmini["view_density"],
                   help="cars per 100 m (default %(default)s)")
    p.add_argument("--scenegraph", help="3D model to show with the roads, e.g. <prefix>_buildings.osgt "
                                        "or <prefix>_scene.osgt (ground + buildings)")
    p.add_argument("--duration", type=float,
                   help="close the viewer after this many seconds [s] (default: when closed)")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser(
        "drive-check", help="drive every junction movement with esmini's RoadManager"
    )
    p.add_argument("input", help=".xodr file written by build-xodr (needs its .roadmap.json)")
    p.set_defaults(func=cmd_drive_check)

    p = sub.add_parser("metrics", help="geometry metrics of .xodr files (curvature, lateral "
                                       "acceleration, two-road junctions), network metrics of "
                                       "graphs and a build-failure sweep")
    p.add_argument("inputs", nargs="*", help=".xodr files to measure")
    p.add_argument("--graph", action="append", metavar="JSON",
                   help="RoadGraph JSON whose network metrics (junction legs, skew, CNR, "
                        "orientation entropy, blocks) to report, repeatable")
    p.add_argument("--json", metavar="OUT", help="write the full report as JSON")
    p.add_argument("--sweep", type=int, metavar="N",
                   help="also generate N networks (seeds --first-seed.. , maps as pipeline --maps) "
                        "and count how many the builder rejects")
    p.add_argument("--first-seed", type=int, default=0, help="first sweep seed (default %(default)s)")
    p.add_argument("--generator", default="lsystem", choices=generator_names(),
                   help="sweep generator (default %(default)s)")
    p.add_argument("--preset", default="city", help="sweep generator preset (default %(default)s)")
    p.set_defaults(func=cmd_metrics)

    return parser


def _add_generator_args(p: argparse.ArgumentParser, name: str, cfg: dict) -> None:
    """Flags shared by every generator."""
    gen = get_generator(name)
    p.add_argument("--seed", type=int, default=cfg["seed"], help="(default %(default)s)")
    p.add_argument("--params", help="YAML with parameter values (or preset: + params:)")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="override one parameter (repeatable), e.g. --set rotation_deg=15")
    p.add_argument("--map-file", action="append", metavar="KIND=FILE.npz",
                   help="input map to use, written by `map` or a pipeline (repeatable), "
                        "e.g. --map-file terrain=out/coast.npz")
    p.add_argument("--flat", action="store_true",
                   help="keep the roads flat even with an elevation map (no heights in the graph)")
    presets = presets_of(gen.params_type)
    p.add_argument("--preset", choices=presets or None, help="start from a named parameter set")


def _add_pipeline_map_args(p: argparse.ArgumentParser, cfg: dict) -> None:
    defaults = cfg["maps"]["pipeline"]
    p.add_argument("--maps", action="store_true",
                   help="generate terrain, elevation (on the terrain), population (on both) and, "
                        "for lsystem's field goal, style (on all three) maps first and grow the "
                        "network on them (lsystem); other generators get them for reference only "
                        f"({', '.join(f'{k}: {v}' for k, v in defaults.items())})")
    for kind in MAP_KINDS:
        p.add_argument(f"--{kind}", choices=map_generator_names(kind), metavar="NAME",
                       help=f"generate a {kind} map with this generator (lsystem grows on it) "
                            f"({', '.join(map_generator_names(kind))}); overrides --maps")
    p.add_argument("--map-set", action="append", metavar="KIND.KEY=VALUE",
                   help="override one map parameter (repeatable), e.g. --map-set terrain.rivers=2")


# Commands that only report or run another run: never recorded themselves.
NOT_RECORDED = frozenset({"generators", "maps", "history", "replay", "run"})
# Generator / map parameters: folded into the run's config (generators.<name>,
# maps.<kind>.<name>) instead of its args.
PARAM_COMMANDS = ("generate", "pipeline", "map")
PARAM_DESTS = frozenset({"preset", "params", "set", "map_set"})
GLOBAL_DESTS = frozenset({"cfg", "func", "config_file", "verbose", "note", "no_history"})
HASH_ARG = re.compile(r"[0-9a-fA-F]{4,64}")
GLOBAL_VALUE_FLAGS = ("--note",)  # global flags that take a value
# Generators that no longer exist, for a clear message when an old run is replayed.
REMOVED_GENERATORS = {
    "manhattan": "the manhattan generator was removed in stage C; lsystem grows the same grids "
                 "(--preset manhattan_like, or --set oneway_pattern=... boundary=ring)",
}


def store_of(cfg: dict) -> Optional[RunStore]:
    """The run folders from config (history.dir) or $ROADGEN_HISTORY; None if off."""
    return RunStore.from_env(cfg["history"]["dir"])


def cmd_history(args: argparse.Namespace) -> int:
    store = store_of(args.cfg)
    if store is None:
        log.info("run history is off")
        return 0
    runs = store.runs()
    if args.grep:
        runs = [r for r in runs if args.grep in r.note or args.grep in r.command_line()]
    for r in runs:
        print(f"{r.hash}  {r.note}" if r.note else r.hash)
        print(f"    {r.command_line()}")
    log.info("%d runs in %s", len(runs), store.root)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    store = store_of(args.cfg)
    if store is None:
        raise SystemExit("error: run history is off")
    try:
        run = store.get(args.hash)
    except (KeyError, ValueError) as e:
        raise SystemExit(f"error: {e.args[0]}") from e
    if args.show:
        print(run.command_line())
        print(f"(run config and outputs in {run.path})")
        return 0
    log.info("replaying %s: %s", run.hash, run.command_line())
    return execute(run.spec, args.note or run.note, not args.no_history, run.cli)[0]


def cmd_run(args: argparse.Namespace) -> int:
    try:
        spec, data = load_spec(args.file)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise SystemExit(f"error: {e}") from e
    cli = data.get("cli") or f"python -m cli run {args.file}"
    return execute(spec, args.note or data.get("note"), not args.no_history, cli)[0]


def _subparsers(parser: argparse.ArgumentParser) -> Optional[argparse._SubParsersAction]:
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _own_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions
            if not isinstance(a, (argparse._SubParsersAction, argparse._HelpAction))]


def _command_parsers(parser: argparse.ArgumentParser, command: Sequence[str]
                     ) -> list[tuple[str, argparse.ArgumentParser]]:
    """(subparsers dest, parser) for each word of command, e.g. pipeline lsystem."""
    out, p = [], parser
    for word in command:
        sub = _subparsers(p)
        if sub is None or word not in sub.choices:
            raise SystemExit(f"error: unknown command {' '.join(command)!r}")
        p = sub.choices[word]
        out.append((sub.dest, p))
    if _subparsers(p) is not None:
        raise SystemExit(f"error: incomplete command {' '.join(command)!r}")
    return out


def _fold_params(args: argparse.Namespace, config: dict) -> None:
    """--preset, --params, --set and --map-set into the run's config."""
    if args.command == "map":
        keys = ("maps", args.kind, args.map_generator)
        params_type = get_map_generator(args.kind, args.map_generator).params_type
    else:
        keys = ("generators", args.generator)
        params_type = get_generator(args.generator).params_type
    sec = config
    for key in keys:
        sec = sec.setdefault(key, {})
    preset = sec.get("preset")
    if args.params:
        try:
            data = yaml.safe_load(Path(args.params).read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            raise SystemExit(f"error: --params: {e}") from e
        if "params" in data or "preset" in data:
            preset = data.get("preset") or preset
            data = data.get("params") or {}
        sec.update(plain(data))
    sec.update(plain(parse_set(args.set)))
    preset = args.preset or preset
    # Keep only what differs from defaults + preset: that is what this run changes.
    base = plain({**section(DEFAULTS, *keys), **getattr(params_type, "PRESETS", {}).get(preset, {})})
    for key in [k for k in sec if k in base and plain(sec[k]) == base[k]]:
        del sec[key]
    if preset:
        sec["preset"] = preset
    for key, value in parse_set(getattr(args, "map_set", None)).items():
        kind, sep, field = key.partition(".")
        if not sep or kind not in MAP_KINDS:
            raise SystemExit(f"error: --map-set expects <kind>.<key>=value with kind in {MAP_KINDS}")
        name = getattr(args, kind, None) or args.cfg["maps"]["pipeline"][kind]
        config.setdefault("maps", {}).setdefault(kind, {}).setdefault(name, {})[field] = plain(value)


def _prune(config: dict) -> dict:
    """Without empty mappings."""
    out = {}
    for key, value in config.items():
        if isinstance(value, dict):
            value = _prune(value)
            if not value:
                continue
        out[key] = value
    return out


def compile_spec(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict:
    """The run's spec from parsed arguments: command, the args that differ from their
    defaults and the config that differs from defaults.yaml (--config and the
    parameter options folded in)."""
    command, p = [], parser
    while (sub := _subparsers(p)) is not None:
        command.append(getattr(args, sub.dest))
        p = sub.choices[command[-1]]
    config = diff(args.cfg, DEFAULTS)
    config.pop("history", None)
    folded = PARAM_DESTS if command[0] in PARAM_COMMANDS else frozenset()
    if folded:
        _fold_params(args, config)
    run_args = {}
    for _, sub_parser in _command_parsers(parser, command):
        for a in _own_actions(sub_parser):
            value = getattr(args, a.dest)
            if a.dest not in folded and a.dest not in GLOBAL_DESTS and value != a.default:
                run_args[a.dest] = value
    return plain({"command": command, "args": run_args, "config": _prune(config)})


def _check_params(config: dict) -> None:
    """Keys of params sections must be fields of their params class (or preset)."""
    sections = [(f"generators.{g}", get_generator, (g,), sec)
                for g, sec in (config.get("generators") or {}).items()]
    sections += [(f"maps.{kind}.{name}", get_map_generator, (kind, name), sec)
                 for kind in MAP_KINDS for name, sec in ((config.get("maps") or {}).get(kind) or {}).items()]
    for where, get, names, sec in sections:
        try:
            fields = {f.name for f in dataclasses.fields(get(*names).params_type)}
        except KeyError as e:
            raise SystemExit(f"error: config {where}: {e}") from e
        unknown = set(sec or {}) - fields - {"preset"}
        if unknown:
            raise SystemExit(f"error: unknown config keys under {where}: {sorted(unknown)}")


def namespace_for(parser: argparse.ArgumentParser, spec: dict) -> argparse.Namespace:
    """The parsed arguments of a spec: parser defaults, then its args."""
    ns: dict = {}
    for a in _own_actions(parser):
        ns[a.dest] = a.default
    ns.update(parser._defaults)
    actions: dict[str, argparse.Action] = {}
    for dest, p in _command_parsers(parser, spec["command"]):
        ns[dest] = p.prog.split()[-1]
        for a in _own_actions(p):
            ns[a.dest] = a.default
            actions[a.dest] = a
        ns.update(p._defaults)
    folded = PARAM_DESTS if spec["command"][0] in PARAM_COMMANDS else frozenset()
    name = " ".join(spec["command"])
    for dest, value in (spec.get("args") or {}).items():
        if dest not in actions or dest in folded:
            hint = " (generator parameters go under config)" if dest in folded else ""
            raise SystemExit(f"error: {name}: unknown argument {dest!r}{hint}")
        choices = actions[dest].choices
        if choices and value is not None and any(v not in choices for v in
                                                 (value if isinstance(value, list) else [value])):
            raise SystemExit(f"error: {name}: {dest} {value!r} not in {sorted(choices)}")
        ns[dest] = value
    for dest, a in actions.items():
        if a.required and ns.get(dest) is None:
            raise SystemExit(f"error: {name}: argument {dest!r} is required")
    return argparse.Namespace(**ns)


def execute(spec: dict, note: Optional[str] = None, record: bool = True,
            cli: Optional[str] = None) -> tuple[int, Optional[str]]:
    """Run a spec; (exit code, run hash). A recorded run writes into out/<hash>/,
    which only exists if the command succeeded."""
    for name in [*spec["command"], *((spec.get("config") or {}).get("generators") or {})]:
        if name in REMOVED_GENERATORS:
            raise SystemExit(f"error: {name}: {REMOVED_GENERATORS[name]}")
    try:
        cfg = with_overrides(spec["config"])
    except ValueError as e:
        raise SystemExit(f"error: config: {e}") from e
    _check_params(spec["config"])
    args = namespace_for(build_parser(cfg), spec)
    args.cfg, args.spec, args.run_dir = cfg, spec, None
    store = store_of(cfg) if record else None
    if store is None:
        return args.func(args), None
    h = run_hash(spec)
    args.run_dir = store.begin(h)
    try:
        rc = args.func(args)
    except BaseException:
        store.discard(args.run_dir)
        raise
    if rc != 0:
        store.discard(args.run_dir)
        return rc, None
    folder = store.commit(h, args.run_dir, spec, note, cli)
    log.info("run %s: %s", h, folder)
    return 0, h


def _bare_hash(argv: list[str], commands: Sequence[str]) -> list[str]:
    """`cli HASH ...` -> `cli replay HASH ...`."""
    k = 0
    while k < len(argv) and argv[k].startswith("-"):
        k += 2 if argv[k] in GLOBAL_VALUE_FLAGS else 1
    if k < len(argv) and argv[k] not in commands and HASH_ARG.fullmatch(argv[k]):
        return [*argv[:k], "replay", *argv[k:]]
    return argv


def _typed(argv: list[str]) -> str:
    """The command as typed, without the flags that don't change its result."""
    strip = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    strip.add_argument("-v", "--verbose", action="store_true")
    strip.add_argument("--note")
    strip.add_argument("--no-history", action="store_true")
    _, argv = strip.parse_known_args(argv)
    return " ".join(["python -m cli", *(_quote(a) for a in argv)])


def _quote(arg: str) -> str:
    return arg if arg and all(c.isalnum() or c in "-_./=:,[]+" for c in arg) else repr(arg)


def run_argv(argv: Optional[Sequence[str]] = None) -> tuple[int, Optional[str]]:
    """Run one command line; (exit code, run hash or None)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # Read --config first: the parser's defaults (and --help) depend on it.
    pre = argparse.ArgumentParser(add_help=False)
    add_config_arg(pre)
    known, rest = pre.parse_known_args(argv)
    try:
        config = load_config(known.config_file)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise SystemExit(f"error: --config: {e}") from e
    parser = build_parser(config)
    rest = _bare_hash(rest, list(_subparsers(parser).choices))
    args = parser.parse_args(rest)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command in NOT_RECORDED:
        return args.func(args), None
    return execute(compile_spec(parser, args), args.note, not args.no_history, _typed(argv))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one command line."""
    return run_argv(argv)[0]


if __name__ == "__main__":
    raise SystemExit(main())
