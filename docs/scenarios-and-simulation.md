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

### Junctions

Vehicles get no route: esmini's default controller follows the lane and
picks a random connection at every junction. It never changes lanes, so a
vehicle reaches a left-turn pocket's movements only if it starts in the
pocket.

### Traffic lights

With a network built with HD maps, the sidecar's `signal_plans`
([lights](opendrive.md#traffic-lights-and-controllers-hd-maps)) go into the
`.xosc` twice (`--no-lights` leaves both out):

- **RoadNetwork `TrafficSignalController`** per junction: the plan's phases
  with a `TrafficSignalState` per light, `delay` = where the cycle starts.
  This is the standard form other tools read. **esmini 3.9 ignores it.**
- **Storyboard events**, the form esmini switches: one act, one event per
  state change up to `--duration`, with a `TrafficSignalStateAction` per
  light that changes (`name` = signal id) and a `SimulationTimeCondition`.
  The first event, at 0 s, sets every light.

esmini's default controller does not stop vehicles at red lights; they drive
through. The lights are there for the HD map, for rendering and for
controllers that read them.

The storyboard stops when simulation time exceeds `--duration`. The output is
deterministic for a seed, and the header date is fixed. `--scenegraph`
attaches a [3D scene](scene.md).

## Running esmini

The scenario is for looking at a network with traffic; the generated `.xodr`
is the product. `roadgen/sim/runner.py` runs esmini on it; esmini is located
by `roadgen/sim/esmini.py` through `ESMINI_HOME`.

```bash
python -m cli simulate out/<hash>/grid10x10.xosc --gui --camera orbit   # in a window
python -m cli simulate out/<hash>/grid10x10.xosc                        # headless: log only
```

`run_esmini()` builds the command line:

- `--headless`, or `--window 60 60 1280 800` with `--gui`;
- `--fixed_timestep` (0.05 s);
- `--logfile_path`;
- `--enforce_generate_model` when the scenario has a SceneGraphFile, since
  esmini would otherwise not draw the roads.

The log (`<stem>.log`) and esmini's console output (`.stdout`) go into the
run's folder. `simulate` reports the vehicles, the simulated time and the
errors and warnings from the log.

### Result (`SimResult`)

| Field | Meaning |
|-------|---------|
| `sim_time` | Last simulated timestamp [s] |
| `n_vehicles`, `returncode` | As named |
| `errors`, `warnings` | Parsed from the esmini log; a run over the time limit is an error |
| `asset_warnings` | Missing 3D model or texture messages: counted but ignored, since they only affect rendering |
| `ok` | Return code 0 and no errors |
