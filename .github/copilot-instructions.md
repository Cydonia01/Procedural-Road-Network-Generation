# Project: Procedural Road Network Generation for esmini

We are building a Python pipeline that procedurally generates road networks
and converts them to ASAM OpenDRIVE (.xodr). The aim is the road network: the
generated .xodr (with its HD-map content) is the product. Traffic simulation
and training self-driving cars are not aims of this project. We do NOT use
OpenStreetMap; all networks are generated.

Pipeline:
  Input maps (terrain, elevation, population, style) -> Generator (optional input)
  Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validators, drive-check, metrics
                                                       -> presentation: scene (ground, buildings),
                                                          esmini `view` (odrviewer with its own cars),
                                                          optional .xosc (scenario builder)

Everything after the .xodr is for presentation only. When a fix would trade the
.xodr's correctness for looks, the .xodr wins. Scalability (bigger cities) is
not a current goal: 4000 x 4000 m networks already work.

Architecture rules:
- Generators only output a RoadGraph. They never touch OpenDRIVE.
- The OpenDRIVE builder only consumes a RoadGraph. It knows nothing about which generator produced it.
- Every stage is runnable from the CLI and testable in isolation.

Tech stack:
- Python 3.10+, type hints everywhere, dataclasses for models.
- numpy, shapely, networkx, matplotlib, lxml, pytest.
- `scenariogeneration` package (xodr + xosc modules) for writing OpenDRIVE/OpenSCENARIO XML.
  Wrap it behind our own builder classes so it can be replaced later.
- esmini binaries (odrviewer, esmini) and its RoadManager library are external
  tools, used to view networks and to drive-check them; their path comes from
  env var ESMINI_HOME or a config file. Never hard-code paths.

Repo layout:
  roadgen/        (full module map: docs/architecture.md#module-map)
    graph/        model.py, io.py, plot.py
    maps/         base.py, model.py, noise.py, terrain.py, elevation.py, population.py, style.py,
                  image.py, plot.py, align.py, drape.py, annotate.py
    generators/   base.py, lsystem.py, patterns.py, hierarchy.py, junction.py, repair.py,
                  transforms/ (culdesac, stagger, roundabout, interchange)
    geometry/     planar.py, junction_space.py, design.py, primitives.py, clothoid.py, chains.py,
                  alignment.py, profiles.py, vertical.py, grade_separation.py, cross_section.py
    opendrive/    builder.py, lanes.py, junctions.py, marks.py, control.py, furniture.py, lights.py,
                  hdmap.py, plot.py
    validate/     connectivity.py, topology.py, continuity.py, dynamics.py, clearance.py, hdmap.py,
                  schema.py
    metrics/      geometry.py, network.py, hdmap.py, sweep.py
    scenario/     xosc_builder.py
    sim/          esmini.py, drive_check.py, screenshot.py
    scene/        ground.py, buildings.py, parcels.py, junctions.py, osgt.py
    config.py, history.py, defaults.yaml
  cli.py
  tests/
  docs/
  out/            (generated files, git-ignored)

OpenDRIVE conventions we follow:
- Target OpenDRIVE 1.6 (or lower if esmini complains); right-hand traffic.
- Lane ids: negative = right of reference line, positive = left, 0 = center lane.
- Roads stop at the junction boundary; junction interiors are covered only by connecting roads.
- Every road/junction id is unique; links use correct contactPoint (start/end).
- Units: meters, radians, m/s.

Coding rules:
- Small, pure functions; no global state; deterministic output given a seed.
- Write pytest tests for every new module.
- Log with the `logging` module, not print.
- Don't implement features from later phases unless asked.
- Don't keep a replaced implementation behind a flag: when a better one
  replaces it, remove the old path, its parameters and its tests.
