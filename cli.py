"""Command-line entry point: `python -m cli <command> ...`."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence

import yaml

from roadgen.config import load_config, merge, section
from roadgen.generators.base import (
    generator_names,
    get_generator,
    load_params,
    presets_of,
)
from roadgen.generators.manhattan import BOUNDARY_MODES, ONEWAY_PATTERNS, PRESETS
from roadgen.graph.io import load_json, save_json
from roadgen.graph.model import RoadGraph
from roadgen.graph.plot import plot_graph
from roadgen.history import History, run_params
from roadgen.maps.base import get_map_generator, make_map, map_generator_names
from roadgen.maps.model import MAP_KINDS, GridSpec, MapLayer, MapSet, load_npz, save_npz
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.sim.esmini import esmini_binary
from roadgen.validate.connectivity import check_traffic_connectivity
from roadgen.validate.continuity import check_continuity
from roadgen.validate.schema import validate_schema
from roadgen.validate.topology import check_topology

log = logging.getLogger("roadgen.cli")


def cmd_plot_graph(args: argparse.Namespace) -> int:
    graph = load_json(args.input)
    for err in graph.validate():
        log.warning("validation: %s", err)
    plot_graph(graph, args.output, show_node_ids=args.node_ids)
    log.info("wrote %s", args.output)
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


def build_and_validate(
    graph: RoadGraph, xodr_path: Path, junction_margin: float, xsd: Optional[str]
) -> list[str]:
    """Build and write the .xodr, then run every file validator; return all errors."""
    builder = OpenDriveBuilder(
        name=graph.metadata.get("name", "roadgen"), junction_margin=junction_margin
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
        len(builder.edge_to_road),
        len(builder.node_to_junction),
        len(builder.movements),
        sidecar,
    )
    with timed("validate"):
        errors = check_topology(xodr_path)
        errors += check_continuity(xodr_path)
        errors += validate_schema(xodr_path, xsd) or []
    for err in errors:
        log.error("%s", err)
    return errors


def cmd_build_xodr(args: argparse.Namespace) -> int:
    graph = load_json(args.input)
    check_traffic(graph, allow_traps=True)  # informational for hand-made graphs
    errors = build_and_validate(graph, Path(args.output), args.junction_margin, args.xsd)
    return 1 if errors else 0


# CLI flag -> ManhattanParams field, for flags that map one-to-one.
MANHATTAN_FLAGS = {
    "avenues": "n_avenues",
    "streets": "n_streets",
    "avenue_spacing": "avenue_spacing",
    "street_spacing": "street_spacing",
    "rotation": "rotation_deg",
    "origin": "origin",
    "avenue_lanes": "avenue_lanes",
    "street_lanes": "street_lanes",
    "lane_width": "lane_width",
    "avenue_speed": "avenue_speed",
    "street_speed": "street_speed",
    "jitter": "jitter",
    "edge_drop": "edge_drop_prob",
    "avenue_oneway": "avenue_oneway_pattern",
    "street_oneway": "street_oneway_pattern",
    "boundary": "boundary_mode",
    "ring_offset": "ring_offset",
    "ring_lanes": "ring_lanes",
}


def parse_set(items: Optional[Sequence[str]]) -> dict:
    """--set key=value pairs; values are parsed as YAML (numbers, lists, ...)."""
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"error: --set expects key=value, got {item!r}")
        out[key.strip()] = yaml.safe_load(value)
    return out


def generator_params(args: argparse.Namespace):
    """Params from --params YAML, --preset and overrides (flags and --set)."""
    gen = get_generator(args.generator)
    overrides = parse_set(args.set)
    if args.generator == "manhattan":
        overrides.update({
            field: tuple(value) if isinstance(value, list) else value
            for flag, field in MANHATTAN_FLAGS.items()
            if (value := getattr(args, flag, None)) is not None
        })
    try:
        defaults = section(args.cfg, "generators", args.generator)
        return load_params(gen.params_type, args.params, args.preset, overrides, defaults)
    except TypeError as e:  # missing required fields
        if args.generator == "manhattan":
            raise SystemExit("error: --avenues and --streets are required without --preset") from e
        raise SystemExit(f"error: {e}") from e
    except ValueError as e:
        raise SystemExit(f"error: {e}") from e


def default_output(args: argparse.Namespace) -> Path:
    name = "_".join(filter(None, [args.generator, args.preset])) + f"_s{args.seed}"
    return Path("out") / name


def generate_graph(args: argparse.Namespace) -> tuple[RoadGraph, dict[str, MapLayer]]:
    """The network, and the input maps requested by the map flags.

    A generator that reads maps (uses_maps) gets them, built on a grid around
    the area it reports. For any other generator the maps are only built
    afterwards, around the finished network, for reference.
    """
    gen = get_generator(args.generator)
    params = generator_params(args)
    chosen, files = map_requests(args)
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
    if "elevation" in layers and not args.flat:
        graph = drape_graph(graph, layers, args.cfg)
    return graph, layers


def drape_graph(graph: RoadGraph, layers: dict[str, MapLayer], cfg: dict) -> RoadGraph:
    """Heights from the elevation (and terrain) map into the graph."""
    from roadgen.maps.drape import DrapeParams, drape

    with timed("drape"):
        return drape(graph, layers["elevation"], layers.get("terrain"),
                     DrapeParams(**section(cfg, "drape")))


def map_requests(args: argparse.Namespace) -> tuple[dict[str, str], dict[str, str]]:
    """(kind -> generator name) from --maps / --terrain / ..., (kind -> .npz) from --map-file.

    A --map-file wins over a generator for the same kind.
    """
    files: dict[str, str] = {}
    for item in getattr(args, "map_file", None) or []:
        kind, sep, path = item.partition("=")
        if not sep or kind not in MAP_KINDS:
            raise SystemExit(f"error: --map-file expects <kind>=FILE.npz with kind in {MAP_KINDS}")
        files[kind] = path
    use_defaults = getattr(args, "maps", False)
    chosen = {
        kind: getattr(args, kind, None) or (args.cfg["maps"]["pipeline"][kind] if use_defaults else None)
        for kind in MAP_KINDS if kind not in files
    }
    return {k: v for k, v in chosen.items() if v}, files


def input_maps(args: argparse.Namespace, grid: GridSpec, chosen: dict[str, str],
               files: dict[str, str]) -> dict[str, MapLayer]:
    """Loaded and generated map layers, built in MAP_KINDS order (terrain ->
    elevation -> population), each generated one on the layers before it.

    A loaded file is used as it is, but still feeds the kinds after it.
    """
    overrides: dict[str, dict] = {kind: {} for kind in MAP_KINDS}
    for key, value in parse_set(getattr(args, "map_set", None)).items():
        kind, sep, field = key.partition(".")
        if not sep or kind not in MAP_KINDS:
            raise SystemExit(f"error: --map-set expects <kind>.<key>=value with kind in {MAP_KINDS}")
        overrides[kind][field] = value
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
            layers[kind] = build_map(kind, chosen[kind], grid, args.seed, args.cfg,
                                     overrides=overrides[kind], upstream=MapSet(**layers))
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
    output = Path(args.output) if args.output else default_output(args).with_suffix(".json")
    save_json(graph, output)
    log.info("wrote %s", output)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Generate, then write <prefix>.json/.png/.xodr and run all validators."""
    prefix = Path(args.output) if args.output else default_output(args)
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
    errors += build_and_validate(graph, xodr, args.junction_margin, args.xsd)
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
    if args.simulate and not errors:
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
                              junction_margin)]
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


def cmd_drape(args: argparse.Namespace) -> int:
    layers = load_layers(args.map_file)
    if "elevation" not in layers:
        raise SystemExit("error: drape needs --map-file elevation=FILE.npz")
    graph = drape_graph(load_json(args.input), layers, args.cfg)
    output = Path(args.output) if args.output else Path(args.input).with_suffix(".draped.json")
    save_json(graph, output)
    log.info("wrote %s", output)
    return 0


def cmd_ground(args: argparse.Namespace) -> int:
    layers = load_layers(args.map_file)
    if "elevation" not in layers:
        raise SystemExit("error: ground needs --map-file elevation=FILE.npz")
    graph = load_json(args.graph) if args.graph else RoadGraph()
    write_scene(graph, layers, Path(args.output), args.cfg, args.seed,
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
    layer = build_map(args.kind, args.map_generator, grid, args.seed, args.cfg,
                      args.params, args.preset, parse_set(args.set), upstream_maps(args))
    graph = load_json(args.graph) if args.graph else None
    name = "_".join(filter(None, [args.kind, args.map_generator, args.preset])) + f"_s{args.seed}"
    write_map(layer, Path(args.output) if args.output else Path("out") / name, graph)
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
    plot_map(load_npz(args.input), args.output, graph)
    log.info("wrote %s", args.output)
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
    from roadgen.sim.drive_check import drive_movements

    try:
        results = drive_movements(args.input)
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    failed = [r for r in results if not r.ok]
    for r in results:
        (log.error if not r.ok else log.debug)("%s", r.describe())
    log.info("%d/%d movements drove through correctly", len(results) - len(failed), len(results))
    return 1 if failed else 0


def cmd_metrics(args: argparse.Namespace) -> int:
    """Geometry metrics of .xodr files and/or a build-failure sweep; optional JSON report."""
    import json

    from roadgen.metrics.geometry import (
        GeometryMetricsParams,
        geometry_metrics,
        summary_lines,
    )
    from roadgen.metrics.sweep import build_sweep

    if not args.inputs and not args.sweep:
        raise SystemExit("error: metrics needs .xodr files and/or --sweep N")
    params = GeometryMetricsParams(**section(args.cfg, "metrics"))
    report: dict = {"networks": []}
    for path in args.inputs:
        with timed(f"metrics {path}"):
            m = geometry_metrics(path, params)
        for line in summary_lines(m):
            log.info("%s", line)
        report["networks"].append(m)
    if args.sweep:
        seeds = range(args.first_seed, args.first_seed + args.sweep)
        with timed("build sweep"):
            results = build_sweep(args.generator, args.preset, seeds, args.cfg)
        failed = [r for r in results if not r.ok]
        log.info("build sweep (%s, preset %s): %d of %d networks failed to build",
                 args.generator, args.preset, len(failed), len(results))
        report["sweep"] = {
            "generator": args.generator, "preset": args.preset, "seeds": list(seeds),
            "failed": len(failed),
            "results": [dataclasses.asdict(r) for r in results],
        }
    if args.json:
        out = Path(args.json)
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
        builder.write(params, args.output, scenegraph=args.scenegraph)
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
    return 0 if result.ok else 1


def cmd_bench(args: argparse.Namespace) -> int:
    from roadgen.bench.benchmark import BenchConfig, run_sweep

    cfg = BenchConfig.from_yaml(args.config, section(args.cfg, "bench"))
    if args.out_dir:
        cfg = dataclasses.replace(cfg, out_dir=args.out_dir)
    with timed("sweep"):
        results = run_sweep(cfg)
    log.info("results: %s", results)
    if not args.no_plot:
        cmd_bench_plot(argparse.Namespace(results=str(results), out_dir=None))
    return 0


def cmd_bench_plot(args: argparse.Namespace) -> int:
    from roadgen.bench.plots import plot_all

    for path in plot_all(args.results, args.out_dir):
        log.info("wrote %s", path)
    return 0


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
    parser.add_argument("--note", help="description stored with this run in the history")
    parser.add_argument("--no-history", action="store_true",
                        help="don't record this run in the history (see `history`)")
    parser.set_defaults(cfg=cfg)
    sub = parser.add_subparsers(dest="command", required=True)
    margin = cfg["opendrive"]["junction_margin"]
    scenario, esmini, pipeline = cfg["scenario"], cfg["esmini"], cfg["pipeline"]

    p = sub.add_parser("history", help="list recorded runs (hash, note, command)")
    p.add_argument("--grep", metavar="TEXT", help="only runs whose note or command contains TEXT")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("replay", help="run a recorded command again from its hash "
                                      "(also: python -m cli HASH)")
    p.add_argument("hash", help="run hash or a unique prefix of it (4+ digits)")
    p.add_argument("--show", action="store_true",
                   help="print the command and its recorded config file instead of running it")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("plot-graph", help="render a RoadGraph JSON to an image")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("output", help="output image (e.g. out/graph.png)")
    p.add_argument("--node-ids", action="store_true", help="label nodes with ids")
    p.set_defaults(func=cmd_plot_graph)

    p = sub.add_parser("build-xodr", help="convert a RoadGraph JSON to OpenDRIVE")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("output", help="output .xodr (edge->road map goes next to it)")
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
        g.add_argument("-o", "--output", help="output RoadGraph JSON (default: out/<name>_s<seed>.json)")
        g.set_defaults(func=cmd_generate)

    p = sub.add_parser("pipeline", help="generate, plot, build .xodr and validate in one go")
    gen = p.add_subparsers(dest="generator", required=True)
    for name in generator_names():
        g = gen.add_parser(name, help=get_generator(name).description)
        _add_generator_args(g, name, cfg)
        g.add_argument(
            "-o", "--output",
            help="output prefix; writes <prefix>.json, .png, .xodr and .roadmap.json "
                 "(default: out/<name>[_<preset>]_s<seed>)",
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

    p = sub.add_parser("drape", help="give a RoadGraph heights from an elevation map")
    p.add_argument("input", help="RoadGraph JSON file")
    p.add_argument("--map-file", action="append", metavar="KIND=FILE.npz", required=True,
                   help="elevation map (required) and terrain map (bridges), repeatable")
    p.add_argument("-o", "--output", help="output RoadGraph JSON (default: <input>.draped.json)")
    p.set_defaults(func=cmd_drape)

    p = sub.add_parser("ground", help="3D ground mesh (.osgt) from maps, levelled under a network")
    p.add_argument("output", help="output .osgt")
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
                                "(default: out/<kind>_<name>[_<preset>]_s<seed>)")
            m.set_defaults(func=cmd_map)

    p = sub.add_parser("plot-map", help="render a map .npz to an image")
    p.add_argument("input", help="map .npz written by map/pipeline")
    p.add_argument("output", help="output image (e.g. out/terrain.png)")
    p.add_argument("--graph", help="RoadGraph JSON to draw on top")
    p.set_defaults(func=cmd_plot_map)

    p = sub.add_parser("scenario", help="generate an OpenSCENARIO file with many vehicles")
    p.add_argument("input", help=".xodr written by build-xodr/pipeline (needs its .roadmap.json)")
    p.add_argument("-o", "--output", required=True, help="output .xosc")
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

    p = sub.add_parser("bench", help="run a resumable scalability sweep from a YAML config")
    p.add_argument("config", help="YAML sweep config (see configs/)")
    p.add_argument("--out-dir", help="override out_dir from the config")
    p.add_argument("--no-plot", action="store_true", help="skip plotting after the sweep")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("bench-plot", help="plot a benchmark results.csv (PNG + PDF)")
    p.add_argument("results", help="results.csv written by bench")
    p.add_argument("--out-dir", help="default: <results dir>/plots")
    p.set_defaults(func=cmd_bench_plot)

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
                                       "acceleration, two-road junctions) and a build-failure sweep")
    p.add_argument("inputs", nargs="*", help=".xodr files to measure")
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
    """Flags shared by every generator, plus Manhattan's dedicated flags."""
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
    if name == "manhattan":
        _add_manhattan_args(p, section(cfg, "generators", "manhattan"))
    else:
        presets = presets_of(gen.params_type)
        p.add_argument("--preset", choices=presets or None, help="start from a named parameter set")


def _add_pipeline_map_args(p: argparse.ArgumentParser, cfg: dict) -> None:
    defaults = cfg["maps"]["pipeline"]
    p.add_argument("--maps", action="store_true",
                   help="generate terrain, elevation (on the terrain) and population (on both) "
                        "maps first and grow the network "
                        "on them (lsystem); other generators get them for reference only "
                        f"({', '.join(f'{k}: {v}' for k, v in defaults.items())})")
    for kind in MAP_KINDS:
        p.add_argument(f"--{kind}", choices=map_generator_names(kind), metavar="NAME",
                       help=f"generate a {kind} map with this generator (lsystem grows on it) "
                            f"({', '.join(map_generator_names(kind))}); overrides --maps")
    p.add_argument("--map-set", action="append", metavar="KIND.KEY=VALUE",
                   help="override one map parameter (repeatable), e.g. --map-set terrain.rivers=2")


def _add_manhattan_args(p: argparse.ArgumentParser, d: dict) -> None:
    """Flags default to None so a preset (or the configured default) applies.

    d holds the configured defaults, only used to show them in --help.
    """
    p.add_argument("--preset", choices=sorted(PRESETS), help="start from a named parameter set")
    p.add_argument("--avenues", type=int, help="number of avenues (along x)")
    p.add_argument("--streets", type=int, help="number of streets (along y)")
    p.add_argument("--avenue-spacing", type=float, help=f"[m] (default {d['avenue_spacing']})")
    p.add_argument("--street-spacing", type=float, help=f"[m] (default {d['street_spacing']})")
    p.add_argument("--rotation", type=float, help=f"[deg] (default {d['rotation_deg']})")
    p.add_argument("--origin", type=float, nargs=2, metavar=("X", "Y"))
    p.add_argument("--avenue-lanes", type=int, nargs=2, metavar=("FWD", "BWD"),
                   help=f"(default {d['avenue_lanes']})")
    p.add_argument("--street-lanes", type=int, nargs=2, metavar=("FWD", "BWD"),
                   help=f"(default {d['street_lanes']})")
    p.add_argument("--lane-width", type=float, help=f"[m] (default {d['lane_width']})")
    p.add_argument("--avenue-speed", type=float, help=f"[m/s] (default {d['avenue_speed']})")
    p.add_argument("--street-speed", type=float, help=f"[m/s] (default {d['street_speed']})")
    p.add_argument("--jitter", type=float, help=f"node noise [m] (default {d['jitter']})")
    p.add_argument("--edge-drop", type=float,
                   help=f"probability of dropping each street segment (default {d['edge_drop_prob']})")
    p.add_argument("--avenue-oneway", choices=ONEWAY_PATTERNS,
                   help=f"(default {d['avenue_oneway_pattern']})")
    p.add_argument("--street-oneway", choices=ONEWAY_PATTERNS,
                   help=f"(default {d['street_oneway_pattern']})")
    p.add_argument("--boundary", choices=BOUNDARY_MODES,
                   help=f"(default {d['boundary_mode']})")
    p.add_argument("--ring-offset", type=float, help=f"[m] (default {d['ring_offset']})")
    p.add_argument("--ring-lanes", type=int, nargs=2, metavar=("FWD", "BWD"),
                   help=f"(default {d['ring_lanes']})")


# Commands that only report: never recorded in the history.
NOT_RECORDED = frozenset({"generators", "maps", "history", "replay"})
HASH_ARG = re.compile(r"[0-9a-fA-F]{4,64}")
GLOBAL_VALUE_FLAGS = ("--note",)  # global flags that take a value


def history_of(cfg: dict) -> Optional[History]:
    """The run history from config (history.file) or $ROADGEN_HISTORY; None if off."""
    return History.from_env(cfg["history"]["file"])


def cmd_history(args: argparse.Namespace) -> int:
    history = history_of(args.cfg)
    if history is None:
        log.info("run history is off")
        return 0
    runs = history.runs()
    if args.grep:
        runs = [r for r in runs if args.grep in r.note or args.grep in r.command_line()]
    for r in runs:
        print(f"{r.hash}  {r.note}" if r.note else r.hash)
        print(f"    {r.command_line()}")
    log.info("%d runs in %s", len(runs), history.path)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    history = history_of(args.cfg)
    if history is None:
        raise SystemExit("error: run history is off")
    try:
        run = history.get(args.hash)
    except (KeyError, ValueError) as e:
        raise SystemExit(f"error: {e.args[0]}") from e
    if args.show:
        print(run.command_line())
        if run.config_file:
            print(f"(config file {run.config_file} at the time; its content is in the history)")
        return 0
    log.info("replaying %s: %s", run.hash, run.command_line())
    # Exactly the recorded configuration; keys added to defaults.yaml since then come from it.
    return main(run.argv, merge(load_config(), run.config), expected_hash=run.hash)


def _bare_hash(argv: list[str], commands: Sequence[str]) -> list[str]:
    """`cli HASH ...` -> `cli replay HASH ...`."""
    k = 0
    while k < len(argv) and argv[k].startswith("-"):
        k += 2 if argv[k] in GLOBAL_VALUE_FLAGS else 1
    if k < len(argv) and argv[k] not in commands and HASH_ARG.fullmatch(argv[k]):
        return [*argv[:k], "replay", *argv[k:]]
    return argv


def _record(args: argparse.Namespace, argv: list[str], config_file: Optional[str],
            expected_hash: Optional[str]) -> None:
    history = history_of(args.cfg)
    if history is None or args.no_history or args.command in NOT_RECORDED:
        return
    # Store the command without the flags that don't change its result.
    strip = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    strip.add_argument("-v", "--verbose", action="store_true")
    strip.add_argument("--note")
    strip.add_argument("--no-history", action="store_true")
    _, argv = strip.parse_known_args(argv)
    try:
        h, new = history.record(argv, run_params(vars(args)), args.cfg, config_file, args.note)
    except (OSError, ValueError) as e:
        log.warning("run history: %s", e)
        return
    if expected_hash is not None and h != expected_hash:
        log.warning("inputs differ from run %s (defaults.yaml gained keys since); recorded as %s",
                    expected_hash, h)
    log.info("run %s %s %s", h, "recorded in" if new else "already in", history.path)


def main(argv: Optional[Sequence[str]] = None, config: Optional[dict] = None,
         expected_hash: Optional[str] = None) -> int:
    """Run one command. config replaces defaults.yaml + --config (used by replay)."""
    # Read --config first: the parser's defaults (and --help) depend on it.
    pre = argparse.ArgumentParser(add_help=False)
    add_config_arg(pre)
    known, rest = pre.parse_known_args(list(sys.argv[1:] if argv is None else argv))
    if config is None:
        try:
            config = load_config(known.config_file)
        except (OSError, ValueError, yaml.YAMLError) as e:
            raise SystemExit(f"error: --config: {e}") from e
    parser = build_parser(config)
    commands = next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    rest = _bare_hash(rest, list(commands))
    args = parser.parse_args(rest)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    rc = args.func(args)
    if rc == 0:
        _record(args, rest, known.config_file, expected_hash)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
