# Procedural-Road-Network-Generation

**roadgen** is a Python pipeline that procedurally generates road networks as
training and test environments for autonomous driving. It writes them as
ASAM OpenDRIVE (`.xodr`), fills them with traffic as OpenSCENARIO (`.xosc`),
and runs them in [esmini](https://github.com/esmini/esmini). All networks are
generated; OpenStreetMap is not used.

```
Input maps (terrain, elevation, population, style; procedural or from images)
   |
Generator -> RoadGraph (JSON) -> OpenDRIVE builder -> .xodr -> validators
                                                    -> scenario builder -> .xosc -> esmini
```

## What it can generate

- **Cities** (`lsystem`, after Parish & Müller): grids, Manhattan-style one-way
  grids with a ring road, curved and organic streets, radial (Paris),
  contour-following and suburban patterns blended per district, cul-de-sacs,
  staggered and many-leg junctions, roundabouts, freeways with overpasses,
  tunnels and diamond interchanges.
- **Single junctions** (`junction`): any 3–7 leg shape, offset T pairs,
  roundabouts, diamond and cloverleaf interchanges, for long-tail data.
- **Terrain:** networks grow on generated or real maps. Highways head for
  population, bridge rivers and tunnel through hills, and streets stop at
  water, parks and steep slopes. Roads follow the terrain in height.
- **Drivable geometry:** curvature-continuous roads sized for their design
  speed (AASHTO), banking, lane tapers, sloped junctions. These are checked by
  validators and by driving every lane and junction movement with esmini's
  RoadManager.
- **Traffic and 3D:** scenarios with hundreds to thousands of vehicles, and
  optional ground and buildings for the esmini view.

## Quick start

```bash
docker compose build && docker compose run --rm dev      # or a native install, see below
pytest
python -m cli pipeline lsystem --preset grid --set extent=[750,240] --seed 1 --drive-check --simulate -o first
python -m cli pipeline lsystem --preset city --maps --allow-traps --ground --buildings -o hills
```

Each run writes into its own folder `out/<hash>/` and logs where.
[Getting started](docs/getting-started.md) covers a native install, viewing
results in the esmini GUI and what each output file is.

## Documentation

Start at the **[documentation index](docs/README.md)**, which finds the right
page by task or by source folder. The main pages:

| Page | Covers |
|------|--------|
| [Getting started](docs/getting-started.md) | Install, first run, viewing results |
| [Architecture](docs/architecture.md) | Pipeline, design rules, module map, output files |
| [CLI reference](docs/cli-reference.md) | Every command, parameters and `--config`, run folders |
| [Example runs](runs/README.md) | Recorded commands for every stage, replayable by hash |
| [Development](docs/development.md) | Tests, coding rules, project history, known issues |

## Layout

```
cli.py          command-line entry point (python -m cli ...)
roadgen/        the package (module map: docs/architecture.md#module-map)
  defaults.yaml every default parameter
tests/          pytest suite and RoadGraph fixtures
docs/           documentation
runs/           example runs and their inputs
plans/          plans, design discussions and reviews (traceability)
files/          reference papers
```
