"""Scalability sweep: grid size x vehicle count x seed, one CSV row per run.

For every (grid, seed) the network is generated and built once and cached
under <out_dir>/networks together with its metrics. For every vehicle count
a scenario is written and esmini runs it once.

Load time (time to first step) is the wall-clock moment esmini prints its
first simulation-timestamped log line, read live through a pseudo-terminal
(load_method "log"). Where that is unavailable, a probe run of the same
scenario with its stop trigger at t = 0 is used instead (load_method
"probe"); subtracting two runs is much noisier, especially for large
networks whose load takes minutes.

The stepping wall time is the full run minus the load time, and the
real-time factor (RTF) is sim_duration / stepping wall time; RTF < 1 means
slower than real time. rtf_total uses the full wall time instead. Stepping
times below MIN_STEPPING_S are below the timing resolution, so RTF is then
recorded as the lower bound sim_duration / MIN_STEPPING_S and flagged in
rtf_lower_bound.

Rows are appended as soon as each run finishes and are keyed by the config
hash, grid, vehicle count and seed, so an interrupted sweep resumes where it
stopped. Combinations whose vehicles don't fit are recorded as "skipped",
runs killed by the timeout or memory cap as "timeout" / "memory", and any
other exception as "failed" - the sweep itself never crashes on one run.
Once a network's run is killed, larger vehicle counts on that same network
can only take longer or use more memory, so they are recorded as "skipped"
(dominated) instead of spending another full timeout each.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional, Union

import yaml
from lxml import etree

from roadgen.generators.manhattan import ManhattanGenerator, ManhattanParams
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.scenario.xosc_builder import END_MARGIN, ScenarioBuilder, ScenarioParams
from roadgen.sim.runner import run_esmini

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

COLUMNS = [
    # key
    "config_id", "grid", "n_avenues", "n_streets", "n_vehicles", "seed",
    # status
    "status", "error", "started_at",
    # network
    "n_nodes", "n_edges", "n_junctions", "n_connecting_roads", "road_length_m",
    "connecting_length_m", "gen_time_s", "build_time_s", "xodr_size_bytes",
    # scenario + simulation
    "sim_duration", "spawn_mode", "timestep", "scenario_time_s", "load_time_s", "load_method",
    "wall_time_s", "sim_wall_time_s", "sim_time_s", "rtf", "rtf_lower_bound", "rtf_total",
    "peak_rss_mb", "n_errors", "n_warnings",
]
KEY = ("config_id", "n_avenues", "n_streets", "n_vehicles", "seed")
MIN_STEPPING_S = 0.01  # [s] below this, stepping time is under the timing resolution


@dataclass(frozen=True)
class BenchConfig:
    grid_sizes: list[tuple[int, int]]
    vehicle_counts: list[int]
    seeds: list[int]
    sim_duration: float = 60.0
    timestep: float = 0.05
    spawn_mode: str = "random_lanes"
    min_spawn_gap: float = 10.0
    speed_range: tuple[float, float] = (8.0, 13.9)
    timeout_s: float = 900.0  # per esmini run
    memory_cap_mb: float = 8192.0  # per esmini run
    network: dict[str, Any] = field(default_factory=dict)  # ManhattanParams overrides / preset
    out_dir: str = "out/bench"

    @classmethod
    def from_yaml(cls, path: PathLike) -> "BenchConfig":
        data = yaml.safe_load(Path(path).read_text()) or {}
        unknown = set(data) - {f for f in cls.__dataclass_fields__}
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        data["grid_sizes"] = [tuple(g) for g in data["grid_sizes"]]
        if "speed_range" in data:
            data["speed_range"] = tuple(data["speed_range"])
        cfg = cls(**data)
        cfg.network_params(*cfg.grid_sizes[0])  # fail early on bad network params
        ScenarioParams(1, 0, cfg.sim_duration, cfg.spawn_mode, cfg.speed_range, cfg.min_spawn_gap)
        return cfg

    def network_params(self, n_avenues: int, n_streets: int) -> ManhattanParams:
        net = dict(self.network)
        preset = net.pop("preset", None)
        for key in ("avenue_lanes", "street_lanes", "ring_lanes", "origin"):
            if key in net:
                net[key] = tuple(net[key])
        net.update(n_avenues=n_avenues, n_streets=n_streets)
        return ManhattanParams.preset(preset, **net) if preset else ManhattanParams(**net)

    @property
    def config_id(self) -> str:
        """Hash of everything except the swept dimensions (and limits/out_dir)."""
        fixed = {
            "network": self.network, "sim_duration": self.sim_duration,
            "timestep": self.timestep, "spawn_mode": self.spawn_mode,
            "min_spawn_gap": self.min_spawn_gap, "speed_range": list(self.speed_range),
        }
        return hashlib.sha1(json.dumps(fixed, sort_keys=True).encode()).hexdigest()[:10]

    def combinations(self) -> Iterator[tuple[tuple[int, int], int, int]]:
        """(grid, seed, n_vehicles), grouped so each network is built once,
        with vehicle counts ascending so dominated runs can be skipped."""
        for grid in self.grid_sizes:
            for seed in self.seeds:
                for n in sorted(self.vehicle_counts):
                    yield grid, seed, n


# --- results file ---------------------------------------------------------------------


def row_key(row: dict) -> tuple:
    return tuple(str(row[k]) for k in KEY)


def read_done(results: Path) -> set[tuple]:
    if not results.is_file():
        return set()
    with open(results, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(
                f"{results} has different columns; move it aside or use another out_dir"
            )
        return {row_key(r) for r in reader}


def append_row(results: Path, row: dict) -> None:
    new = not results.is_file()
    with open(results, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow({k: _fmt(row.get(k, "")) for k in COLUMNS})


def _fmt(v: Any) -> Any:
    return f"{v:.6g}" if isinstance(v, float) else v


# --- networks ---------------------------------------------------------------------------


def network_metrics(xodr: Path, builder: Optional[OpenDriveBuilder] = None) -> dict:
    root = etree.parse(str(xodr)).getroot()
    normal = connecting = 0.0
    n_conn = 0
    for r in root.findall("road"):
        length = float(r.get("length"))
        if r.get("junction", "-1") == "-1":
            normal += length
        else:
            connecting += length
            n_conn += 1
    return {
        "n_junctions": len(root.findall("junction")),
        "n_connecting_roads": n_conn,
        "road_length_m": normal,
        "connecting_length_m": connecting,
        "xodr_size_bytes": xodr.stat().st_size,
    }


def ensure_network(cfg: BenchConfig, grid: tuple[int, int], seed: int, out: Path) -> tuple[Path, dict]:
    """Build (or reuse) the network for grid/seed; returns (xodr path, metrics)."""
    a, s = grid
    stem = out / "networks" / f"{cfg.config_id}_{a}x{s}_s{seed}"
    xodr, metrics_path = stem.with_suffix(".xodr"), stem.with_suffix(".metrics.json")
    if xodr.is_file() and metrics_path.is_file():
        return xodr, json.loads(metrics_path.read_text())

    params = cfg.network_params(a, s)
    t0 = time.perf_counter()
    graph = ManhattanGenerator().generate(params, seed)
    t1 = time.perf_counter()
    builder = OpenDriveBuilder(name=graph.metadata["name"])
    builder.build(graph)
    t2 = time.perf_counter()
    builder.write(xodr)
    metrics = {
        "n_nodes": len(graph.nodes),
        "n_edges": len(graph.edges),
        "gen_time_s": t1 - t0,
        "build_time_s": t2 - t1,
        **network_metrics(xodr),
    }
    metrics_path.write_text(json.dumps(metrics, indent=2))
    log.info("network %dx%d seed %d: %d junctions, %.1f km, built in %.2f s",
             a, s, seed, metrics["n_junctions"], metrics["road_length_m"] / 1000, metrics["build_time_s"])
    return xodr, metrics


def lane_capacity(builder: ScenarioBuilder, gap: float) -> int:
    """Upper bound on vehicles that fit with the spawn gap (perfect packing)."""
    total = 0
    for sl in builder.slots:
        usable = sl.length - 2 * END_MARGIN
        if usable > 0:
            total += int(usable // gap) + 1 if gap > 0 else 10**9
    return total


def probe_scenario(xosc: Path) -> Path:
    """Copy of xosc whose stop trigger fires immediately (load-time probe)."""
    tree = etree.parse(str(xosc))
    for cond in tree.getroot().iterfind(".//StopTrigger//SimulationTimeCondition"):
        cond.set("value", "0")
    probe = xosc.with_name(xosc.stem + "_load.xosc")
    tree.write(str(probe), xml_declaration=True, encoding="utf-8")
    return probe


# --- sweep --------------------------------------------------------------------------------


def run_one(cfg: BenchConfig, grid: tuple[int, int], seed: int, n: int, out: Path) -> dict:
    a, s = grid
    row: dict[str, Any] = {
        "config_id": cfg.config_id, "grid": f"{a}x{s}", "n_avenues": a, "n_streets": s,
        "n_vehicles": n, "seed": seed, "sim_duration": cfg.sim_duration,
        "spawn_mode": cfg.spawn_mode, "timestep": cfg.timestep,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        xodr, metrics = ensure_network(cfg, grid, seed, out)
        row.update(metrics)

        sb = ScenarioBuilder.from_files(xodr)
        cap = lane_capacity(sb, cfg.min_spawn_gap)
        if n > cap:
            reason = f"{n} vehicles exceed lane capacity {cap} at min_spawn_gap {cfg.min_spawn_gap} m"
            log.warning("skip %s n=%d seed=%d: %s", row["grid"], n, seed, reason)
            return {**row, "status": "skipped", "error": reason}
        params = ScenarioParams(n, seed, cfg.sim_duration, cfg.spawn_mode, cfg.speed_range, cfg.min_spawn_gap)
        xosc = out / "runs" / f"{cfg.config_id}_{a}x{s}_n{n}_s{seed}.xosc"
        t0 = time.perf_counter()
        try:
            sb.write(params, xosc)
        except ValueError as e:  # random placement couldn't reach n
            log.warning("skip %s n=%d seed=%d: %s", row["grid"], n, seed, e)
            return {**row, "status": "skipped", "error": str(e)}
        row["scenario_time_s"] = time.perf_counter() - t0

        limits = dict(timeout=cfg.timeout_s, memory_cap_mb=cfg.memory_cap_mb, record=False, csv=False)
        full = run_esmini(xosc, timestep=cfg.timestep, **limits)
        load, method, probe_errors = full.load_time, "log", []
        if load is None and not full.killed:
            probe = run_esmini(probe_scenario(xosc), timestep=cfg.timestep, **limits)
            load, method, probe_errors = probe.wall_time, "probe", probe.errors
        stepping = full.wall_time - load if load is not None else None
        row.update(
            load_time_s=load if load is not None else "",
            load_method=method if load is not None else "",
            wall_time_s=full.wall_time,
            sim_time_s=full.sim_time,
            rtf_total=full.sim_time / full.wall_time if full.wall_time > 0 else "",
            peak_rss_mb=full.peak_rss_mb,
            n_errors=len(full.errors),
            n_warnings=len(full.warnings),
        )
        if stepping is not None:
            row.update(
                sim_wall_time_s=max(stepping, 0.0),
                rtf=cfg.sim_duration / max(stepping, MIN_STEPPING_S),
                rtf_lower_bound=int(stepping < MIN_STEPPING_S),
            )
        if full.killed:
            return {**row, "status": full.killed, "error": "; ".join(full.errors)}
        if full.errors or probe_errors:
            return {**row, "status": "error", "error": "; ".join((full.errors + probe_errors)[:5])}
        return {**row, "status": "ok", "error": ""}
    except Exception as e:  # record, never crash the sweep
        log.error("run %s n=%d seed=%d failed: %s", row["grid"], n, seed, e)
        tb = traceback.format_exc().strip().splitlines()[-1]
        return {**row, "status": "failed", "error": f"{type(e).__name__}: {e} ({tb})"}


def _killed_runs(results: Path, config_id: str) -> dict:
    """(grid, seed) -> (smallest killed vehicle count, status) from earlier rows."""
    killed: dict = {}
    if not results.is_file():
        return killed
    with open(results, newline="") as f:
        for r in csv.DictReader(f):
            if r["config_id"] == config_id and r["status"] in ("timeout", "memory"):
                net = ((int(r["n_avenues"]), int(r["n_streets"])), int(r["seed"]))
                n = int(r["n_vehicles"])
                if net not in killed or n < killed[net][0]:
                    killed[net] = (n, r["status"])
    return killed


def _dominated_row(cfg: BenchConfig, grid, seed: int, n: int, first_n: int, status: str) -> dict:
    a, s = grid
    return {
        "config_id": cfg.config_id, "grid": f"{a}x{s}", "n_avenues": a, "n_streets": s,
        "n_vehicles": n, "seed": seed, "status": "skipped",
        "error": f"dominated: the run with {first_n} vehicles on this network hit the {status} limit",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "sim_duration": cfg.sim_duration, "spawn_mode": cfg.spawn_mode, "timestep": cfg.timestep,
    }


def run_sweep(cfg: BenchConfig, results: Optional[PathLike] = None) -> Path:
    out = Path(cfg.out_dir)
    (out / "networks").mkdir(parents=True, exist_ok=True)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    results = Path(results) if results else out / "results.csv"
    done = read_done(results)
    killed_at = _killed_runs(results, cfg.config_id)
    (out / "config.json").write_text(json.dumps(asdict(cfg), indent=2, default=list))

    combos = list(cfg.combinations())
    todo = [c for c in combos if row_key({
        "config_id": cfg.config_id, "n_avenues": c[0][0], "n_streets": c[0][1],
        "n_vehicles": c[2], "seed": c[1]}) not in done]
    log.info("sweep %s: %d combinations, %d already in %s, %d to run",
             cfg.config_id, len(combos), len(combos) - len(todo), results, len(todo))
    for k, (grid, seed, n) in enumerate(todo, 1):
        t0 = time.perf_counter()
        net = (grid, seed)
        if net in killed_at and n >= killed_at[net][0]:
            first_n, status = killed_at[net]
            row = _dominated_row(cfg, grid, seed, n, first_n, status)
            log.warning("skip %s n=%d seed=%d: %s", row["grid"], n, seed, row["error"])
        else:
            row = run_one(cfg, grid, seed, n, out)
            if row["status"] in ("timeout", "memory"):
                killed_at.setdefault(net, (n, row["status"]))
        append_row(results, row)
        rtf = row.get("rtf")
        log.info(
            "[%d/%d] %s n=%d seed=%d: %s%s (%.1f s)", k, len(todo), row["grid"], n, seed,
            row["status"], f", RTF {rtf:.2f}" if isinstance(rtf, float) else "",
            time.perf_counter() - t0,
        )
    return results
