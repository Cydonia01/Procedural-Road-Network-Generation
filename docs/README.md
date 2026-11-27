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
| Generate a city (grids, patterns, road hierarchy, roundabouts, freeways …) | [lsystem](lsystem.md) |
| Grow on terrain, or use real maps | [Input maps](maps.md) |
| Understand road shapes, curves and speeds | [Road geometry](geometry.md) |
| Understand slopes, bridges, tunnels and overpasses | [Heights](heights.md) |
| Understand the `.xodr` the builder writes | [OpenDRIVE builder](opendrive.md) |
| Understand the HD-map content (medians, road marks, signs, lights, right of way, turn lanes), or turn it off with `--no-hd` | [OpenDRIVE builder](opendrive.md), [CLI reference → pipeline](cli-reference.md#pipeline) |
| Know what is checked, and measure a network | [Validation and metrics](validation.md) |
| Look at a network in esmini, or take a screenshot | [CLI reference → view](cli-reference.md#view) |
| Add ground and buildings to the 3D view | [3D scene](scene.md) |
| Open a network in Blender (`.glb`) | [3D scene → Blender export](scene.md#blender-export-glb), [CLI reference → export-gltf](cli-reference.md#export-gltf) |
| Texture the `.glb`, render it in Blender | [3D scene → Textures](scene.md#textures), [Scene script](scene.md#scene-script) |
| Run in Docker, or open GUI windows from it | [Docker](docker.md) |
| Add a generator, map or other component, or run tests | [Development](development.md) |
| See example commands for every stage | [Example runs](../runs/README.md) |

## By source folder

| Code | Page |
|------|------|
| `cli.py`, `kadmos/config.py`, `kadmos/history.py`, `kadmos/defaults.yaml`, `kadmos/presets.yaml` | [CLI reference](cli-reference.md) |
| `kadmos/graph/` | [RoadGraph](road-graph.md) |
| `kadmos/maps/` (except `align.py`, `drape.py`) | [Input maps](maps.md) |
| `kadmos/maps/align.py`, `kadmos/maps/drape.py` | [Heights](heights.md) |
| `kadmos/generators/lsystem.py`, `patterns.py`, `hierarchy.py` | [lsystem](lsystem.md) |
| `kadmos/generators/base.py`, `repair.py`, `transforms/` | [Generators](generators.md) |
| `kadmos/geometry/` | [Road geometry](geometry.md); `vertical.py` and `grade_separation.py` in [Heights](heights.md) |
| `kadmos/opendrive/` | [OpenDRIVE builder](opendrive.md) |
| `kadmos/validate/`, `kadmos/metrics/`, `kadmos/sim/drive_check.py` | [Validation and metrics](validation.md) |
| `kadmos/sim/esmini.py`, `kadmos/sim/screenshot.py` | [CLI reference → view](cli-reference.md#view) |
| `kadmos/scene/` | [3D scene](scene.md) |
| `scripts/` | [3D scene → Textures](scene.md#textures), [Scene script](scene.md#scene-script) |
| `tests/` | [Development](development.md#tests) |
| `Dockerfile`, `compose.yaml`, `docker/` | [Docker](docker.md) |

## Pipeline pages in data-flow order

[Input maps](maps.md) → [lsystem](lsystem.md) / [Generators](generators.md)
→ [RoadGraph](road-graph.md) → [Road geometry](geometry.md) →
[Heights](heights.md) → [OpenDRIVE builder](opendrive.md) →
[Validation](validation.md) → [3D scene](scene.md)
