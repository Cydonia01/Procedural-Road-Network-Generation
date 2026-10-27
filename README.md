# Procedural-Road-Network-Generation

**roadgen** is a Python pipeline that procedurally generates
road networks, converts them to ASAM OpenDRIVE (`.xodr`), builds OpenSCENARIO
(`.xosc`) traffic scenarios, and runs them headless in
[esmini](https://github.com/esmini/esmini) to train and test autonomous
driving. All networks are generated; OpenStreetMap is not used.

```
Input maps (terrain, elevation, population; procedural or from images)
   |
Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validators
                                                    -> Scenario Builder -> .xosc -> esmini -> metrics
```

**Features**

- **Generator** `lsystem`: Parish & Müller style growth for every city
  pattern:
  - grids with alternating one-way avenues and streets, a ring road and
    dropped streets (a Midtown-like preset);
  - curved streets (drifting, contour-following, bowed);
  - pattern fields (Chen et al.): grid, radial (Paris), organic, contour
    (San Francisco) and suburb patterns blended per district by a style
    map, with skewed junctions and a grid-to-organic knob;
  - a connectivity knob from grids to tree-like suburbs, with turn-around
    bulbs at dead ends, and staggered crossings and star junctions;
  - roundabouts, freeways with overpasses, highway tunnels, diamond
    interchanges and lane adds and drops.
- **Generator** `junction`: one JunctionArt-style junction per seed (3–7
  legs, skew, offset T pairs, random lanes per side, roundabouts) or a
  diamond / cloverleaf interchange, for long-tail data.
- **Input maps** (after Parish & Müller): pluggable generators, built as a
  linear pipeline so the maps agree: a water/land/vegetation map (lakes,
  coast, rivers, parks), then an elevation map on it (fBm, ridged
  multifractal, thermal erosion; water in the valleys), then a population
  density map on both (polycentric Clark model; nobody on water or steep
  slopes), then a style map of street-pattern districts. Real maps can be
  read from images.
  `lsystem` grows on them: highways seek population peaks and bridge
  rivers, and streets stop at shores, parks, steep slopes and empty land.
- **Road geometry** (design-speed rules after AASHTO): road bends are not
  junctions but one road of lines, spirals and arcs sized for the speed
  limit, with advisory speeds where a bend is too tight, banking on curves,
  lane-count changes as tapers, and sloped junction areas. Curvature is
  continuous everywhere, and no road needs more than 3 m/s² of lateral
  acceleration at its posted speed. Bends that would cut into water or
  steep ground are capped against the maps.
- **OpenDRIVE builder:**
  - junctions with any number of arms, at any angle down to 10°;
  - one connecting road per lane movement, as three clothoids
    (curvature-continuous) with a speed from the turn's radius;
  - no lane ever dead-ends.
- **Validation:**
  - traffic connectivity (no traps, everything reachable);
  - id and link topology, and lane continuity within 1 cm;
  - curvature continuity and lateral acceleration at the posted speed;
  - optional XSD schema check;
  - a drive-through of every junction movement and every lane with esmini's
    own RoadManager.
- **Geometry metrics:** `metrics` measures any `.xodr` (curvature, lateral
  acceleration, curvature jumps) and sweeps generator seeds for build failures.
- **Scenarios:** hundreds to thousands of vehicles, with random junction
  choices or shortest-path routes.
- **Heights:** with an elevation map the roads follow the terrain
  (`<elevationProfile>`, `<superelevation>`, junction planes, `<bridge>` over water).
- **3D scene:** optional buildings, one extruded block per city block, and a
  ground mesh from the maps, both standing on the terrain.

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
python -m cli pipeline lsystem --preset grid --set extent=[750,240] --seed 1 --drive-check --simulate -o first
# logs "run <hash>: out/<hash>": the run's folder, with first.xodr and the rest of its outputs
python -m cli view out/<hash>/first.xodr    # natively; in Docker see below
```

Opening a window (`view`, `simulate --gui`) needs a display. The `dev`
service has none, so `odrviewer` exits immediately without an error.
In Docker, use the GUI service for your OS:

```bash
# Linux (X11 or Wayland/XWayland), from a terminal in your desktop session
docker compose run --rm gui python -m cli view out/<hash>/first.xodr

# macOS: XQuartz running, "Allow connections from network clients" on,
# and `xhost +localhost` run once (see docs/docker.md)
docker compose run --rm gui-mac python -m cli view out/<hash>/first.xodr
```

## Common commands

```bash
python -m cli generators                                       # list generators, presets, parameters

# Generate + build + validate (writes .json, .png, .xodr, .roadmap.json)
python -m cli pipeline lsystem --preset grid --seed 1 -o grid10x10              # -> out/<G>/grid10x10.*
python -m cli pipeline lsystem --preset manhattan_like --seed 1
python -m cli pipeline lsystem --preset paris --allow-traps        # radial star and rings, a grid outside
python -m cli pipeline lsystem --preset mixed_city --maps --allow-traps   # districts from a style map
python -m cli pipeline lsystem --preset highway_corridor --maps --allow-traps   # freeways and a diamond
python -m cli pipeline junction --preset random --seed 17 --allow-traps   # one long-tail junction
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings --simulate
python -m cli pipeline lsystem --preset city --allow-traps --maps   # grown on terrain/elevation/population maps

# Input maps on their own
python -m cli maps                                   # list map generators, presets, parameters
python -m cli map terrain noise --preset coastal --seed 3 -o coast
python -m cli map elevation fbm --preset hilly

# Individual stages; each run writes into its own out/<hash>/ (X, S, O: the folders they log)
python -m cli build-xodr tests/fixtures/single_4way.json     # -> out/<X>/single_4way.xodr
python -m cli drive-check out/<X>/single_4way.xodr
python -m cli metrics out/<X>/single_4way.xodr      # curvature, lateral acceleration, two-road junctions
python -m cli scenario out/<G>/grid10x10.xodr --vehicles 200 --seed 1 --duration 120   # -> out/<S>/grid10x10.xosc
python -m cli simulate out/<S>/grid10x10.xosc        # headless: timing, errors, .dat/.csv/.log
python -m cli simulate out/<S>/grid10x10.xosc --gui  # watch it
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o organic
python -m cli view out/<O>/organic.xodr --scenegraph out/<O>/organic_buildings.osgt   # roads, random cars and buildings

# Run folders: every successful command in out/<hash>/ with its outputs and run.yaml
python -m cli history                                # list runs (hash, note, command)
python -m cli HASH                                   # run it again (or: python -m cli run out/HASH/run.yaml)
python -m cli --note "my run" pipeline ...           # describe a run; --no-history: no folder, -o is a path
```

Generated files go in `out/<hash>/`, one folder per run, which git ignores.
`out/<hash>/run.yaml` shows the run's command and every parameter that
differs from the defaults. Every command is documented in the
[CLI reference](docs/cli-reference.md).

### Default parameters

All default values (generator parameters, scenario, buildings, esmini and pipeline
settings) live in [roadgen/defaults.yaml](roadgen/defaults.yaml).
To change them, write a YAML file with only the keys you want to change and pass
it with `--config` before the command; any command-line flag still wins:

```bash
python -m cli --config my_defaults.yaml pipeline lsystem --preset grid
```

Precedence, lowest first: `defaults.yaml` < preset < `--config` file <
`--params` file < flags and `--set`. All of it is stored, as the difference
from `defaults.yaml`, in the run's `run.yaml`
([CLI reference → history](docs/cli-reference.md#history)).

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Installation and first run |
| [Architecture](docs/architecture.md) | Pipeline, design rules, module map, output files |
| [RoadGraph](docs/road-graph.md) | Data model and JSON format |
| [Generators](docs/generators.md) | `lsystem` (grids, pattern fields, cul-de-sacs), presets, transforms, adding a generator |
| [Input maps](docs/maps.md) | Terrain, elevation and population maps; where to get real ones |
| [OpenDRIVE builder](docs/opendrive.md) | Roads, lanes, junction geometry, id scheme, sidecar |
| [Validation](docs/validation.md) | Connectivity, topology, continuity, schema, drive-check |
| [Scenarios & simulation](docs/scenarios-and-simulation.md) | `.xosc` generation, esmini runner, 3D buildings |
| [CLI reference](docs/cli-reference.md) | All commands and flags |
| [Example runs](runs/README.md) | Examples for every phase, from input maps to the esmini GUI, replayable by hash |
| [Docker environment](docs/docker.md) | Container setup, Linux/macOS GUI |
| [Development guide](docs/development.md) | Tests, coding rules, project phases, known issues |

## Layout

```
cli.py          command-line entry point (python -m cli ...)
roadgen/
  graph/        RoadGraph model, JSON I/O, plotting
  generators/   procedural network generators (registry, lsystem, pattern fields, transforms)
  maps/         input maps: terrain, elevation, population (registry, noise, image loaders)
  geometry/     neutral road geometry: design-speed rules, curve pieces, junction trims
  opendrive/    RoadGraph -> OpenDRIVE builder (lanes, junctions, geometry)
  validate/     connectivity / topology / continuity / schema checks
  metrics/      geometry metrics of an .xodr, build-failure sweep
  scenario/     OpenSCENARIO builder
  sim/          esmini runner and RoadManager drive-check
  scene/        3D buildings and ground (.osgt)
  history.py    run folders out/<hash>/: hashes of successful commands, replay
runs/           example runs (examples.sh, README) and their inputs
tests/          pytest suite and RoadGraph fixtures
docs/           documentation
```
