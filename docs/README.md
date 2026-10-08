# Documentation

Detailed documentation for **roadgen**, the procedural road network pipeline
of the CMPE492 project. The [top-level README](../README.md) has the short
version. These pages explain how each part works and why it was built that way.

## Start here

| Page | What it covers |
|------|----------------|
| [Getting started](getting-started.md) | Install with Docker or natively, then run the first pipeline |
| [Architecture](architecture.md) | Pipeline stages, design rules, module map, files produced |
| [CLI reference](cli-reference.md) | Every `python -m cli` command and flag |

## Pipeline stages

Read these in pipeline order:

| Page | Stage |
|------|-------|
| [RoadGraph data model](road-graph.md) | The generator-agnostic graph and its JSON format |
| [Generators](generators.md) | `manhattan` and `lsystem`, presets, parameter files, writing a new generator |
| [OpenDRIVE builder](opendrive.md) | RoadGraph → `.xodr`: roads, lanes, junctions, id scheme, `.roadmap.json` |
| [Validation](validation.md) | Traffic connectivity, topology, continuity, XSD schema, esmini drive-check |
| [Scenarios & simulation](scenarios-and-simulation.md) | `.xosc` generation, running esmini, 3D buildings |
| [Scalability benchmark](benchmarking.md) | Sweep config, results CSV, statuses, plots |

## Working on the project

| Page | What it covers |
|------|----------------|
| [Docker environment](docker.md) | Container setup on Linux and macOS, GUI forwarding |
| [Development guide](development.md) | Tests, fixtures, coding rules, project phases, known issues |
