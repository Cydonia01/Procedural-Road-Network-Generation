"""Command-line entry point: `python -m cli <command> ...`."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence

import yaml

from roadgen.generators.base import generator_names, get_generator, load_params, presets_of
from roadgen.generators.manhattan import BOUNDARY_MODES, ONEWAY_PATTERNS, PRESETS, ManhattanParams
from roadgen.graph.io import load_json, save_json
from roadgen.graph.model import RoadGraph
from roadgen.graph.plot import plot_graph
from roadgen.opendrive.builder import DEFAULT_JUNCTION_MARGIN, OpenDriveBuilder
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
        builder.build(graph)
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
        return load_params(gen.params_type, args.params, args.preset, overrides)
    except TypeError as e:  # missing required fields
        if args.generator == "manhattan":
            raise SystemExit("error: --avenues and --streets are required without --preset") from e
        raise SystemExit(f"error: {e}") from e
    except ValueError as e:
        raise SystemExit(f"error: {e}") from e


def default_output(args: argparse.Namespace) -> Path:
    name = "_".join(filter(None, [args.generator, args.preset])) + f"_s{args.seed}"
    return Path("out") / name


def generate_graph(args: argparse.Namespace) -> RoadGraph:
    params = generator_params(args)
    with timed(f"generate {args.generator}"):
        graph = get_generator(args.generator).generate(params, args.seed)
    log.info("generated %d nodes, %d edges", len(graph.nodes), len(graph.edges))
    return graph


def cmd_generate(args: argparse.Namespace) -> int:
    graph = generate_graph(args)
    output = Path(args.output) if args.output else default_output(args).with_suffix(".json")
    save_json(graph, output)
    log.info("wrote %s", output)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Generate, then write <prefix>.json/.png/.xodr and run all validators."""
    prefix = Path(args.output) if args.output else default_output(args)
    graph = generate_graph(args)
    errors = graph.validate()
    for err in errors:
        log.error("graph: %s", err)
    errors += check_traffic(graph, args.allow_traps)
    save_json(graph, prefix.with_suffix(".json"))
    with timed("plot"):
        plot_graph(graph, prefix.with_suffix(".png"))
    xodr = prefix.with_suffix(".xodr")
    errors += build_and_validate(graph, xodr, args.junction_margin, args.xsd)
    scenegraph = None
    if args.buildings:
        from roadgen.scene.buildings import BuildingParams, export_buildings

        scenegraph = Path(f"{prefix}_buildings.osgt")
        with timed("buildings"):
            n = export_buildings(graph, scenegraph, BuildingParams(seed=args.seed))
        log.info("wrote %s (%d buildings)", scenegraph, n)
    if args.drive_check and not errors:
        errors += ["drive-check failed"] if cmd_drive_check(argparse.Namespace(input=str(xodr))) else []
    if args.simulate and not errors:
        errors += simulate_network(xodr, args, scenegraph)
    log.info("pipeline %s (%s)", "FAILED" if errors else "passed", prefix)
    return 1 if errors else 0


def simulate_network(xodr: Path, args: argparse.Namespace, scenegraph: Optional[Path]) -> list[str]:
    """Short headless esmini run with random traffic; returns errors."""
    from roadgen.scenario.xosc_builder import ScenarioBuilder, ScenarioParams
    from roadgen.sim.runner import run_esmini

    xosc = xodr.with_suffix(".xosc")
    params = ScenarioParams(args.sim_vehicles, args.seed, args.sim_duration)
    try:
        ScenarioBuilder.from_files(xodr).write(params, xosc, scenegraph=scenegraph)
        result = run_esmini(xosc, record=False, csv=False, timeout=600)
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
        for f in dataclasses.fields(gen.params_type):
            default = "required" if f.default is dataclasses.MISSING else repr(f.default)
            print(f"  {f.name} = {default}")
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    try:
        odrviewer = esmini_binary("odrviewer")
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    cmd = [str(odrviewer), "--odr", args.input, "--density", str(args.density)]
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

    cfg = BenchConfig.from_yaml(args.config)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli", description="Road network tools")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

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
        "--junction-margin", type=float, default=DEFAULT_JUNCTION_MARGIN,
        help="extra clearance [m] between junction center region and roads",
    )
    p.set_defaults(func=cmd_build_xodr)

    p = sub.add_parser("generators", help="list registered generators, presets and parameters")
    p.set_defaults(func=cmd_generators)

    p = sub.add_parser("generate", help="generate a RoadGraph JSON")
    gen = p.add_subparsers(dest="generator", required=True)
    for name in generator_names():
        g = gen.add_parser(name, help=get_generator(name).description)
        _add_generator_args(g, name)
        g.add_argument("-o", "--output", help="output RoadGraph JSON (default: out/<name>_s<seed>.json)")
        g.set_defaults(func=cmd_generate)

    p = sub.add_parser("pipeline", help="generate, plot, build .xodr and validate in one go")
    gen = p.add_subparsers(dest="generator", required=True)
    for name in generator_names():
        g = gen.add_parser(name, help=get_generator(name).description)
        _add_generator_args(g, name)
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
            "--junction-margin", type=float, default=DEFAULT_JUNCTION_MARGIN,
            help="extra clearance [m] between junction center region and roads",
        )
        g.add_argument(
            "--drive-check", action="store_true",
            help="also drive every junction movement with esmini's RoadManager",
        )
        g.add_argument("--buildings", action="store_true",
                       help="also export building boxes per block to <prefix>_buildings.osgt "
                            "(used as SceneGraphFile by --simulate)")
        g.add_argument("--simulate", action="store_true",
                       help="also run a short headless esmini simulation with random traffic")
        g.add_argument("--sim-vehicles", type=int, default=50)
        g.add_argument("--sim-duration", type=float, default=30.0, help="[s]")
        g.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("scenario", help="generate an OpenSCENARIO file with many vehicles")
    p.add_argument("input", help=".xodr written by build-xodr/pipeline (needs its .roadmap.json)")
    p.add_argument("-o", "--output", required=True, help="output .xosc")
    p.add_argument("--vehicles", type=int, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--duration", type=float, default=60.0, help="simulated time [s]")
    p.add_argument(
        "--mode", choices=("random_lanes", "explicit_routes"), default="random_lanes",
        help="random junction choices, or shortest-path routes to random destinations",
    )
    p.add_argument("--speed-min", type=float, default=8.0, help="[m/s]")
    p.add_argument("--speed-max", type=float, default=13.9, help="[m/s]")
    p.add_argument("--min-gap", type=float, default=10.0, help="min spawn gap in a lane [m]")
    p.add_argument("--graph", help="RoadGraph JSON (default: <input>.json if present)")
    p.add_argument("--scenegraph", help="3D model to show with the roads, e.g. <prefix>_buildings.osgt")
    p.set_defaults(func=cmd_scenario)

    p = sub.add_parser("simulate", help="run an .xosc in esmini and summarize the run")
    p.add_argument("input", help=".xosc file")
    p.add_argument("--gui", action="store_true", help="show the esmini window")
    p.add_argument(
        "--camera", default="top",
        help="camera mode with --gui (orbit, fixed, flex, flex-orbit, top, driver)",
    )
    p.add_argument("--timestep", type=float, default=0.05, help="fixed timestep [s]")
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
    p.add_argument("--density", type=float, default=1, help="cars per 100 m")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser(
        "drive-check", help="drive every junction movement with esmini's RoadManager"
    )
    p.add_argument("input", help=".xodr file written by build-xodr (needs its .roadmap.json)")
    p.set_defaults(func=cmd_drive_check)

    return parser


def _add_generator_args(p: argparse.ArgumentParser, name: str) -> None:
    """Flags shared by every generator, plus Manhattan's dedicated flags."""
    gen = get_generator(name)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--params", help="YAML with parameter values (or preset: + params:)")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="override one parameter (repeatable), e.g. --set rotation_deg=15")
    if name == "manhattan":
        _add_manhattan_args(p)
    else:
        presets = presets_of(gen.params_type)
        p.add_argument("--preset", choices=presets or None, help="start from a named parameter set")


def _add_manhattan_args(p: argparse.ArgumentParser) -> None:
    """Flags default to None so a preset (or the dataclass default) applies."""
    d = ManhattanParams(1, 2)  # only used to show defaults in --help
    p.add_argument("--preset", choices=sorted(PRESETS), help="start from a named parameter set")
    p.add_argument("--avenues", type=int, help="number of avenues (along x)")
    p.add_argument("--streets", type=int, help="number of streets (along y)")
    p.add_argument("--avenue-spacing", type=float, help=f"[m] (default {d.avenue_spacing})")
    p.add_argument("--street-spacing", type=float, help=f"[m] (default {d.street_spacing})")
    p.add_argument("--rotation", type=float, help=f"[deg] (default {d.rotation_deg})")
    p.add_argument("--origin", type=float, nargs=2, metavar=("X", "Y"))
    p.add_argument("--avenue-lanes", type=int, nargs=2, metavar=("FWD", "BWD"),
                   help=f"(default {d.avenue_lanes})")
    p.add_argument("--street-lanes", type=int, nargs=2, metavar=("FWD", "BWD"),
                   help=f"(default {d.street_lanes})")
    p.add_argument("--lane-width", type=float, help=f"[m] (default {d.lane_width})")
    p.add_argument("--avenue-speed", type=float, help=f"[m/s] (default {d.avenue_speed})")
    p.add_argument("--street-speed", type=float, help=f"[m/s] (default {d.street_speed})")
    p.add_argument("--jitter", type=float, help="node noise [m] (default 0)")
    p.add_argument("--edge-drop", type=float, help="probability of dropping each street segment")
    p.add_argument("--avenue-oneway", choices=ONEWAY_PATTERNS, help="(default none)")
    p.add_argument("--street-oneway", choices=ONEWAY_PATTERNS, help="(default none)")
    p.add_argument("--boundary", choices=BOUNDARY_MODES, help="(default dead_end)")
    p.add_argument("--ring-offset", type=float, help=f"[m] (default {d.ring_offset})")
    p.add_argument("--ring-lanes", type=int, nargs=2, metavar=("FWD", "BWD"),
                   help=f"(default {d.ring_lanes})")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
