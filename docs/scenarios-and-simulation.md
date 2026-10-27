# Scenarios and simulation

## Scenario builder

`roadgen/scenario/xosc_builder.py` writes an OpenSCENARIO file that fills a
network with many vehicles. It needs the `.xodr` and its
[`.roadmap.json`](opendrive.md#the-roadmapjson-sidecar).

```bash
python -m cli scenario out/<hash>/grid10x10.xodr --vehicles 200 --seed 1 --duration 120
```

Defaults are in `defaults.yaml` under `scenario`, and every option is listed
in the [CLI reference](cli-reference.md#scenario).

### Spawning

- **Where.** Vehicles spawn on driving lanes of normal roads, never inside
  junctions, and at least 5 m from road ends. A stretch qualifies only where
  the lane has full, constant width and runs on to the road's end in its
  travel direction. That rules out lane tapers and lanes that are dropped
  further on.
- **Lane choice.** The lane is drawn at random, weighted by its usable length.
  The position is uniform along it.
- **Gaps.** Each vehicle keeps at least `--min-gap` (10 m) to any other in the
  same lane. If a vehicle can't be placed after 200 tries, the builder raises
  an error suggesting fewer vehicles or a smaller gap.
- **Speed.** Each vehicle gets a constant target speed from
  `--speed-min`..`--speed-max` (8–13.9 m/s), set as a step at t = 0.
- **Vehicle.** A generic car bounding box (4.5 × 1.8 × 1.5 m). esmini draws
  a box or a default model.

### Spawn modes

| Mode | Behaviour |
|------|-----------|
| `random_lanes` (default) | No route. esmini's default controller follows the lane and picks a random connection at every junction |
| `explicit_routes` | Each vehicle gets the shortest path (by road length) to a random reachable destination, as waypoints |

esmini's default controller never changes lanes, so routes are planned on the
**lane-level movement graph** built from the sidecar. Every step is drivable
from the lane the vehicle is actually in. With `explicit_routes`, vehicles
spawn only on lanes that run the whole road and have a way on, since esmini's
route planner reports errors for lanes missing in some lane section.

The storyboard stops when simulation time exceeds `--duration`. The output is
deterministic for a seed, and the header date is fixed. `--scenegraph`
attaches a [3D scene](scene.md).

## Running esmini

`roadgen/sim/runner.py`; esmini is located by `roadgen/sim/esmini.py`
through `ESMINI_HOME`.

```bash
python -m cli simulate out/<hash>/grid10x10.xosc                  # headless
python -m cli simulate out/<hash>/grid10x10.xosc --gui --camera orbit
```

`run_esmini()` builds the command line:

- `--headless`, or `--window 60 60 1280 800` with `--gui`;
- `--fixed_timestep` (0.05 s);
- `--logfile_path`, plus `--record` (`.dat`) and `--csv_logger` (`.csv`)
  unless `--no-record` / `--no-csv`;
- `--enforce_generate_model` when the scenario has a SceneGraphFile, since
  esmini would otherwise not draw the roads.

The outputs `<stem>.log`, `.stdout`, `.dat` and `.csv` go into the run's
folder.

### Monitoring

`run_monitored()` runs esmini as a child process and:

- **samples peak RSS** of esmini and its children every 20 ms with `psutil`.
  It also reads the kernel's exact peak from `wait4` rusage, so very short
  runs are measured too;
- **enforces a timeout and a memory cap.** The child is killed and the run
  reported as `killed="timeout"` or `"memory"`. On Linux, the cap is also set
  as a kernel `RLIMIT_AS` limit. `pipeline --simulate` uses a 600 s timeout;
- **measures load time.** The child writes to a pseudo-terminal, which makes
  its output line-buffered. The first simulation-timestamped log line
  (`[0.050] …`) marks the end of loading.

`simulate` logs all of this. The load time, stepping real-time factor and
peak memory show what one network and vehicle count cost esmini, which bounds
how fast it can be used for training.

### Result (`SimResult`)

| Field | Meaning |
|-------|---------|
| `wall_time`, `load_time`, `step_wall_time` | Seconds: total, until the first step, stepping only |
| `sim_time`, `real_time_factor` | Last simulated timestamp, and `sim_time / wall_time` |
| `n_vehicles`, `peak_rss_mb`, `returncode`, `killed` | As named |
| `errors`, `warnings` | Parsed from the esmini log |
| `asset_warnings` | Missing 3D model or texture messages: counted but ignored, since they only affect rendering |
| `ok` | Return code 0, no errors, not killed |
