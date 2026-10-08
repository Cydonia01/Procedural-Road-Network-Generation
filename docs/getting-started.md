# Getting started

There are two ways to run the project:

- **Docker (recommended):** Python, every dependency and esmini come preinstalled. Works the same on Linux and macOS.
- **Native:** a Python virtualenv plus an esmini release you download yourself.

## Option A: Docker

Requires Docker with the Compose plugin (Docker Desktop on macOS).

```bash
docker compose build              # once, and again after pyproject.toml changes
docker compose run --rm dev       # shell in /workspace (your checkout)
```

Inside the container, every command below works as written. `ESMINI_HOME` is
already set. To open esmini windows, use the `gui` service (Linux) or `gui-mac`
(macOS with XQuartz) instead of `dev`. See [Docker environment](docker.md).

## Option B: Native install

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

esmini is an external tool. Download a release for your OS from
<https://github.com/esmini/esmini/releases> (`esmini-bin_<OS>.zip`). Either
unpack it so that `./esmini/bin/esmini` exists in the repo (the folder is
git-ignored), or point `ESMINI_HOME` at it:

```bash
export ESMINI_HOME=/path/to/esmini        # must contain bin/esmini, bin/odrviewer, ...
export OPENDRIVE_XSD=/path/to/opendrive_16_core.xsd   # optional, enables the schema check
```

The code was tested against esmini **v3.9**. `drive-check` calibrates its
junction-selector angles against that version.

## First run

```bash
# 1. Is everything installed?
pytest

# 2. List the available generators and their parameters
python -m cli generators

# 3. Full pipeline on a small grid: graph → PNG → .xodr → validation
#    → drive-check → short simulation
python -m cli pipeline manhattan --avenues 4 --streets 4 --seed 1 \
    --drive-check --simulate -o out/first

# 4. Look at the result
#    out/first.png            top-down plot of the graph
#    out/first.xodr           OpenDRIVE road network
#    out/first.roadmap.json   graph-id → OpenDRIVE-id mapping
#    out/first.xosc           traffic scenario (from --simulate)
python -m cli view out/first.xodr    # natively; in Docker see below
```

A passing run ends with `INFO roadgen.cli: pipeline passed (out/first)`.

Opening a window (`view`, `simulate --gui`) needs a display. The `dev`
service has none, so `odrviewer` exits immediately without an error.
In Docker, use the GUI service for your OS:

```bash
# Linux (X11 or Wayland/XWayland), from a terminal in your desktop session
docker compose run --rm gui python -m cli view out/first.xodr

# macOS: XQuartz running, "Allow connections from network clients" on,
# and `xhost +localhost` run once (see docker.md#macos-dev-and-gui-mac)
docker compose run --rm gui-mac python -m cli view out/first.xodr
```

## Where to go next

- **Change the network:** [Generators](generators.md)
- **Understand the `.xodr` output:** [OpenDRIVE builder](opendrive.md)
- **Run traffic:** [Scenarios & simulation](scenarios-and-simulation.md)
- **Measure scalability:** [Scalability benchmark](benchmarking.md)
