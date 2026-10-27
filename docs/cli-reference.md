# CLI reference

Every command runs as `python -m cli <command> [options]` from the repo root.
`python -m cli <command> --help` is the authoritative list and shows the
defaults in effect. Exit code `0` means success, and `1` means a validation,
check or simulation failed.

| Command | Purpose |
|---------|---------|
| **Generate** | |
| [`generators`](#generators-maps) | List generators, presets and parameters |
| [`generate`](#generate) | Generator → RoadGraph JSON |
| [`pipeline`](#pipeline) | Generate → plot → `.xodr` → validate, plus optional maps, scene, drive-check and simulation |
| **Input maps** | |
| [`maps`](#generators-maps) | List map generators, presets and parameters |
| [`map`](#map) | One input map (`.npz` + `.png`) |
| [`plot-map`](#plot-map-plot-graph) | Map `.npz` → image |
| **Graph steps** | |
| [`align`](#align-drape-separate) | Cap bend radii where a fitted curve would leave legal ground |
| [`drape`](#align-drape-separate) | Heights from an elevation map into a RoadGraph |
| [`separate`](#align-drape-separate) | Overpasses where roads cross without a junction |
| [`plot-graph`](#plot-map-plot-graph) | RoadGraph JSON → image |
| **OpenDRIVE and checks** | |
| [`build-xodr`](#build-xodr) | RoadGraph JSON → `.xodr` + validation |
| [`drive-check`](#drive-check) | Drive every junction movement and lane with esmini's RoadManager |
| [`metrics`](#metrics) | Geometry and network metrics, build-failure sweep |
| **Scene and simulation** | |
| [`ground`](#ground) | 3D ground mesh (`.osgt`) from maps |
| [`scenario`](#scenario) | `.xodr` → `.xosc` with many vehicles |
| [`simulate`](#simulate) | Run an `.xosc` in esmini and summarize it |
| [`view`](#view) | Open an `.xodr` in esmini's `odrviewer` |
| **Runs** | |
| [`history`, `replay`, `run`](#run-folders-history-replay-run) | List, rerun or define recorded runs |

## Global options

These go **before** the command, e.g. `python -m cli -v pipeline ...`:

| Option | Meaning |
|--------|---------|
| `-v, --verbose` | Debug logging |
| `--config FILE` | YAML file overriding default parameters ([below](#parameters-and---config)) |
| `--note TEXT` | Description stored in the run's `run.yaml` |
| `--no-history` | Don't record the run: no `out/<hash>/` folder, and `-o` is a path |

| Environment variable | Meaning |
|----------------------|---------|
| `ESMINI_HOME` | esmini root containing `bin/` (fallback `./esmini`) |
| `OPENDRIVE_XSD` | ASAM OpenDRIVE XSD for the schema check |
| `ROADGEN_HISTORY` | Root of the run folders (default `out`); `off` disables recording |

## Parameters and `--config`

Every default lives in [`roadgen/defaults.yaml`](../roadgen/defaults.yaml),
one section per component. Examples: `generators.<name>`,
`maps.<kind>.<name>`, `drape`, `design`, `opendrive`, `scenario`,
`buildings`, `esmini`, `pipeline`.

Parameters are layered. Each layer overrides the one before it:

1. `defaults.yaml`;
2. the preset (`--preset NAME`, or `preset:` in a params or config file);
3. the `--config FILE` given before the command;
4. the `--params FILE.yaml` given to a generator or map;
5. command-line flags, `--set KEY=VALUE` and `--map-set KIND.KEY=VALUE`.

A `--config` file uses the nesting of `defaults.yaml` and only needs the keys
it changes:

```yaml
seed: 42
generators:
  lsystem:
    preset: manhattan_like
    lane_width: 3.25
maps:
  grid: {cell_size: 5.0}
  terrain:
    noise: {water_fraction: 0.25}
scenario:
  speed_range: [5.0, 10.0]
```

A `--params` file holds a generator's (or map generator's) fields directly,
or `preset:` plus a `params:` mapping:

```yaml
preset: manhattan_like
params:
  extent: [750, 560]
  angle_jitter_deg: 1.5
```

- `--set` values are parsed as YAML, so `--set origin=[10,20]` works.
- YAML lists become tuples where the dataclass expects tuples.
- Unknown keys are an error in every layer.

All layers end up in the run's config: the `config` part of
[`run.yaml`](#run-folders-history-replay-run), which holds only the
differences from `defaults.yaml`.

The `manhattan` generator no longer exists. Use `lsystem --preset
manhattan_like`. Replaying a run of it, or a config with a
`generators.manhattan` section, stops with a message saying so.

## Outputs

In a recorded run (the default), every output goes into the run's folder
`out/<hash>/`, and `-o` and the output arguments are **file names**. A path
there is an error. With `--no-history`, no folder is made and outputs go
where `-o` says.

To read an earlier run's output, give its path in that run's folder, e.g.
`--map-file terrain=out/0bafbe99d5d4/coast_terrain.npz`.

## generators, maps

```bash
python -m cli generators      # road generators, presets, every parameter with its default
python -m cli maps            # the same for map generators
```

## generate

```bash
python -m cli generate <lsystem|junction> [options] [-o OUT.json]     # default: <name>[_<preset>]_s<seed>.json
```

| Option | Meaning |
|--------|---------|
| `--seed N` | Random seed (default 0) |
| `--preset NAME` | Start from a named parameter set |
| `--params FILE.yaml` | Parameter file |
| `--set KEY=VALUE` | Override one parameter (repeatable) |
| `--map-file KIND=FILE.npz` | Input map to grow on (`terrain`, `elevation`, `population`, `style`). Repeatable. Only `lsystem` reads maps |
| `--flat` | No heights, even with an elevation map |

Generator parameters have no flags of their own. Use `--set`, e.g. `--set
extent=[500,240] --set boundary=ring`. The parameters are listed in
[lsystem](lsystem.md) and [Generators](generators.md).

Before the graph is written, three steps run ([Heights](heights.md)):

- with any maps, it is aligned;
- with an elevation map, it is also draped (unless `--flat`);
- roads that cross without a junction are always grade-separated.

## pipeline

```bash
python -m cli pipeline <generator> [generator options] [pipeline options] [-o PREFIX]
```

Takes every `generate` option. It writes `PREFIX.json`, `.png`, `.xodr` and
`.roadmap.json`, runs all [validators](validation.md), and logs the time of
each stage. Default prefix: `<name>[_<preset>]_s<seed>`.

| Option | Meaning |
|--------|---------|
| `--allow-traps` | Report traffic traps and unreachable roads as warnings, not errors |
| `--xsd FILE` | OpenDRIVE XSD (default `$OPENDRIVE_XSD`) |
| `--junction-margin M` | Extra junction clearance [m] (default 2) |
| `--maps` | Build all map kinds the generator reads, with the generators named in `maps.pipeline`, and grow on them. Writes `PREFIX_<kind>.npz` / `.png` |
| `--terrain`, `--elevation`, `--population`, `--style NAME` | Build one kind with generator `NAME` (overrides `--maps` for that kind) |
| `--map-set KIND.KEY=VALUE` | Override one map parameter, e.g. `--map-set terrain.rivers=2`. Repeatable |
| `--map-file KIND=FILE.npz` | Load one kind from a file; wins over a generator for that kind |
| `--buildings` | Also write `PREFIX_buildings.osgt` ([scene](scene.md)) |
| `--ground` | Also write `PREFIX_scene.osgt`: ground plus buildings. Needs an elevation map |
| `--drive-check` | Also run [drive-check](#drive-check), if there are no errors so far |
| `--simulate` | Also write `PREFIX.xosc` and run it headless, if there are no errors so far |
| `--sim-vehicles N`, `--sim-duration S` | For `--simulate` (default 50 vehicles, 30 s; 600 s timeout) |

## map

```bash
python -m cli map <terrain|elevation|population|style> <generator> [options] [-o PREFIX]
```

Writes `PREFIX.npz` and `PREFIX.png`. Default prefix:
`<kind>_<name>[_<preset>]_s<seed>`.

| Option | Meaning |
|--------|---------|
| `--seed`, `--preset`, `--params`, `--set` | As for `generate` |
| `--origin X Y`, `--size W H`, `--cell-size M` | Grid: lower-left corner, size and cell size [m] (default `maps.grid`: 0 0, 3000 3000, 10) |
| `--on KIND=FILE.npz` | Build on an earlier kind, e.g. `map elevation fbm --on terrain=coast.npz`. Repeatable; not for `terrain`. Resampled if on another grid |
| `--graph FILE.json` | Draw this RoadGraph on the preview |

## plot-map, plot-graph

```bash
python -m cli plot-map INPUT.npz [OUTPUT.png] [--graph FILE.json]     # default: <input>.png
python -m cli plot-graph INPUT.json [OUTPUT.png] [--node-ids]          # default: <input>.png
```

## align, drape, separate

`generate` and `pipeline` run these steps automatically. The commands run
them on an existing graph. Each step is explained in [Heights](heights.md).

```bash
python -m cli align NET.json --map-file terrain=T.npz [--map-file elevation=E.npz] [-o OUT.json]   # NET.aligned.json
python -m cli drape NET.json --map-file elevation=E.npz [--map-file terrain=T.npz] [-o OUT.json]   # NET.draped.json
python -m cli separate NET.json [-o OUT.json]                                                      # NET.separated.json
```

`drape` needs an elevation map. A terrain map adds bridges. Their parameters
are the config sections `align:`, `drape:` and `grade_separation:`.

## build-xodr

```bash
python -m cli build-xodr INPUT.json [OUTPUT.xodr] [--xsd FILE] [--junction-margin M]   # default: <input>.xodr
```

Builds and validates. Traffic connectivity problems are only warnings here,
since hand-made test graphs often have dead ends.

## drive-check

```bash
python -m cli drive-check NETWORK.xodr      # needs NETWORK.roadmap.json next to it
```

See [Validation → Drive-check](validation.md#drive-check).

## metrics

```bash
python -m cli metrics A.xodr [B.xodr ...] [--graph A.json ...] [--json OUT.json]
python -m cli metrics --sweep N [--first-seed 0] [--generator lsystem] [--preset city]
```

- Geometry metrics of each `.xodr`.
- `--graph` (repeatable) adds the network metrics of a RoadGraph.
- `--sweep N` generates N networks, as `pipeline --maps` would, and counts the
  ones the builder rejects.

The report is informational: the command succeeds whatever the numbers are.
Thresholds are in `defaults.yaml` under `metrics` and `network_metrics`. The
metrics are explained in
[Validation → Metrics](validation.md#geometry-metrics).

## ground

```bash
python -m cli ground [OUT.osgt] --map-file elevation=E.npz [--map-file terrain=T.npz] \
    [--graph NET.json [--buildings]] [--seed N] [--junction-margin M]          # default: scene.osgt
```

A ground mesh from the maps. `--graph` (a draped network) levels it under the
roads, and `--buildings` adds that network's buildings. `--junction-margin`
must match the `.xodr`. Parameters go in `ground:`. See [3D scene](scene.md).

## scenario

```bash
python -m cli scenario NETWORK.xodr --vehicles N [options] [-o OUT.xosc]   # default: <input>.xosc
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--vehicles N` | required | Number of vehicles |
| `--seed N` | 0 | |
| `--duration S` | 60 | Simulated time [s] |
| `--mode` | `random_lanes` | or `explicit_routes` ([spawn modes](scenarios-and-simulation.md#spawn-modes)) |
| `--speed-min`, `--speed-max` | 8.0, 13.9 | Target speed range [m/s] |
| `--min-gap M` | 10 | Minimum gap between vehicles in a lane [m] |
| `--graph FILE` | `<input>.json` if present | RoadGraph for consistency checks |
| `--scenegraph FILE` | – | 3D scene `.osgt` shown with the roads |

## simulate

```bash
python -m cli simulate SCENARIO.xosc [options]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--gui` | off | Show the esmini window (needs a display) |
| `--camera MODE` | `top` | With `--gui`: `orbit`, `fixed`, `flex`, `flex-orbit`, `top`, `driver` |
| `--timestep S` | 0.05 | Fixed timestep [s] |
| `--no-record`, `--no-csv` | – | Skip the `.dat` recording / the `.csv` log |

It logs the vehicles, simulated and wall time, real-time factor, load time,
peak memory, and esmini errors and warnings. The `.log`, `.dat` and `.csv` go
into the run's folder (next to the `.xosc` with `--no-history`).

## view

```bash
python -m cli view NETWORK.xodr [--density CARS_PER_100M] [--scenegraph FILE.osgt] [--duration S]
```

| Option | Meaning |
|--------|---------|
| `--density` | Random cars per 100 m of lane (default 1) |
| `--scenegraph FILE.osgt` | Ground and/or buildings to show with the roads |
| `--duration S` | Close after S seconds (default: stays open) |

Needs a display. In Docker, use the `gui` or `gui-mac` service
([Docker](docker.md)). `view` is not recorded as a run.

## Run folders (history, replay, run)

Every command that **succeeds** gets its own folder `out/<hash>/` (the root
is `history.dir`, overridden by `$ROADGEN_HISTORY`). It holds everything the
command wrote, plus `run.yaml`:

- A failing command runs in a temporary folder and leaves nothing behind.
- Listing commands (`generators`, `maps`, `history`) and `view` are not recorded.
- Each successful command logs its folder, e.g.
  `INFO roadgen.cli: run 3fa2c81b9d04: out/3fa2c81b9d04`.

```yaml
# out/3fa2c81b9d04/run.yaml
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

The hash covers `command`, `args` and `config` only. The same run therefore
always gets the same folder, however it was typed: flag order, an explicit
default, `--set` versus `--config`, `-v` or a different `--note`. Running the
same inputs again replaces the folder and keeps its note. A `--params` file's
content is copied into `config`, so the run no longer depends on the file.

```bash
python -m cli history [--grep TEXT]     # runs, oldest first, with note and command
python -m cli replay HASH [--show]      # rerun (a unique prefix of 4+ digits is enough)
python -m cli HASH                      # the same
python -m cli run out/HASH/run.yaml     # the same, from the file
python -m cli run my_run.yaml           # a run file written by hand
```

- **Replay** uses today's code and defaults with the run's non-default
  arguments and parameters, so its result follows the pipeline as the code
  changes. `--show` only prints the command and the folder.
- **Input files.** A command reads its input files from where they are now,
  so replay the runs that write them first.
- **Run files.** Only `command` is required. `args` uses argument names as in
  `run.yaml`, and `config` uses the nesting of `defaults.yaml`. Copying a
  `run.yaml` and editing it is the easiest way to make a new run:

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

The example runs are listed in [runs/README.md](../runs/README.md).
