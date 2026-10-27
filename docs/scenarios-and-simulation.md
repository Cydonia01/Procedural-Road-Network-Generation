# Scenarios & simulation

## Scenario builder (`roadgen/scenario/xosc_builder.py`)

Builds an OpenSCENARIO file that fills a network written by the OpenDRIVE
builder with many vehicles. It needs the `.xodr` and its `.roadmap.json`.

```bash
python -m cli scenario out/<hash>/grid10x10.xodr --vehicles 200 --seed 1 --duration 120 \
    -o grid10x10.xosc
```

`out/<hash>/` stands for the folder of the run that wrote a file; this run
writes `grid10x10.xosc` into its own folder.

### Spawning

- Vehicles spawn on **driving lanes of normal roads** (never inside
  junctions), at least 5 m from road ends, and only on stretches where the
  lane has full, constant width and runs on to the road's end in its travel
  direction: never in a lane taper or in a lane that is dropped further on.
  With `explicit_routes`, only on lanes that run the whole road and have a
  way on, since routes are planned over those (esmini's default controller
  never changes lanes, and its route planner reports errors for lanes that
  are missing in some lane section).
- The lane is chosen at random, **weighted by usable lane length**, and the
  position is uniform along it.
- Each vehicle keeps at least `--min-gap` metres (default 10) to any other
  vehicle in the same lane. If the vehicles can't be placed after 200 tries
  per vehicle, the builder raises an error with a hint to use fewer vehicles
  or a smaller gap.
- Each vehicle gets a constant target speed drawn from
  `--speed-min`..`--speed-max` (default 8–13.9 m/s), set with a step change
  at t = 0.
- All vehicles are a generic car bounding box (4.5 × 1.8 × 1.5 m). esmini
  draws a box, or a default model, when no 3D model is given.

### Spawn modes (`--mode`)

| Mode | Behaviour |
|------|-----------|
| `random_lanes` (default) | No route. esmini's default controller follows the lane and picks a random connection at every junction. |
| `explicit_routes` | Each vehicle gets the shortest path (by road length) to a random reachable destination, as waypoints. |

Routes are planned on the **lane-level movement graph** built from the
sidecar, because esmini's default controller never changes lanes on a road.
Every step of the path is therefore drivable from the lane the vehicle is
actually in.

The storyboard stops when simulation time exceeds `--duration`. The output is
deterministic for a seed, and the file header date is fixed. `--scenegraph`
attaches a 3D model (see [Buildings](#3d-buildings)).

## Running esmini (`roadgen/sim/runner.py`)

```bash
python -m cli simulate out/<hash>/grid10x10.xosc                # headless
python -m cli simulate out/<hash>/grid10x10.xosc --gui          # window, top camera
python -m cli simulate out/<hash>/grid10x10.xosc --gui --camera orbit
```

`run_esmini()` builds the command line:
- `--headless`, or `--window 60 60 1280 800` with `--gui`;
- `--fixed_timestep` (default 0.05 s);
- `--logfile_path`, plus `--record` (`.dat`) and `--csv_logger` (`.csv`) unless
  `--no-record` / `--no-csv` are given;
- `--enforce_generate_model` when the scenario has a SceneGraphFile, because
  esmini would otherwise not draw the roads.

Outputs go next to the scenario: `<stem>.log`, `.stdout`, `.dat`, `.csv`.

### Monitoring

`run_monitored()` runs esmini as a child process and:

- **samples peak RSS** (esmini plus its children) every 20 ms with `psutil`.
  It also reads the kernel's exact peak from `wait4` rusage, so even very
  short runs are measured;
- **enforces a timeout and a memory cap** (used by the benchmark). The child
  is killed and the run is reported as `killed="timeout"` or `"memory"`. On
  Linux the cap is also set as a kernel `RLIMIT_AS` limit;
- **measures load time**. The child writes to a pseudo-terminal, which makes
  its output line-buffered, and the wall-clock moment of the first
  simulation-timestamped log line (`[0.050] …`) marks the end of loading.

### Result (`SimResult`)

| Field | Meaning |
|-------|---------|
| `wall_time`, `load_time`, `step_wall_time` | Seconds, total / until first step / stepping only |
| `sim_time`, `real_time_factor` | Last simulated timestamp, and sim_time / wall_time |
| `n_vehicles`, `peak_rss_mb`, `returncode`, `killed` | Self-explanatory |
| `errors`, `warnings` | Parsed from the esmini log |
| `asset_warnings` | Missing 3D model/texture messages. Counted but **ignored**, since they only affect rendering |
| `ok` | Return code 0, no errors, and not killed |

`pipeline --simulate` runs a short version: 50 vehicles and 30 s by default
(`--sim-vehicles`, `--sim-duration`), with a 600 s timeout.

## 3D buildings

`roadgen/scene/buildings.py`, enabled with `pipeline --buildings`, adds simple
city blocks to the 3D view.

1. **Blocks** are the bounded faces of the planar road graph (shapely
   `polygonize`), drawn along the fitted roads, so buildings follow the
   curves the builder builds.
2. A block's **footprint** is the face minus every road widened by its half
   width plus a `setback` (3 m). Dead-end roads inside a block therefore stay
   clear too.
3. Convex corners are **rounded** (6 m), so buildings keep clear of turning
   paths inside junctions. Footprints under 150 m² are dropped.
4. Each footprint is **extruded** to a random height between 12 and 60 m.
   The roof is triangulated by ear clipping (Delaunay fallback for holes),
   and the walls get outward normals.
5. With maps (`pipeline --maps`), a building **stands on the ground**: its
   base is 1 m below the lowest ground under it, and its height is added to
   the highest. Footprints touching water are dropped.

### Ground

`pipeline --ground` (or the `ground` command) adds the terrain itself:
`roadgen/scene/ground.py` turns the elevation map into a triangle mesh
(`ground.cell_size`, 20 m), colored by terrain class, and levels it 0.3 m
below every road and junction area, so the ground never covers a road.
Under bridges it is left alone. Ground and buildings go into one
`PREFIX_scene.osgt`, because esmini takes a single SceneGraphFile.

Formats:

- **`.osgt`** (OpenSceneGraph ASCII, z-up). esmini loads it as a
  `SceneGraphFile`. esmini's bundled OSG can't read `.obj`. esmini only loads
  scene graphs **when it has a window** (`simulate --gui`).
- **`.obj` + `.mtl`** (y-up by default). For Blender, MeshLab and similar tools.

```bash
python -m cli pipeline lsystem --preset grid --set extent=[1000,320] --buildings --simulate -o city
python -m cli simulate out/<hash>/city.xosc --gui
# Hilly city: roads follow the terrain, ground and buildings in the 3D view
python -m cli pipeline lsystem --preset city --allow-traps --maps --ground --buildings --simulate -o hills
python -m cli simulate out/<hash>/hills.xosc --gui
```
