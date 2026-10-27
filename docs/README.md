# Documentation

Detailed documentation for **roadgen**, the procedural road network pipeline.
The [top-level README](../README.md) has the short version.
These pages explain how each part works and why it was built that way.

## Start here

| Page | What it covers |
|------|----------------|
| [Getting started](getting-started.md) | Install with Docker or natively, then run the first pipeline |
| [Architecture](architecture.md) | Pipeline stages, design rules, module map, files produced |
| [CLI reference](cli-reference.md) | Every `python -m cli` command and flag |
| [Example runs](../runs/README.md) | Recorded example commands for every phase, replayable by hash |
| [Input maps and heights walkthrough](implementation-guide.md) | Reading order for the maps / heights code, every new command tried phase by phase |

## Pipeline stages

Read these in pipeline order:

| Page | Stage |
|------|-------|
| [RoadGraph data model](road-graph.md) | The generator-agnostic graph and its JSON format |
| [Input maps](maps.md) | Terrain, elevation, population and style maps (procedural or from images), data sources |
| [Generators](generators.md) | `lsystem` (grids, pattern fields, connectivity, cul-de-sacs), presets, parameter files, graph transforms, writing a new generator |
| [Road geometry](geometry.md) | Neutral geometry package: design-speed rules, line / arc / spiral pieces, clothoid transitions, junction trims |
| [OpenDRIVE builder](opendrive.md) | RoadGraph → `.xodr`: roads, lanes, junctions, id scheme, `.roadmap.json` |
| [Validation](validation.md) | Traffic connectivity, topology, continuity, clearance, XSD schema, esmini drive-check, geometry and network metrics |
| [Scenarios & simulation](scenarios-and-simulation.md) | `.xosc` generation, running esmini, 3D buildings |

## Working on the project

| Page | What it covers |
|------|----------------|
| [Docker environment](docker.md) | Container setup on Linux and macOS, GUI forwarding |
| [Development guide](development.md) | Tests, fixtures, coding rules, project phases, known issues |
