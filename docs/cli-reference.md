# CLI reference

All commands run as `python -m cli <command> [options]` from the repo root.
Run `python -m cli <command> --help` for the authoritative list.

Global option: `-v, --verbose` enables debug logging. It must come **before**
the command, e.g. `python -m cli -v pipeline ...`.

Exit codes: `0` on success, `1` when a validation, check or simulation failed.

## Overview

| Command | Purpose |
|---------|---------|
| [`generators`](#generators) | List generators, presets and parameters |
| [`generate`](#generate) | Generator → RoadGraph JSON |
| [`pipeline`](#pipeline) | Generate → plot → `.xodr` → validate (→ buildings → drive-check → simulate) |
| [`plot-graph`](#plot-graph) | RoadGraph JSON → image |
| [`build-xodr`](#build-xodr) | RoadGraph JSON → `.xodr` + validation |
| [`drive-check`](#drive-check) | Drive every junction movement with esmini's RoadManager |
| [`view`](#view) | Open an `.xodr` in esmini's `odrviewer` |
| [`scenario`](#scenario) | `.xodr` → `.xosc` with many vehicles |
| [`simulate`](#simulate) | Run an `.xosc` in esmini and summarize |
| [`bench`](#bench) | Scalability sweep from a YAML config |
| [`bench-plot`](#bench-plot) | Plot a benchmark `results.csv` |

## generators

```bash
python -m cli generators
```

## generate

```bash
python -m cli generate <manhattan|lsystem> [generator options] [-o out.json]
```

Default output: `out/<name>[_<preset>]_s<seed>.json`.

### Options for every generator

| Option | Meaning |
|--------|---------|
| `--seed N` | Random seed (default 0) |
| `--preset NAME` | Start from a named parameter set |
| `--params FILE.yaml` | Parameter file (fields, or `preset:` + `params:`) |
| `--set KEY=VALUE` | Override one parameter. Repeatable, and the value is parsed as YAML |

### Manhattan-only flags

These map 1:1 onto [`ManhattanParams`](generators.md#manhattan-parametric-grid-manhattanpy):

| Flag | Field |
|------|-------|
| `--avenues`, `--streets` | `n_avenues`, `n_streets` (required without `--preset`) |
| `--avenue-spacing`, `--street-spacing` | spacing [m] |
| `--rotation` | `rotation_deg` |
| `--origin X Y` | `origin` |
| `--avenue-lanes FWD BWD`, `--street-lanes FWD BWD` | lane tuples |
| `--lane-width`, `--avenue-speed`, `--street-speed` | [m], [m/s] |
| `--jitter` | node noise [m] |
| `--edge-drop` | `edge_drop_prob` |
| `--avenue-oneway`, `--street-oneway` | `none` / `alternating` |
| `--boundary` | `dead_end` / `loop` |
| `--ring-offset`, `--ring-lanes FWD BWD` | ring road |

## pipeline

```bash
python -m cli pipeline <generator> [generator options] [pipeline options] [-o PREFIX]
```

Writes `PREFIX.json`, `.png`, `.xodr` and `.roadmap.json`, then runs all
validators and logs the time each stage took. Default prefix:
`out/<name>[_<preset>]_s<seed>`.

| Option | Meaning |
|--------|---------|
| `--allow-traps` | Report traffic traps / unreachable roads as warnings instead of errors |
| `--xsd FILE` | OpenDRIVE XSD (default `$OPENDRIVE_XSD`) |
| `--junction-margin M` | Extra junction clearance [m] (default 2.0) |
| `--buildings` | Also write `PREFIX_buildings.osgt` |
| `--drive-check` | Also run drive-check (only if there are no errors so far) |
| `--simulate` | Also write `PREFIX.xosc` and run it headless (only if there are no errors so far) |
| `--sim-vehicles N` | Vehicles for `--simulate` (default 50) |
| `--sim-duration S` | Simulated seconds for `--simulate` (default 30) |

## plot-graph

```bash
python -m cli plot-graph INPUT.json OUTPUT.png [--node-ids]
```

## build-xodr

```bash
python -m cli build-xodr INPUT.json OUTPUT.xodr [--xsd FILE] [--junction-margin M]
```

Traffic connectivity problems are only warnings here, since hand-made test
graphs often have dead ends.

## drive-check

```bash
python -m cli drive-check NETWORK.xodr      # needs NETWORK.roadmap.json
```

## view

```bash
python -m cli view NETWORK.xodr [--density CARS_PER_100M]   # default density 1
```

Needs a display. In Docker, the `dev` service has none (`odrviewer` exits
immediately), so use the GUI service for your OS:

```bash
docker compose run --rm gui python -m cli view NETWORK.xodr       # Linux
docker compose run --rm gui-mac python -m cli view NETWORK.xodr   # macOS (XQuartz, see docker.md)
```

The same applies to `simulate --gui`.

## scenario

```bash
python -m cli scenario NETWORK.xodr -o OUT.xosc --vehicles N [options]
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

## bench

```bash
python -m cli bench CONFIG.yaml [--out-dir DIR] [--no-plot]
```

## bench-plot

```bash
python -m cli bench-plot RESULTS.csv [--out-dir DIR]     # default: <results dir>/plots
```

## Environment variables

| Variable | Used by | Meaning |
|----------|---------|---------|
| `ESMINI_HOME` | everything esmini-related | esmini root containing `bin/` (falls back to `./esmini`) |
| `OPENDRIVE_XSD` | schema check | Path to the ASAM OpenDRIVE XSD |
