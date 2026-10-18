# Procedural-Road-Network-Generation

**roadgen** is a Python pipeline that procedurally generates
road networks, converts them to ASAM OpenDRIVE (`.xodr`), builds OpenSCENARIO
(`.xosc`) traffic scenarios, and runs them headless in
[esmini](https://github.com/esmini/esmini) for large-scale traffic and
scalability experiments. All networks are generated; OpenStreetMap is not used.

```
Input maps (terrain, elevation, population; procedural or from images)
   |
Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validators
                                                    -> Scenario Builder -> .xosc -> esmini -> metrics
```

**Features**

- **Generators:**
  - `manhattan`: parametric grids with one-way patterns, multi-lane avenues,
    a ring road, jitter, dropped streets, and a Midtown-like preset.
  - `lsystem`: Parish & Müller style growth.
- **Input maps** (after Parish & Müller): pluggable generators, built as a
  linear pipeline so the maps agree: a water/land/vegetation map (lakes,
  coast, rivers, parks), then an elevation map on it (fBm, ridged
  multifractal, thermal erosion; water in the valleys), then a population
  density map on both (polycentric Clark model; nobody on water or steep
  slopes). Real maps can be read from images.
  `lsystem` grows on them: highways seek population peaks and bridge
  rivers, and streets stop at shores, parks, steep slopes and empty land.
- **OpenDRIVE builder:**
  - junctions with any number of arms, at any angle down to 10°;
  - one connecting road per lane movement, with arc-like cubic curves;
  - no lane ever dead-ends.
- **Validation:**
  - traffic connectivity (no traps, everything reachable);
  - id and link topology, and lane continuity within 1 cm;
  - optional XSD schema check;
  - a drive-through of every junction movement with esmini's own RoadManager.
- **Scenarios:** hundreds to thousands of vehicles, with random junction
  choices or shortest-path routes.
- **Heights:** with an elevation map the roads follow the terrain
  (`<elevationProfile>`, flat junction areas, `<bridge>` over water).
- **3D scene:** optional buildings, one extruded block per city block, and a
  ground mesh from the maps, both standing on the terrain.
- **Benchmark:** resumable scalability sweep measuring real-time factor, load
  time and peak memory, with report plots.

## Quick start

### With Docker (Linux and macOS)

Python, all dependencies and esmini are preinstalled. See
[docs/docker.md](docs/docker.md) for GUI setup and macOS notes.

```bash
docker compose build
docker compose run --rm dev                 # shell with the repo mounted at /workspace
```

### Native

Requires Python 3.10+ and an [esmini release](https://github.com/esmini/esmini/releases)
(tested with v3.9).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export ESMINI_HOME=/path/to/esmini          # or unpack it into ./esmini (git-ignored)
export OPENDRIVE_XSD=/path/to/opendrive_16_core.xsd   # optional
```

### First run

```bash
pytest
python -m cli pipeline manhattan --avenues 4 --streets 4 --seed 1 --drive-check --simulate -o out/first
python -m cli view out/first.xodr           # natively; in Docker see below
```

Opening a window (`view`, `simulate --gui`) needs a display. The `dev`
service has none, so `odrviewer` exits immediately without an error.
In Docker, use the GUI service for your OS:

```bash
# Linux (X11 or Wayland/XWayland), from a terminal in your desktop session
docker compose run --rm gui python -m cli view out/first.xodr

# macOS: XQuartz running, "Allow connections from network clients" on,
# and `xhost +localhost` run once (see docs/docker.md)
docker compose run --rm gui-mac python -m cli view out/first.xodr
```

## Common commands

```bash
python -m cli generators                                       # list generators, presets, parameters

# Generate + build + validate (writes .json, .png, .xodr, .roadmap.json)
python -m cli pipeline manhattan --avenues 10 --streets 10 --seed 1 -o out/grid10x10
python -m cli pipeline manhattan --preset manhattan_like --seed 1
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings --simulate
python -m cli pipeline lsystem --preset city --allow-traps --maps   # grown on terrain/elevation/population maps

# Input maps on their own
python -m cli maps                                   # list map generators, presets, parameters
python -m cli map terrain noise --preset coastal --seed 3 -o out/coast
python -m cli map elevation fbm --preset hilly

# Individual stages
python -m cli build-xodr tests/fixtures/single_4way.json out/single_4way.xodr
python -m cli drive-check out/single_4way.xodr
python -m cli scenario out/grid10x10.xodr --vehicles 200 --seed 1 --duration 120 -o out/grid10x10.xosc
python -m cli simulate out/grid10x10.xosc            # headless: timing, errors, .dat/.csv/.log
python -m cli simulate out/grid10x10.xosc --gui      # watch it
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o out/organic
python -m cli view out/organic.xodr --scenegraph out/organic_buildings.osgt   # roads, random cars and buildings

# Run history: every successful command is recorded under a hash of its inputs
python -m cli history                                # list runs (hash, note, command)
python -m cli 0bafbe99d5d4                           # replay one: the coastal terrain map example
python -m cli --note "my run" pipeline ...           # describe a run; --no-history skips recording

# Scalability benchmark
python -m cli bench configs/bench_smoke.yaml         # ~1 minute
python -m cli bench configs/bench_scalability.yaml   # full, resumable sweep
```

Generated files go in `out/`, which git ignores. Every command is documented
in the [CLI reference](docs/cli-reference.md).

### Default parameters

All default values (generator parameters, scenario, buildings, esmini, pipeline
and benchmark settings) live in [roadgen/defaults.yaml](roadgen/defaults.yaml).
To change them, write a YAML file with only the keys you want to change and pass
it with `--config` before the command; any command-line flag still wins:

```bash
python -m cli --config my_defaults.yaml pipeline manhattan --avenues 4 --streets 4
```

Precedence, lowest first: `defaults.yaml` < `--config` file < `--preset` <
`--params` file < flags and `--set`.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Installation and first run |
| [Architecture](docs/architecture.md) | Pipeline, design rules, module map, output files |
| [RoadGraph](docs/road-graph.md) | Data model and JSON format |
| [Generators](docs/generators.md) | `manhattan`, `lsystem`, presets, adding a generator |
| [Input maps](docs/maps.md) | Terrain, elevation and population maps; where to get real ones |
| [OpenDRIVE builder](docs/opendrive.md) | Roads, lanes, junction geometry, id scheme, sidecar |
| [Validation](docs/validation.md) | Connectivity, topology, continuity, schema, drive-check |
| [Scenarios & simulation](docs/scenarios-and-simulation.md) | `.xosc` generation, esmini runner, 3D buildings |
| [Scalability benchmark](docs/benchmarking.md) | Sweep config, results CSV, plots |
| [CLI reference](docs/cli-reference.md) | All commands and flags |
| [Example runs](runs/README.md) | Recorded examples for every phase, from input maps to the esmini GUI, replayable by hash |
| [Docker environment](docs/docker.md) | Container setup, Linux/macOS GUI |
| [Development guide](docs/development.md) | Tests, coding rules, project phases, known issues |

## Layout

```
cli.py          command-line entry point (python -m cli ...)
roadgen/
  graph/        RoadGraph model, JSON I/O, plotting
  generators/   procedural network generators (registry, manhattan, lsystem)
  maps/         input maps: terrain, elevation, population (registry, noise, image loaders)
  opendrive/    RoadGraph -> OpenDRIVE builder (lanes, junctions, geometry)
  validate/     connectivity / topology / continuity / schema checks
  scenario/     OpenSCENARIO builder
  sim/          esmini runner and RoadManager drive-check
  scene/        3D buildings and ground (.osgt)
  bench/        scalability benchmark and plots
  history.py    run history: hashes of successful commands, replay
configs/        benchmark configs
runs/           run history (history.json), example runs and their inputs
tests/          pytest suite and RoadGraph fixtures
docs/           documentation
```
