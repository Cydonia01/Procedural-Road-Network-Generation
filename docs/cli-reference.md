# CLI reference

All commands run as `python -m cli <command> [options]` from the repo root.
Run `python -m cli <command> --help` for the authoritative list.

Global options must come **before** the command, e.g. `python -m cli -v pipeline ...`:

| Option | Meaning |
|--------|---------|
| `-v, --verbose` | Debug logging |
| `--config FILE` | YAML file overriding default parameters |
| `--note TEXT` | Description stored in this run's [`run.yaml`](#history) |
| `--no-history` | Don't record this run: no `out/<hash>/` folder, `-o` is a path |

### Defaults and `--config`

Every default value lives in [`roadgen/defaults.yaml`](../roadgen/defaults.yaml):
generator parameters (`generators.lsystem`), map parameters (`maps`), the seed,
`opendrive.junction_margin`, `scenario`, `buildings`, `esmini` (timestep,
window, camera, view density), `pipeline` (`--simulate` settings) and `bench`
(keys a sweep config leaves out). A `--config` file uses the same nesting and
only needs the keys it changes; unknown keys are an error. A generator or
map generator section may also name a preset. For example:

```yaml
seed: 42
generators:
  lsystem:
    preset: manhattan_like
    lane_width: 3.25
scenario:
  speed_range: [5.0, 10.0]
```

Precedence, lowest first: `defaults.yaml` < preset < `--config` file <
`--params` file < command-line flags and `--set`. A preset is where a
parameter section starts; anything you write wins over it. `--help` shows
the defaults in effect, including those from `--config`.

All of these end up in one **run config**: the `config` part of the run's
`run.yaml` (see [history](#history)), which holds only what differs from
`defaults.yaml`. `--preset`, `--params`, `--set` and
`--map-set` are written into the section they set
(`generators.<name>`, `maps.<kind>.<name>`). Benchmark networks use
`bench.network`, not `generators.lsystem`, so a sweep's config id only
depends on the sweep settings.

The `manhattan` generator was removed in stage C (lsystem grows the same
grids, `--preset manhattan_like`); replaying an old run of it, or a config
with a `generators.manhattan` section, stops with a message saying so.

Exit codes: `0` on success, `1` when a validation, check or simulation failed.

## Overview

| Command | Purpose |
|---------|---------|
| [`generators`](#generators) | List generators, presets and parameters |
| [`generate`](#generate) | Generator → RoadGraph JSON |
| [`maps`](#maps) | List map generators, presets and parameters |
| [`map`](#map) | Generate one input map (`.npz` + `.png`) |
| [`plot-map`](#plot-map) | Map `.npz` → image |
| [`align`](#align) | Cap bend radii where a fitted curve would leave legal ground |
| [`drape`](#drape) | Heights from an elevation map into a RoadGraph |
| [`ground`](#ground) | 3D ground mesh (`.osgt`) from maps, levelled under a network |
| [`pipeline`](#pipeline) | Generate → plot → `.xodr` → validate (→ buildings → drive-check → simulate) |
| [`plot-graph`](#plot-graph) | RoadGraph JSON → image |
| [`build-xodr`](#build-xodr) | RoadGraph JSON → `.xodr` + validation |
| [`drive-check`](#drive-check) | Drive every junction movement with esmini's RoadManager |
| [`metrics`](#metrics) | Geometry metrics of `.xodr` files and a build-failure sweep |
| [`view`](#view) | Open an `.xodr` in esmini's `odrviewer` |
| [`scenario`](#scenario) | `.xodr` → `.xosc` with many vehicles |
| [`simulate`](#simulate) | Run an `.xosc` in esmini and summarize |
| [`bench`](#bench) | Scalability sweep from a YAML config |
| [`bench-plot`](#bench-plot) | Plot a benchmark `results.csv` |
| [`history`](#history) | List recorded runs (hash, note, command) |
| [`replay`](#replay) | Run a recorded command again from its hash (also `python -m cli HASH`) |
| [`run`](#run) | Run a run config: `out/<hash>/run.yaml`, or one written by hand |

### Outputs

A recorded run (the default) writes every output into its own folder
`out/<hash>/` ([history](#history)). There, `-o` and the output arguments
below are only **file names**, not paths. A path is an error. Without them, the
default names below are used. With `--no-history` (or `ROADGEN_HISTORY=off`)
no folder is made, and outputs go where `-o` says, as paths. To read the
output of an earlier run, give its path in its folder, e.g.
`--map-file terrain=out/0bafbe99d5d4/coast_terrain.npz`.

## generators

```bash
python -m cli generators
```

## generate

```bash
python -m cli generate lsystem [generator options] [-o out.json]
```

Default output: `<name>[_<preset>]_s<seed>.json`.

### Options for every generator

| Option | Meaning |
|--------|---------|
| `--seed N` | Random seed (default 0) |
| `--preset NAME` | Start from a named parameter set |
| `--params FILE.yaml` | Parameter file (fields, or `preset:` + `params:`) |
| `--set KEY=VALUE` | Override one parameter. Repeatable, and the value is parsed as YAML |
| `--map-file KIND=FILE.npz` | Input map to grow on (`terrain`, `elevation`, `population`, `style`), written by `map` or a pipeline run. Repeatable. Only `lsystem` reads maps ([details](maps.md)). With an elevation map the graph also gets heights ([drape](maps.md#heights-from-the-maps-to-esmini)) |
| `--flat` | Keep the roads flat even with an elevation map |

Generator parameters have no flags of their own: use `--set`, e.g.
`generate lsystem --preset grid --set extent=[500,240] --set boundary=ring`
(the [parameters](generators.md#lsystem-growth-after-parish--müller-lsystempy)).

## pipeline

```bash
python -m cli pipeline <generator> [generator options] [pipeline options] [-o PREFIX]
```

Writes `PREFIX.json`, `.png`, `.xodr` and `.roadmap.json`, then runs all
validators and logs the time each stage took. Default prefix:
`<name>[_<preset>]_s<seed>`.

| Option | Meaning |
|--------|---------|
| `--allow-traps` | Report traffic traps / unreachable roads as warnings instead of errors |
| `--xsd FILE` | OpenDRIVE XSD (default `$OPENDRIVE_XSD`) |
| `--junction-margin M` | Extra junction clearance [m] (default 2.0) |
| `--buildings` | Also write `PREFIX_buildings.osgt`. With maps, buildings stand on the ground and none stand on water |
| `--ground` | Also write `PREFIX_scene.osgt`: a ground mesh from the maps, levelled under the roads, with the `--buildings` on it. Used as SceneGraphFile by `--simulate`. Needs an elevation map |
| `--drive-check` | Also run drive-check (only if there are no errors so far) |
| `--simulate` | Also write `PREFIX.xosc` and run it headless (only if there are no errors so far) |
| `--sim-vehicles N` | Vehicles for `--simulate` (default 50) |
| `--sim-duration S` | Simulated seconds for `--simulate` (default 30) |
| `--maps` | Generate terrain, elevation (on the terrain), population (on both) and style (on all three) maps (generators from `maps.pipeline`) first, on a grid around the generator's area, and grow the network on them. Only the kinds the generator reads are built: lsystem builds a style map only for `global_goal: field`. Written to `PREFIX_<kind>.npz` / `.png`. Generators that don't read maps get them around the finished network, for reference |
| `--terrain`, `--elevation`, `--population`, `--style NAME` | Same for one kind, with generator `NAME` (overrides `--maps` for that kind, and always builds it) |
| `--map-set KIND.KEY=VALUE` | Override one map parameter, e.g. `--map-set terrain.rivers=2`, the same as `maps.terrain.<generator>.rivers` in the config. Repeatable |

With an elevation map the network gets heights before it is written, unless
`--flat` is given: nodes, profiles along the edges and bridges over water
([details](maps.md#heights-from-the-maps-to-esmini)).

## maps

```bash
python -m cli maps
```

## map

```bash
python -m cli map <terrain|elevation|population|style> <generator> [options] [-o PREFIX]
```

Writes `PREFIX.npz` and `PREFIX.png`. Default prefix:
`<kind>_<name>[_<preset>]_s<seed>`. See [Input maps](maps.md).

| Option | Meaning |
|--------|---------|
| `--seed N`, `--preset NAME`, `--params FILE.yaml`, `--set KEY=VALUE` | As for generators |
| `--origin X Y` | Lower-left corner [m] (default `maps.grid.origin`) |
| `--size W H` | Map size [m] (default `maps.grid.size`) |
| `--cell-size M` | Cell size [m] (default `maps.grid.cell_size`) |
| `--graph FILE.json` | Draw this RoadGraph on the preview |
| `--on KIND=FILE.npz` | Build on an earlier map of the pipeline (terrain → elevation → population → style), e.g. `map elevation fbm --on terrain=out/<hash>/coast.npz`. Repeatable; not for `terrain`. The file may be on another grid; it is resampled |

## align

```bash
python -m cli align NET.json --map-file terrain=T.npz [--map-file elevation=E.npz] [-o OUT.json]
```

Caps the radius of bends whose fitted curve would cross new water or climb
steeper than `align.max_grade` (sets `Node.radius_cap`; see
[maps](maps.md#heights-from-the-maps-to-esmini)). Default output:
`NET.aligned.json`. `pipeline` runs it automatically when it has maps.

## drape

```bash
python -m cli drape NET.json --map-file elevation=E.npz [--map-file terrain=T.npz] [-o OUT.json]
```

Gives a RoadGraph heights from an elevation map (and bridges from a terrain
map). Default output: `NET.draped.json`. Parameters: `drape:` in the config.

## ground

```bash
python -m cli ground [OUT.osgt] --map-file elevation=E.npz [--map-file terrain=T.npz] \
    [--graph NET.json [--buildings]] [--seed N] [--junction-margin M]
```

A 3D ground mesh from the maps, colored by terrain class. With `--graph` (a
draped network), the ground is levelled under its roads; `--buildings` adds
its buildings on top. Use the result as `scenario --scenegraph`. Default
output: `scene.osgt`. Parameters: `ground:` in the config.

## plot-map

```bash
python -m cli plot-map INPUT.npz [OUTPUT.png] [--graph FILE.json]     # default: <input>.png
```

## plot-graph

```bash
python -m cli plot-graph INPUT.json [OUTPUT.png] [--node-ids]          # default: <input>.png
```

## build-xodr

```bash
python -m cli build-xodr INPUT.json [OUTPUT.xodr] [--xsd FILE] [--junction-margin M]   # default: <input>.xodr
```

Traffic connectivity problems are only warnings here, since hand-made test
graphs often have dead ends.

## drive-check

```bash
python -m cli drive-check NETWORK.xodr      # needs NETWORK.roadmap.json
```

## metrics

```bash
python -m cli metrics A.xodr [B.xodr ...] [--graph A.json ...] [--json OUT.json]
python -m cli metrics --sweep 20 [--first-seed 0] [--generator lsystem] [--preset city]
```

Measures how drivable and smooth a network is, from the `.xodr` alone (see
[Validation → Geometry metrics](validation.md#geometry-metrics)): lateral
acceleration at the posted speed per road group, the share of junctions
joining only two roads, and curvature jumps at road links and geometry
joins. `--sweep N` also generates N networks (seeds `--first-seed` … , maps
as `pipeline --maps`) and counts how many the OpenDRIVE builder rejects.
`--graph NET.json` (repeatable) adds the [network metrics](validation.md#network-metrics)
of a RoadGraph: junction legs, skew, offset T pairs, connected-node ratio,
densities, orientation entropy, tortuosity, block areas and stage C
features; the sweep logs their median per seed too.
The report is informational: the command succeeds whatever the numbers are.
Thresholds are in `defaults.yaml` under `metrics` and `network_metrics`.

## view

```bash
python -m cli view NETWORK.xodr [--density CARS_PER_100M] [--scenegraph FILE.osgt] [--duration S]
```

| Option | Meaning |
|--------|---------|
| `--density` | Random cars per 100 m of lane (default 1) |
| `--scenegraph FILE.osgt` | 3D model shown with the roads: `PREFIX_buildings.osgt`, or `PREFIX_scene.osgt` (ground + buildings) from `pipeline --buildings` / `--ground` or `ground`. odrviewer still draws the roads itself |
| `--duration S` | Close the viewer after S seconds; by default it stays open until you close it |

Needs a display. In Docker, the `dev` service has none (`odrviewer` exits
immediately), so use the GUI service for your OS:

```bash
docker compose run --rm gui python -m cli view NETWORK.xodr       # Linux
docker compose run --rm gui-mac python -m cli view NETWORK.xodr   # macOS (XQuartz, see docker.md)
```

The same applies to `simulate --gui`.

## scenario

```bash
python -m cli scenario NETWORK.xodr [-o OUT.xosc] --vehicles N [options]   # default: <input>.xosc
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--vehicles N` | required | Number of vehicles |
| `--seed N` | 0 | |
| `--duration S` | 60 | Simulated time [s] |
| `--mode` | `random_lanes` | or `explicit_routes` |
| `--speed-min`, `--speed-max` | 8.0, 13.9 | [m/s] |
| `--min-gap M` | 10 | Minimum spawn gap within a lane [m] |
| `--graph FILE` | `<input>.json` if present | RoadGraph used for consistency checks |
| `--scenegraph FILE` | – | 3D model shown with the roads (e.g. buildings `.osgt`) |

## simulate

```bash
python -m cli simulate SCENARIO.xosc [options]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--gui` | off | Show the esmini window |
| `--camera MODE` | `top` | With `--gui`: `orbit`, `fixed`, `flex`, `flex-orbit`, `top`, `driver` |
| `--timestep S` | 0.05 | Fixed timestep |
| `--no-record` | – | Skip the `.dat` recording |
| `--no-csv` | – | Skip the `.csv` log |

The `.dat`, `.csv` and `.log` go into the run's folder (next to the `.xosc`
with `--no-history`).

## bench

```bash
python -m cli bench CONFIG.yaml [--out-dir DIR] [--no-plot]
```

A recorded sweep writes into its run folder (`out_dir` and `--out-dir` only
apply with `--no-history`).

## bench-plot

```bash
python -m cli bench-plot RESULTS.csv [--out-dir DIR]     # default: <results dir>/plots
```

## Environment variables

| Variable | Used by | Meaning |
|----------|---------|---------|
| `ESMINI_HOME` | everything esmini-related | esmini root containing `bin/` (falls back to `./esmini`) |
| `OPENDRIVE_XSD` | schema check | Path to the ASAM OpenDRIVE XSD |
| `ROADGEN_HISTORY` | every recorded command | Root of the run folders (default `out`); `off` disables recording |

## history

```bash
python -m cli history [--grep TEXT]
```

Every command that **succeeds** (exit code 0) gets its own folder
`out/<hash>/` (`history.dir` in the config; the environment variable
`ROADGEN_HISTORY` overrides it, and `ROADGEN_HISTORY=off` turns recording
off). The folder holds everything the command wrote, plus `run.yaml`. A
command that fails leaves no folder and no output: it runs in a temporary
folder, which only becomes `out/<hash>/` on success. Commands that only list
things (`generators`, `maps`, `history`) are not recorded.

`run.yaml` defines the run, with only what differs from the defaults:

```yaml
hash: 3fa2c81b9d04
note: my run
recorded: '2026-10-25T18:00:00+00:00'
cli: python -m cli pipeline lsystem --preset city --maps --seed 7 -o city
command: [pipeline, lsystem]
args: {maps: true, output: city, seed: 7}      # arguments that differ from their defaults
config:                                        # parameters that differ from defaults.yaml
  generators:
    lsystem: {preset: city}
```

The hash is a hash of `command`, `args` and `config`. Different ways of
writing the same run therefore share one folder: an explicit default, flags
in another order, `--set rotation_deg=3` or the same value in
a `--config` file, `-v` or another `--note`. Running the same inputs again
replaces the folder and keeps its note. A parameter file's content
(`--params`) goes into `config`, so the run doesn't depend on the file
afterwards.

`history` lists the runs, oldest first, with their notes and command lines;
`--grep` keeps the runs whose note or command contains TEXT. Each successful
command also logs its folder:

```
INFO roadgen.cli: run 3fa2c81b9d04: out/3fa2c81b9d04
```

## replay

```bash
python -m cli replay HASH [--show]
python -m cli HASH                 # the same
```

Runs a recorded run again from its `run.yaml`. A unique prefix of 4 or more
digits is enough. It uses today's code and defaults with the run's
non-default arguments and parameters, so the result follows the pipeline
as it changes. The outputs replace the folder's. `--show` prints the
command and the folder instead of running it. A command that reads files
reads them from where they are now, so replay the runs that write them
first. See [runs/README.md](../runs/README.md) for the example runs.

## run

```bash
python -m cli run out/HASH/run.yaml     # the same as python -m cli HASH
python -m cli run my_run.yaml           # a run config written by hand
```

Runs a file of the `run.yaml` form. Only `command` is required; `args` uses
the argument names (as in `run.yaml`), and `config` holds parameters as in
`defaults.yaml`. Copying a run's `run.yaml` and editing it is a way to make
a new run; it is recorded under its own hash.

```yaml
command: pipeline lsystem
args: {maps: true, allow_traps: true}
config:
  seed: 4
  generators:
    lsystem: {preset: city, branch_prob: 0.8}
  maps:
    terrain: {noise: {rivers: 2}}
```
