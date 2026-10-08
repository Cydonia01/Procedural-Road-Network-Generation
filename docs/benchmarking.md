# Scalability benchmark

`roadgen/bench/` sweeps **grid size × vehicle count × seed**, runs esmini once
per combination, and records one CSV row per run.

```bash
python -m cli bench configs/bench_smoke.yaml          # ~1 minute end-to-end check
python -m cli bench configs/bench_scalability.yaml    # the full sweep (hours)
python -m cli bench-plot out/bench/results.csv        # re-plot without re-running
```

## Config (YAML → `BenchConfig`)

| Key | Default | Meaning |
|-----|---------|---------|
| `grid_sizes` | required | List of `[n_avenues, n_streets]` |
| `vehicle_counts` | required | Run in ascending order per network |
| `seeds` | required | Network *and* scenario seed |
| `sim_duration` | 60 | Simulated seconds per run |
| `timestep` | 0.05 | esmini fixed timestep [s] |
| `spawn_mode` | `random_lanes` | or `explicit_routes` |
| `min_spawn_gap` | 10 | [m] |
| `speed_range` | [8.0, 13.9] | [m/s] |
| `timeout_s` | 900 | Per esmini run |
| `memory_cap_mb` | 8192 | Per esmini run |
| `network` | `{}` | `ManhattanParams` overrides, optionally `preset:` (e.g. `{preset: manhattan_like}`) |
| `out_dir` | `out/bench` | Overridable with `--out-dir` |

Unknown keys are rejected, and network and scenario parameters are checked
before anything runs.

## How a sweep runs

1. The **config id** is a hash of everything except the swept dimensions,
   limits and `out_dir`. Changing the network or scenario settings therefore
   starts a fresh series in the same CSV.
2. **Networks are cached** per (grid, seed) in `<out_dir>/networks/`
   (`.xodr`, sidecar and `.metrics.json`) and reused across vehicle counts
   and reruns.
3. For each vehicle count:
   - If it exceeds the lane capacity at the spawn gap, the row is `skipped`.
   - Otherwise the scenario is written to `<out_dir>/runs/` and esmini runs it
     with the timeout and memory cap, with no recording.
4. **Load time** comes from the first simulation-timestamped log line
   (`load_method=log`). If that is unavailable, a probe run whose stop
   trigger fires at t = 0 measures it instead (`load_method=probe`, which is
   noisier).
5. **RTF** = `sim_duration / stepping wall time`. RTF < 1 means slower than
   real time. Stepping times under 10 ms are below the timing resolution, so
   RTF is then recorded as a lower bound and `rtf_lower_bound=1` is set.
   `rtf_total` uses the full wall time.
6. **Domination.** Once a run on a network is killed (timeout or memory),
   larger vehicle counts on the same network are recorded as `skipped`
   without running, since they could only be worse.
7. **Resumable.** Each row is appended as soon as its run finishes, keyed by
   (config id, grid, vehicles, seed). Rerunning the same command skips rows
   already present.

A failing run never crashes the sweep. The exception is recorded as `failed`.

## `results.csv` columns

| Group | Columns |
|-------|---------|
| Key | `config_id`, `grid`, `n_avenues`, `n_streets`, `n_vehicles`, `seed` |
| Status | `status`, `error`, `started_at` |
| Network | `n_nodes`, `n_edges`, `n_junctions`, `n_connecting_roads`, `road_length_m`, `connecting_length_m`, `gen_time_s`, `build_time_s`, `xodr_size_bytes` |
| Simulation | `sim_duration`, `spawn_mode`, `timestep`, `scenario_time_s`, `load_time_s`, `load_method`, `wall_time_s`, `sim_wall_time_s`, `sim_time_s`, `rtf`, `rtf_lower_bound`, `rtf_total`, `peak_rss_mb`, `n_errors`, `n_warnings` |

`status` values:

| Status | Meaning |
|--------|---------|
| `ok` | Completed without esmini errors |
| `error` | Completed, but esmini logged errors |
| `skipped` | Vehicles don't fit at the gap, or dominated by an earlier kill |
| `timeout` / `memory` | Killed at the limit |
| `failed` | Any other exception |

A results file with different columns is refused. Move it aside or use
another `out_dir`.

## Plots (`plots.py`)

Written to `<results dir>/plots/` as PNG and PDF. They are regenerated after
every `bench` unless `--no-plot` is given.

1. **`rtf_vs_vehicles`:** real-time factor vs vehicle count, one line per
   grid size, with the RTF = 1 boundary and where each grid crosses it.
   Timeouts and memory kills appear as markers on the lower edge.
2. **`load_and_size_vs_junctions`:** esmini load time (per vehicle count) and
   `.xodr` size vs number of junctions, as two panels.
3. **`wall_vs_work`:** wall time vs vehicles × junctions, with the real-time
   boundary.

Points are means across seeds with ±1 standard deviation error bars. Only
`ok` rows are plotted.

## Getting meaningful numbers

- Run on **native Linux/x86_64**. Under Docker on Apple Silicon, esmini runs
  through Rosetta emulation, so timings and memory use don't represent native
  performance.
- Don't run other heavy work during a sweep. Wall time is the primary metric.
- Use several seeds. The plots show the variation across seeds.
