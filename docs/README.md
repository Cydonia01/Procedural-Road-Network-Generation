# Documentation

New here? Read [Getting started](getting-started.md), then
[Architecture](architecture.md). After that, use the tables below to jump to
what you need.

## By task

| I want to … | Read |
|-------------|------|
| Install and run the first network | [Getting started](getting-started.md) |
| Understand how the pieces fit | [Architecture](architecture.md) |
| Look up a command or flag | [CLI reference](cli-reference.md) |
| Change parameters, or use a `--config` file | [CLI reference → Parameters](cli-reference.md#parameters-and---config) |
| Find, rerun or share a run | [CLI reference → Run folders](cli-reference.md#run-folders-history-replay-run) |
| Generate a city (grids, patterns, roundabouts, freeways …) | [lsystem](lsystem.md) |
| Generate one unusual junction or interchange | [Generators → junction](generators.md#junction-one-junction-per-seed) |
| Grow on terrain, or use real maps | [Input maps](maps.md) |
| Understand road shapes, curves and speeds | [Road geometry](geometry.md) |
| Understand slopes, bridges, tunnels and overpasses | [Heights](heights.md) |
| Understand the `.xodr` the builder writes | [OpenDRIVE builder](opendrive.md) |
| Know what is checked, and measure a network | [Validation and metrics](validation.md) |
| Put traffic on a network and run esmini | [Scenarios and simulation](scenarios-and-simulation.md) |
| Add ground and buildings to the 3D view | [3D scene](scene.md) |
| Run in Docker, or open GUI windows from it | [Docker](docker.md) |
| Add a generator, map or other component, or run tests | [Development](development.md) |
| See example commands for every stage | [Example runs](../runs/README.md) |

## By source folder

| Code | Page |
|------|------|
| `cli.py`, `roadgen/config.py`, `roadgen/history.py`, `roadgen/defaults.yaml` | [CLI reference](cli-reference.md) |
| `roadgen/graph/` | [RoadGraph](road-graph.md) |
| `roadgen/maps/` (except `align.py`, `drape.py`) | [Input maps](maps.md) |
| `roadgen/maps/align.py`, `roadgen/maps/drape.py` | [Heights](heights.md) |
| `roadgen/generators/lsystem.py`, `patterns.py` | [lsystem](lsystem.md) |
| `roadgen/generators/base.py`, `junction.py`, `repair.py`, `transforms/` | [Generators](generators.md) |
| `roadgen/geometry/` | [Road geometry](geometry.md); `vertical.py` and `grade_separation.py` in [Heights](heights.md) |
| `roadgen/opendrive/` | [OpenDRIVE builder](opendrive.md) |
| `roadgen/validate/`, `roadgen/metrics/`, `roadgen/sim/drive_check.py` | [Validation and metrics](validation.md) |
| `roadgen/scenario/`, `roadgen/sim/runner.py`, `roadgen/sim/esmini.py` | [Scenarios and simulation](scenarios-and-simulation.md) |
| `roadgen/scene/` | [3D scene](scene.md) |
| `tests/` | [Development](development.md#tests) |
| `Dockerfile`, `compose.yaml`, `docker/` | [Docker](docker.md) |

## Pipeline pages in data-flow order

[Input maps](maps.md) → [lsystem](lsystem.md) / [Generators](generators.md)
→ [RoadGraph](road-graph.md) → [Road geometry](geometry.md) →
[Heights](heights.md) → [OpenDRIVE builder](opendrive.md) →
[Validation](validation.md) → [Scenarios and simulation](scenarios-and-simulation.md)
→ [3D scene](scene.md)
