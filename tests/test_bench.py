from __future__ import annotations

import csv
import dataclasses
import sys
from pathlib import Path

import pytest

from roadgen.bench import benchmark
from roadgen.bench.benchmark import COLUMNS, BenchConfig, read_done, run_sweep
from roadgen.bench.plots import crossing, plot_all, power_law
from roadgen.sim.runner import SimResult, run_monitored


def write_config(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(text.replace("OUT", str(tmp_path / "bench")))
    return p


SMALL = """
grid_sizes: [[2, 2], [3, 3]]
vehicle_counts: [3, 6, 100000]
seeds: [1, 2]
sim_duration: 5
out_dir: OUT
"""


def fake_runner(calls: list, wall: float = 2.0, load: float = 0.5, log_load: bool = True, **overrides):
    """Stand-in for run_esmini: full runs take `wall` s, of which `load` s loading.

    With log_load=False the full run can't report its load time, so the
    benchmark has to fall back to a probe run (which takes `load` s).
    """
    def run(xosc, **kw):
        calls.append((Path(xosc).name, kw))
        probe = Path(xosc).stem.endswith("_load")
        result = SimResult(
            wall_time=load if probe else wall, sim_time=0.05 if probe else 5.0,
            real_time_factor=1.0, n_vehicles=0, errors=[], log_path=Path(xosc),
            peak_rss_mb=40.0, load_time=load if (log_load and not probe) else None,
        )
        return dataclasses.replace(result, **(overrides if not probe else {}))
    return run


def rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# --- config -----------------------------------------------------------------------------


def test_config_from_yaml(tmp_path):
    cfg = BenchConfig.from_yaml(write_config(tmp_path, SMALL))
    assert cfg.grid_sizes == [(2, 2), (3, 3)]
    assert len(list(cfg.combinations())) == 2 * 3 * 2
    assert cfg.network_params(4, 5).n_avenues == 4


def test_config_rejects_unknown_keys(tmp_path):
    with pytest.raises(ValueError, match="unknown config keys"):
        BenchConfig.from_yaml(write_config(tmp_path, SMALL + "vehicles_per_lane: 3\n"))


def test_config_network_preset(tmp_path):
    cfg = BenchConfig.from_yaml(write_config(tmp_path, SMALL + "network: {preset: manhattan_like}\n"))
    p = cfg.network_params(3, 4)
    assert (p.n_avenues, p.n_streets, p.boundary_mode) == (3, 4, "loop")


def test_config_id_tracks_fixed_parameters(tmp_path):
    a = BenchConfig.from_yaml(write_config(tmp_path, SMALL))
    assert a.config_id == dataclasses.replace(a, seeds=[9], timeout_s=1).config_id
    assert a.config_id != dataclasses.replace(a, sim_duration=6).config_id
    assert a.config_id != dataclasses.replace(a, network={"jitter": 1.0}).config_id


# --- sweep logic (fake esmini) ------------------------------------------------------------


def test_sweep_records_skips_and_resumes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(benchmark, "run_esmini", fake_runner(calls))
    cfg = BenchConfig.from_yaml(write_config(tmp_path, SMALL))
    results = run_sweep(cfg)

    data = rows(results)
    assert len(data) == 12
    assert list(data[0]) == COLUMNS
    skipped = [r for r in data if r["status"] == "skipped"]
    assert {r["n_vehicles"] for r in skipped} == {"100000"}
    assert all("capacity" in r["error"] for r in skipped)
    ok = [r for r in data if r["status"] == "ok"]
    assert len(ok) == 8
    r = ok[0]
    assert float(r["load_time_s"]) == 0.5 and float(r["wall_time_s"]) == 2.0
    assert r["load_method"] == "log"
    assert float(r["sim_wall_time_s"]) == pytest.approx(1.5)
    assert float(r["rtf"]) == pytest.approx(5.0 / 1.5, rel=1e-5)  # CSV keeps 6 digits
    assert float(r["rtf_total"]) == pytest.approx(5.0 / 2.0, rel=1e-5)
    assert int(r["n_junctions"]) > 0 and int(r["xodr_size_bytes"]) > 0
    assert len(calls) == 8  # one esmini run per ok row

    # Resume: nothing left to do, nothing appended.
    calls.clear()
    run_sweep(cfg)
    assert calls == [] and len(rows(results)) == 12

    # Extending the sweep only runs the new combinations.
    run_sweep(dataclasses.replace(cfg, seeds=[1, 2, 3]))
    assert len(rows(results)) == 18
    assert len(calls) == 4


def test_networks_are_built_once_per_grid_and_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "run_esmini", fake_runner([]))
    cfg = BenchConfig.from_yaml(write_config(tmp_path, SMALL))
    run_sweep(cfg)
    nets = sorted(p.name for p in (tmp_path / "bench" / "networks").glob("*.xodr"))
    assert len(nets) == 4  # 2 grids x 2 seeds


@pytest.mark.parametrize("killed", ["timeout", "memory"])
def test_killed_runs_are_recorded(tmp_path, monkeypatch, killed):
    monkeypatch.setattr(benchmark, "run_esmini", fake_runner([], killed=killed, errors=[f"{killed}!"]))
    cfg = dataclasses.replace(BenchConfig.from_yaml(write_config(tmp_path, SMALL)), vehicle_counts=[3])
    data = rows(run_sweep(cfg))
    assert {r["status"] for r in data} == {killed}
    assert all(r["error"] for r in data)


def test_larger_runs_after_a_kill_are_skipped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(benchmark, "run_esmini", fake_runner(calls, killed="timeout", errors=["t"]))
    cfg = dataclasses.replace(BenchConfig.from_yaml(write_config(tmp_path, SMALL)),
                              grid_sizes=[(2, 2)], seeds=[1], vehicle_counts=[6, 3])
    data = rows(run_sweep(cfg))
    assert [(r["n_vehicles"], r["status"]) for r in data] == [("3", "timeout"), ("6", "skipped")]
    assert "dominated" in data[1]["error"]
    assert len(calls) == 1

    # The rule survives a resume: a new, larger count is skipped without running.
    calls.clear()
    data = rows(run_sweep(dataclasses.replace(cfg, vehicle_counts=[3, 6, 9])))
    assert data[-1]["n_vehicles"] == "9" and data[-1]["status"] == "skipped"
    assert calls == []


def test_exceptions_are_recorded_not_raised(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("esmini exploded")

    monkeypatch.setattr(benchmark, "run_esmini", boom)
    cfg = dataclasses.replace(BenchConfig.from_yaml(write_config(tmp_path, SMALL)), vehicle_counts=[3])
    data = rows(run_sweep(cfg))
    assert {r["status"] for r in data} == {"failed"}
    assert "esmini exploded" in data[0]["error"]


def test_probe_fallback_when_log_has_no_load_time(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(benchmark, "run_esmini", fake_runner(calls, log_load=False))
    cfg = dataclasses.replace(BenchConfig.from_yaml(write_config(tmp_path, SMALL)),
                              vehicle_counts=[3], seeds=[1], grid_sizes=[(2, 2)])
    (r,) = rows(run_sweep(cfg))
    assert r["load_method"] == "probe" and float(r["load_time_s"]) == 0.5
    assert [name.endswith("_load.xosc") for name, _ in calls] == [False, True]
    assert float(r["rtf"]) == pytest.approx(5.0 / 1.5, rel=1e-5)


def test_unresolvable_rtf_is_a_lower_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "run_esmini", fake_runner([], wall=0.505, load=0.5))
    cfg = dataclasses.replace(BenchConfig.from_yaml(write_config(tmp_path, SMALL)),
                              vehicle_counts=[3], seeds=[1], grid_sizes=[(2, 2)])
    (r,) = rows(run_sweep(cfg))
    assert r["rtf_lower_bound"] == "1"
    assert float(r["rtf"]) == pytest.approx(5.0 / benchmark.MIN_STEPPING_S)


def test_mismatched_results_file_is_refused(tmp_path):
    bad = tmp_path / "results.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="different columns"):
        read_done(bad)


# --- process monitor ----------------------------------------------------------------------


def test_monitor_timeout(tmp_path):
    run = run_monitored(["sleep", "5"], tmp_path / "o.txt", timeout=0.3)
    assert run.killed == "timeout"
    assert run.wall < 2.0


def test_monitor_memory_cap_and_peak(tmp_path):
    hog = [sys.executable, "-c", "import time; b = bytearray(300 * 2**20); time.sleep(5)"]
    run = run_monitored(hog, tmp_path / "o.txt", timeout=20, memory_cap_mb=100)
    assert run.killed == "memory"
    assert run.peak_rss_mb > 100


def test_monitor_first_line_timing(tmp_path):
    import re

    script = "import time; time.sleep(0.4); print('[0.000] go', flush=False); time.sleep(0.3)"
    run = run_monitored([sys.executable, "-c", script], tmp_path / "o.txt",
                        first_line_pattern=re.compile(rb"\[\d+\.\d+\]"))
    # Unflushed print still shows up immediately: the pty makes stdout line-buffered.
    assert 0.35 < run.first_match < 0.65
    assert 0.65 < run.wall < 1.2
    assert b"[0.000] go" in (tmp_path / "o.txt").read_bytes()


def test_monitor_peak_of_short_process(tmp_path):
    quick = [sys.executable, "-c", "b = bytearray(80 * 2**20)"]
    run = run_monitored(quick, tmp_path / "o.txt")
    assert run.killed is None and run.returncode == 0
    assert run.peak_rss_mb > 80  # from wait4 even if the poll never saw it


# --- plots ---------------------------------------------------------------------------------


def test_crossing():
    # One decade of RTF per decade of vehicles: RTF 5 at 100 -> RTF 1 at 500.
    pts = [(10, 50.0), (100, 5.0), (1000, 0.5)]
    assert crossing(pts) == pytest.approx(500.0, rel=1e-6)
    assert crossing([(10, 5.0), (100, 2.0)]) is None


def test_power_law():
    a, b = power_law([(x, 3.0 * x ** 2) for x in (10, 100, 1000)])
    assert (a, b) == (pytest.approx(3.0), pytest.approx(2.0))
    assert power_law([(5, 1.0)]) is None


def test_plot_all_writes_png_and_pdf(tmp_path):
    results = tmp_path / "results.csv"
    with open(results, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for grid, j in (("5x5", 25), ("10x10", 100)):
            for n in (10, 100, 1000):
                for seed in (1, 2):
                    rtf = 2000.0 / (n * j) * (1 + 0.05 * seed)
                    w.writerow({
                        "config_id": "x", "grid": grid, "n_avenues": grid.split("x")[0],
                        "n_streets": grid.split("x")[1], "n_vehicles": n, "seed": seed,
                        "status": "timeout" if (n, grid) == (1000, "10x10") else "ok",
                        "n_junctions": j, "xodr_size_bytes": j * 20000, "load_time_s": 0.01 * j,
                        "sim_wall_time_s": 60 / rtf, "rtf": rtf, "sim_duration": 60,
                    })
    paths = plot_all(results, tmp_path / "plots")
    names = sorted(p.name for p in paths)
    assert names == sorted(
        f"{stem}.{ext}"
        for stem in ("rtf_vs_vehicles", "load_and_size_vs_junctions", "wall_vs_work")
        for ext in ("png", "pdf")
    )
    assert all(p.stat().st_size > 1000 for p in paths)


# --- real esmini ---------------------------------------------------------------------------


def _esmini_available() -> bool:
    from roadgen.sim.esmini import esmini_binary

    try:
        esmini_binary("esmini")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found")
def test_tiny_real_sweep(tmp_path):
    cfg = BenchConfig.from_yaml(write_config(tmp_path, """
grid_sizes: [[2, 2]]
vehicle_counts: [3]
seeds: [1]
sim_duration: 5
timeout_s: 60
memory_cap_mb: 2048
out_dir: OUT
"""))
    (r,) = rows(run_sweep(cfg))
    assert r["status"] == "ok", r["error"]
    assert float(r["sim_time_s"]) == pytest.approx(5.0, abs=0.1)
    assert float(r["peak_rss_mb"]) > 1.0
