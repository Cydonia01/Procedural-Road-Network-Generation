# Getting started

## Install

### Docker (recommended)

Python, every dependency and esmini v3.9 come preinstalled, the same on
Linux and macOS. You need Docker with the Compose plugin (Docker Desktop on
macOS).

```bash
docker compose build              # once, and again after pyproject.toml changes
docker compose run --rm dev       # shell in /workspace (your checkout, mounted)
```

Every command below works inside that shell as written. For a single command,
use `docker compose run --rm -T dev <command>`. Details are in
[Docker](docker.md).

### Native

You need Python 3.10+ and an esmini release (tested with v3.9; `drive-check`
is calibrated against it).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Download `esmini-bin_<OS>.zip` from
<https://github.com/esmini/esmini/releases>. Then either unpack it so that
`./esmini/bin/esmini` exists (the folder is git-ignored), or point
`ESMINI_HOME` at it:

```bash
export ESMINI_HOME=/path/to/esmini                    # contains bin/esmini, bin/odrviewer, ...
export OPENDRIVE_XSD=/path/to/opendrive_16_core.xsd   # optional: enables the schema check
```

## First run

```bash
pytest                                     # is everything installed?
python -m cli generators                   # generators, presets and parameters

# Generate a small grid, plot it, build the .xodr, validate it,
# drive every junction movement and run a short simulation
python -m cli pipeline lsystem --preset grid --set extent=[750,240] --seed 1 \
    --drive-check --simulate -o first
```

A passing run ends with `INFO roadgen.cli: run <hash>: out/<hash>`. That
folder holds:

| File | Contents |
|------|----------|
| `first.json` | The [RoadGraph](road-graph.md) |
| `first.png` | Top-down plot of the graph |
| `first.xodr` | OpenDRIVE road network |
| `first.roadmap.json` | Graph id → OpenDRIVE id mapping, every junction movement |
| `first.xosc` | Traffic scenario (from `--simulate`) |
| `run.yaml` | The command and its non-default parameters, enough to rerun it |

Every output file is listed in
[Architecture → Output files](architecture.md#output-files), and run folders
are explained in the
[CLI reference](cli-reference.md#run-folders-history-replay-run).

## Viewing results

`view` opens a network in esmini's `odrviewer` with random cars, and
`simulate --gui` shows a scenario running:

```bash
python -m cli view out/<hash>/first.xodr
python -m cli simulate out/<hash>/first.xosc --gui
```

Both need a display. The Docker `dev` service has none: `odrviewer` exits
immediately without an error. Use the GUI service for your OS instead (setup
in [Docker](docker.md)):

```bash
docker compose run --rm gui     python -m cli view out/<hash>/first.xodr   # Linux
docker compose run --rm gui-mac python -m cli view out/<hash>/first.xodr   # macOS + XQuartz
```

## Next

- More example commands: [Example runs](../runs/README.md)
- Grow a city on terrain: `python -m cli pipeline lsystem --preset city --maps --allow-traps --ground --buildings`,
  explained in [lsystem](lsystem.md) and [Input maps](maps.md)
- How the pipeline fits together: [Architecture](architecture.md)
