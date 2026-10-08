# Procedural-Road-Network-Generation

CMPE492 Project — a Python pipeline that procedurally generates road networks,
converts them to ASAM OpenDRIVE (`.xodr`), builds OpenSCENARIO (`.xosc`) traffic
scenarios, and runs them headless in [esmini](https://github.com/esmini/esmini).

```
Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validator
                                                    -> Scenario Builder -> .xosc -> esmini -> metrics
```

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

esmini is an external tool. Download a release and either unpack it into
`./esmini` (git-ignored) or point `ESMINI_HOME` at it:

```bash
export ESMINI_HOME=/path/to/esmini
export OPENDRIVE_XSD=/path/to/opendrive_16_core.xsd   # optional
```

## Usage

Render a RoadGraph JSON file to an image:

```bash
python -m cli plot-graph tests/data/three_nodes.json out/three_nodes.png --node-ids
```

Generate a Manhattan grid (avenues along x, streets along y), or run the
whole pipeline in one go. `pipeline` writes `<prefix>.json`, `.png`, `.xodr`
and `.roadmap.json`, runs all validators, and logs how long each stage took:

```bash
python -m cli generate manhattan --avenues 5 --streets 10 --seed 1 -o out/grid.json
python -m cli pipeline manhattan --avenues 10 --streets 10 --seed 1 -o out/grid10x10
python -m cli pipeline manhattan --avenues 10 --streets 10 --seed 1 \
    --jitter 3 --edge-drop 0.2 --rotation 15 --drive-check -o out/grid_variant
```

Run `python -m cli generate manhattan --help` for all parameters (spacing,
lane counts, speeds, origin, one-way patterns, boundary mode). The same
seed and parameters always give identical output.

A Midtown-like preset: rotated 29 degrees, avenues 250 m apart with 4 lanes
and streets 80 m apart with 2 lanes, both alternating one-way, and a ring
road around the grid. Flags given on the command line override the preset:

```bash
python -m cli pipeline manhattan --preset manhattan_like --seed 1 -o out/manhattan_like
python -m cli pipeline manhattan --preset manhattan_like --avenues 4 --streets 8 -o out/small
```

- `--avenue-oneway` / `--street-oneway alternating`: consecutive avenues or
  streets flip direction, and one-way roads put all their lanes forward.
- `--boundary dead_end` (default) keeps the grid closed. `--boundary loop`
  extends every avenue and street to a two-way ring road so traffic never
  dead-ends (`--ring-offset`, `--ring-lanes`).

`pipeline` also checks traffic connectivity. It fails if vehicles could get
trapped (dead ends, one-way sinks), if some road can never be reached, or if
the lane-direction graph is not strongly connected, and it names the nodes
involved. Pass `--allow-traps` to report these as warnings instead.
`build-xodr` always reports them as warnings.

### Generators

Generators register themselves by name. `python -m cli generators` lists them
with their presets and every parameter. Each one accepts `--preset`,
`--params file.yaml` (either plain fields, or `preset:` plus `params:`),
repeatable `--set key=value`, and `--seed`. The output path is optional and
defaults to `out/<name>[_<preset>]_s<seed>`.

- `manhattan`: the parametric grid described above.
- `lsystem`: growth in the style of Parish & Müller. Proposed segments wait
  in a priority queue and pass local constraints (stay in bounds, stop at
  the first crossing, snap to a nearby node or road, minimum length, minimum
  angle) before a global goal proposes the next ones. The `grid` goal is
  implemented: preset `grid` reproduces the 10x10 Manhattan grid exactly,
  and `grid_organic` adds heading and length noise and skips some branches.

```bash
python -m cli pipeline lsystem --preset grid
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings --simulate
```

Further `pipeline` options:
- `--buildings`: writes `<prefix>_buildings.osgt`, one extruded building per
  city block. The file is OpenSceneGraph ASCII, which esmini can load as a
  SceneGraphFile (the bundled esmini cannot read `.obj`). `.obj` export is
  also available for other tools.
- `--simulate`: runs a short headless esmini simulation with random traffic,
  using the buildings when they were exported.

`scenario --scenegraph <file>` attaches a 3D model to any scenario.

Junctions may have any number of arms at any angle down to 10 degrees.
Adjacent arms are pulled back until their road edges no longer overlap, at
most one exit counts as straight, and turns are cubic curves shaped like
circular arcs.

Convert a RoadGraph to OpenDRIVE and open it in esmini's `odrviewer`:

```bash
python -m cli build-xodr tests/fixtures/single_4way.json out/single_4way.xodr
python -m cli view out/single_4way.xodr --density 2
```

Nodes of degree 1 are dead ends and every other node becomes a junction,
with one single-lane connecting road per lane movement (straight, left,
right; no U-turns; one-way edges respected). A degree-2 node, such as a
grid corner, is a two-arm junction in which every lane continues. Junction
fixtures live in `tests/fixtures/`.

`build-xodr` also writes `out/single_4way.roadmap.json`, which maps graph
edge ids to road ids and node ids to junction ids, and lists every junction
movement. It then checks the file:

- topology: ids are unique, and road, junction and lane links all resolve;
- continuity: lane centers match within 1 cm and 0.01 rad at every link;
- schema: only if `OPENDRIVE_XSD` (or `--xsd`) points at the ASAM OpenDRIVE
  XSD; otherwise this check is skipped with a warning.

To drive every junction movement with esmini's own RoadManager library and
confirm that each one takes its connecting road into the right lane without
position jumps:

```bash
python -m cli drive-check out/single_4way.xodr
```

esmini is looked up in `$ESMINI_HOME`, falling back to `esmini/` in the repo.

Generate an OpenSCENARIO file with many vehicles and run it in esmini:

```bash
python -m cli scenario out/grid10x10.xodr --vehicles 200 --seed 1 --duration 120 -o out/grid10x10.xosc
python -m cli simulate out/grid10x10.xosc          # headless: timing, errors, .dat/.csv/.log
python -m cli simulate out/grid10x10.xosc --gui    # watch it (top camera by default)
```

- `--mode random_lanes` (default): no routes, so esmini picks a random
  connection at every junction.
- `--mode explicit_routes`: each vehicle follows a shortest path to a random
  reachable destination. The path is planned lane by lane over the junction
  movements, because esmini's default controller never changes lanes on a
  road.

Vehicles spawn on random driving lanes of normal roads, at least
`--min-gap` metres apart within a lane, with speeds drawn from
`--speed-min`..`--speed-max`. The same seed gives an identical file.
`simulate` writes `<stem>.dat`, `.csv` and `.log` next to the scenario. It
reports simulated time, wall time, the real-time factor, and any esmini
errors or warnings; missing 3D-model and texture warnings are counted but
ignored.

### Scalability benchmark

```bash
python -m cli bench configs/bench_scalability.yaml   # resumable sweep -> out/bench/results.csv + plots
python -m cli bench-plot out/bench/results.csv      # re-plot (PNG + PDF in out/bench/plots/)
```

The YAML config sets the grid sizes, vehicle counts, seeds, simulated
duration, spawn mode, a timeout and memory cap per esmini run, and optional
`network` overrides (e.g. `{preset: manhattan_like}`). Each network is built
once per grid size and seed and cached.

Each combination appends one row to `results.csv`:
- network size and road length;
- generation and build times, and `.xodr` size;
- esmini load time (time to first step);
- stepping wall time and real-time factor (RTF = simulated s / wall s);
- peak memory;
- status and errors.

Load time comes from the first simulation-timestamped esmini log line, read
live through a pseudo-terminal. Status values:
- `ok` / `error`: the run completed, without or with esmini errors.
- `skipped`: the vehicles don't fit at the spawn gap, or the run is
  dominated by an earlier timeout or memory kill on the same network.
- `timeout` / `memory`: killed at the limit.
- `failed`: any other exception.

Rerunning the same command skips rows that are already present, so an
interrupted sweep can simply be restarted. `configs/bench_smoke.yaml` runs
in about a minute.

Generated files go in `out/`, which git ignores.

## Tests

```bash
pytest
```

## Layout

```
roadgen/
  graph/        RoadGraph model, JSON I/O, plotting
  generators/   procedural network generators
  opendrive/    RoadGraph -> OpenDRIVE builder
  validate/     schema / topology / continuity checks
  scenario/     OpenSCENARIO builder
  sim/          esmini runner
  bench/        benchmarks
cli.py          command-line entry point
tests/
```
