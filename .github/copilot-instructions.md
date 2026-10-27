# Project: Procedural Road Network Generation for esmini

We are building a Python pipeline that procedurally generates road networks,
converts them to ASAM OpenDRIVE (.xodr), generates OpenSCENARIO (.xosc) traffic
scenarios, and runs them in the esmini simulator to train and test autonomous
driving. We do NOT use OpenStreetMap; all networks are generated.

Pipeline:
  Input maps (terrain, elevation, population) -> Generator (optional input)
  Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validator
                                                       -> Scenario Builder -> .xosc -> esmini (headless) -> metrics

Architecture rules:
- Generators only output a RoadGraph. They never touch OpenDRIVE.
- The OpenDRIVE builder only consumes a RoadGraph. It knows nothing about which generator produced it.
- Every stage is runnable from the CLI and testable in isolation.

Tech stack:
- Python 3.10+, type hints everywhere, dataclasses for models.
- numpy, shapely, networkx, matplotlib, lxml, pytest.
- `scenariogeneration` package (xodr + xosc modules) for writing OpenDRIVE/OpenSCENARIO XML.
  Wrap it behind our own builder classes so it can be replaced later.
- esmini binaries (esmini, odrviewer) are external tools; their path comes from
  env var ESMINI_HOME or a config file. Never hard-code paths.

Repo layout:
  roadgen/        (full module map: docs/architecture.md#module-map)
    graph/        model.py, io.py, plot.py
    maps/         base.py, model.py, noise.py, terrain.py, elevation.py, population.py, style.py,
                  image.py, plot.py, align.py, drape.py
    generators/   base.py, lsystem.py, patterns.py, junction.py, repair.py,
                  transforms/ (drop, culdesac, stagger, roundabout, interchange)
    geometry/     planar.py, junction_space.py, design.py, primitives.py, clothoid.py, chains.py,
                  alignment.py, profiles.py, vertical.py, grade_separation.py
    opendrive/    builder.py, lanes.py, junctions.py
    validate/     connectivity.py, topology.py, continuity.py, dynamics.py, clearance.py, schema.py
    metrics/      geometry.py, network.py, sweep.py
    scenario/     xosc_builder.py
    sim/          esmini.py, runner.py, drive_check.py
    scene/        ground.py, buildings.py, osgt.py
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