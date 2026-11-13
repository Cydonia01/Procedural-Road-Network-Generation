# CLAUDE.md

Project: Procedural Road Network Generation for esmini.

We are building a Python pipeline that procedurally generates road networks
and converts them to ASAM OpenDRIVE (.xodr). The aim is the road network: the
generated .xodr (with its HD-map content) is the product. Traffic simulation
and training self-driving cars are not aims of this project. We do NOT use
OpenStreetMap; all networks are generated.

## Pipeline

```
Input maps (terrain, elevation, population, style) -> Generator (optional input)
Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validators, drive-check, metrics
                                                     -> presentation: scene (ground, buildings),
                                                        esmini `view` (odrviewer with its own cars)
```

Everything after the .xodr is for presentation only. When a fix would trade the
.xodr's correctness for looks, the .xodr wins. Scalability (bigger cities) is
not a current goal: 4000 x 4000 m networks already work.

## Architecture rules

- Generators only output a RoadGraph. They never touch OpenDRIVE.
- The OpenDRIVE builder only consumes a RoadGraph. It knows nothing about which generator produced it.
- Every stage is runnable from the CLI and testable in isolation.

## Tech stack

- Python 3.10+, type hints everywhere, dataclasses for models.
- numpy, shapely, networkx, matplotlib, lxml, pytest.
- `scenariogeneration` package (xodr module) for writing OpenDRIVE XML.
  Wrap it behind our own builder classes so it can be replaced later.
- esmini binaries (odrviewer, esmini) and its RoadManager library are external
  tools, used to view networks and to drive-check them; their path comes from
  env var ESMINI_HOME or a config file. Never hard-code paths.

## Repo layout

```
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
    validate/     stages.py, connectivity.py, topology.py, continuity.py, dynamics.py, clearance.py,
                  hdmap.py, schema.py
    metrics/      geometry.py, network.py, hdmap.py, sweep.py
    sim/          esmini.py, drive_check.py, screenshot.py
    scene/        surface.py, ground.py, structures.py, buildings.py, parcels.py, junctions.py, osgt.py
    config.py, history.py, defaults.yaml, presets.yaml
  cli.py
  tests/
  docs/
  out/            (generated files, git-ignored)
```

## OpenDRIVE conventions we follow

- Target OpenDRIVE 1.6 (or lower if esmini complains); right-hand traffic.
- Lane ids: negative = right of reference line, positive = left, 0 = center lane.
- Roads stop at the junction boundary; junction interiors are covered only by connecting roads.
- Every road/junction id is unique; links use correct contactPoint (start/end).
- Units: meters, radians, m/s.

## Coding rules

- Small, pure functions; no global state; deterministic output given a seed.
- Write pytest tests for every new module.
- Log with the `logging` module, not print.
- Don't implement features from later phases unless asked.
- Don't keep a replaced implementation behind a flag: when a better one
  replaces it, remove the old path, its parameters and its tests.

## Running things

- The host has no Python dependencies; run everything in the dev container:
  `docker compose run --rm -T dev python -m pytest -q`
  (`-T` for non-interactive use). Same for `python -m cli ...` and `ruff check`.
- Generated files go under `out/` (git-ignored).
- Every successful CLI run gets a folder `out/<hash>/` with its outputs
  (`-o` only names them) and `run.yaml`: the command plus only the args and
  parameters that differ from defaults (`roadgen/history.py`). `python -m cli
  HASH` or `python -m cli run out/HASH/run.yaml` reruns it. Use
  `--no-history` for throwaway runs (no folder, `-o` is a path); tests run
  with recording off (`tests/conftest.py`).

## Where things are

- All default parameters: `roadgen/defaults.yaml`. Params dataclasses read
  their field defaults from it (`roadgen/config.py`); a test checks they match,
  so new params go in both places.
- Road generators: `roadgen/generators/` (registry in `base.py`).
- Input maps (terrain, elevation, population, style): `roadgen/maps/` —
  separate per-kind registry, same params/preset pattern. `lsystem` reads
  them (`uses_maps`, `map_bounds`, `map_kinds`, `generate(..., maps=MapSet)`);
  the CLI builds them before generating. New lsystem features must default
  to off: `tests/test_lsystem_identity.py` checks the existing presets
  against fingerprints; re-record them (`python -m tests.lsystem_identity`)
  only for an intended change, and say so in the plan.
- Presets: `roadgen/presets.yaml` (keyed like `defaults.yaml`), read with
  `config.presets()`. One per street pattern plus the map cities; other
  layouts are parameter sets, not new presets.
- HD maps (medians, marks, signs, lights, junction control, turn lanes; no side lanes)
  are on by default (`hdmap.enabled`); `--no-hd` gives the plain
  driving-lane network. Tests that build networks without the pipeline
  pass `HdMapParams(enabled=False)` where they assume driving lanes only.
- The generated `.xodr` is the product; everything after it (scene,
  buildings, `view`) is for presentation only, and `view` is how networks
  are presented. There are no `simulate` or `scenario` commands any more.
- lsystem is the only city generator (the `manhattan` generator was removed
  in stage C): grids, pattern fields (`patterns.py`) and graph transforms
  (`roadgen/generators/transforms/`).
- Docs: `docs/` (index in `docs/README.md`, by task and by source folder).
  Each topic has one home page; other pages link to it instead of repeating
  it (parameters and run folders: `cli-reference.md`; module map and output
  files: `architecture.md`). Update the matching page, the CLI reference and
  the index when adding commands or modules. Keep stage labels and
  "fixed in" notes out of the reference pages; history goes in
  `docs/development.md#project-history` and `plans/`.
- Reference papers: `files/*.pdf` (`file1.pdf` is Parish & Müller 2001).

## Plans and prompts (traceability)

- Every plan, design discussion or review gets its own Markdown file in
  `plans/`, named `YYYY-MM-DD-<short-slug>.md`. Discussions (pros/cons,
  options, decisions) are saved too, not only final plans. Add each file to
  the index in `plans/README.md`.
- Each file starts with a **Prompts** section quoting, verbatim and dated,
  the user prompts that led to it. Follow-up prompts that change a plan are
  appended there, and the plan is updated rather than duplicated.
- Record the decisions the user made, and the ones left to Claude with the
  reasoning, in a **Decisions** section.
- When implementation deviates from a plan, update the plan file in the
  same change and note why.
- Small helper scripts used to produce numbers quoted in a plan go in
  `plans/scripts/` while they are still used; the timestamped plan files
  are the record (quote the numbers there), so scripts nothing uses any
  more are deleted.

## More conventions

- New pluggable components follow the existing pattern: ABC + `register`
  decorator, frozen params dataclass with presets (in `presets.yaml`),
  defaults in `defaults.yaml`, deterministic for a seed, CLI subcommand + pipeline flag.
- Match the existing style: `Optional[...]`, short comments with units in
  brackets (`[m]`, `[m/s]`), `log` via `logging.getLogger(__name__)`.
