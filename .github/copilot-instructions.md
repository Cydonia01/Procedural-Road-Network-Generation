# Project: Procedural Road Network Generation for esmini

We are building a Python pipeline that procedurally generates road networks,
converts them to ASAM OpenDRIVE (.xodr), generates OpenSCENARIO (.xosc) traffic
scenarios, and runs them in the esmini simulator for large-scale traffic and
scalability experiments. We do NOT use OpenStreetMap; all networks are generated.

Pipeline:
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
  roadgen/
    graph/        model.py, io.py, plot.py
    generators/   base.py, manhattan.py
    opendrive/    builder.py, lanes.py, junctions.py, geometry.py
    validate/     schema.py, topology.py, continuity.py
    scenario/     xosc_builder.py
    sim/          runner.py
    bench/        benchmark.py
  cli.py
  tests/
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