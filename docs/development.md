# Development

## Tests

```bash
docker compose run --rm -T dev python -m pytest -q        # whole suite (~560 tests, a few minutes)
docker compose run --rm -T dev python -m pytest tests/test_junctions.py -k acute
docker compose run --rm -T dev ruff check                 # lint (mypy roadgen is available too)
```

`pyproject.toml` configures pytest (`testpaths = ["tests"]`, repo root on
the path, deprecation noise filtered). `tests/conftest.py` turns run
recording off. Tests that need esmini or its RoadManager library
(`*_runs_in_esmini`, `test_esmini_drives_*`) skip themselves when it isn't
found, so a green run without esmini doesn't cover the simulator.

| Area | Test files |
|------|------------|
| RoadGraph, config, CLI runs | `test_graph.py`, `test_config.py`, `test_registry.py`, `test_history.py` |
| Input maps | `test_maps.py`, `test_style.py` |
| lsystem | `test_lsystem.py`, `test_lsystem_grid.py`, `test_lsystem_maps.py`, `test_curved_growth.py`, `test_patterns.py`, `test_culdesac.py`, `test_lsystem_identity.py` |
| Other generators, transforms, repair | `test_junction_generator.py`, `test_roundabout.py`, `test_interchange.py`, `test_repair.py` |
| Road geometry | `test_geometry.py`, `test_alignment.py`, `test_chain_roads.py`, `test_turns.py` |
| Heights | `test_align.py`, `test_elevation.py`, `test_banking.py`, `test_grade_separation.py` |
| OpenDRIVE builder | `test_opendrive.py`, `test_junctions.py`, `test_junction_variety.py` |
| Validators and metrics | `test_validators.py`, `test_metrics.py`, `test_network_metrics.py` |
| Scenario, simulation, scene | `test_scenario.py`, `test_runner.py`, `test_buildings.py` |

`test_lsystem_identity.py` guards a project rule: without maps, and with
new features at their defaults, the existing lsystem presets must grow
exactly the networks recorded in `tests/lsystem_identity.py`. New lsystem
features must therefore default to off.

### Fixtures

All fixtures are [RoadGraph JSON](road-graph.md#json-format):

- `tests/data/`: minimal graphs, `three_nodes.json` and single roads with
  different lane layouts (`road_1_1`, `road_2_0`, `road_2_2`);
- `tests/fixtures/`: junction shapes, namely `single_T`, `acute_T`,
  `angled_Y`, `single_4way`, `skewed_4way`, `4way_oneway`,
  `4way_avenue_x_street`, `star_5way` and `mixed_5way`.

Add a fixture whenever you fix a junction bug.

## Coding rules

The full rules are in [`.github/copilot-instructions.md`](../.github/copilot-instructions.md)
and [`CLAUDE.md`](../CLAUDE.md). In short:

- Python 3.10+, type hints everywhere, dataclasses for models (frozen for
  params), `Optional[...]`.
- Small, pure functions, no global state, deterministic output for a seed.
- Log with `logging.getLogger(__name__)`, never `print` (except listing
  commands). Keep comments short, with units in brackets (`[m]`, `[m/s]`).
- Every new module gets tests.
- Respect the [architecture rules](architecture.md#design-rules):
  - generators output only RoadGraphs, and the builder reads only RoadGraphs;
  - esmini is found only through `roadgen/sim/esmini.py`.
- Update the matching doc page, the CLI reference and the README with every
  new command or module. Record plans and decisions in `plans/`.

## Adding a component

Generators, map generators and graph transforms all follow one pattern:

1. **Params.** A frozen params dataclass validated in `__post_init__`, with
   its defaults in `roadgen/defaults.yaml`. A test checks that the dataclass
   defaults match the YAML, so add new parameters in both places.
2. **Presets** as a `PRESETS` dict.
3. **Registration.** An ABC subclass registered with a decorator (`@register`,
   `@register_map`), and its module imported in the package `__init__.py`.
4. **Determinism.** Deterministic for a seed. New optional features draw from
   their own random sub-stream and default to off.
5. **CLI.** A CLI subcommand, a pipeline flag where it fits, and a doc
   section.

Step-by-step guides:

- [Adding a generator](generators.md#adding-a-generator)
- [Adding a map generator](maps.md#adding-a-map-generator)
- [Graph transforms](generators.md#graph-transforms)

## Project history

The project was built in phases from the prompts in `copilot_prompts.md`.
Each phase had acceptance checks before the next one started. Later stages
were planned in `plans/` ([index](../plans/README.md)), each with its
prompts and decisions.

| Phase | Content | Main code |
|-------|---------|-----------|
| 0 | Setup and RoadGraph | `graph/` |
| 1 | Single road → OpenDRIVE → esmini | `opendrive/builder.py`, `lanes.py`, `validate/` |
| 2 | Junctions | `opendrive/junctions.py`, `geometry/junction_space.py`, `sim/drive_check.py` |
| 3–4 | Manhattan grid generator (later folded into lsystem): one-way, multi-lane, ring road, connectivity | `validate/connectivity.py` |
| 5 | Agent scenarios | `scenario/`, `sim/runner.py` |
| 7 | Pluggable generators and 3D | `generators/base.py`, `lsystem.py`, `scene/` |
| 8–9 | Input maps and map-driven growth: highways, bridges, terrain, density and grade constraints | `maps/`, `lsystem.py` |
| Stage A | Geometry accuracy: fitted curves, design speeds, banking, junction planes, clothoid turns, repair, curved growth | `geometry/`, `metrics/`, `maps/align.py`, `generators/repair.py`, `validate/dynamics.py` |
| Run folders | One hashed `out/<hash>/` per run, `run.yaml`, replay | `history.py`, `config.py` |
| Stage C | Long-tail diversity: network metrics, manhattan → lsystem, pattern fields, style maps, cul-de-sacs, junction variety, `junction` generator, roundabouts, overpasses, tunnels, interchanges | `metrics/network.py`, `patterns.py`, `maps/style.py`, `generators/transforms/`, `junction.py`, `geometry/grade_separation.py`, `validate/clearance.py` |

Phase 6 (a scalability benchmark) was removed: the project trains
autonomous cars, and `simulate` reports what a single run costs
([plan](../plans/2026-10-27-slim-benchmark.md)).

## Known issues and open ideas

- **`test_monitor_memory_cap_and_peak` fails on Linux.** `run_monitored`
  also sets `RLIMIT_AS` there, so the test's 300 MB allocation fails at once
  with `MemoryError`. The child exits with code 1 and is never reported as
  `killed == "memory"`. On macOS, where no kernel limit is set, the test
  passes. Either the runner should treat this exit as a memory kill, or the
  test should accept it on Linux.
- **The schema check needs the ASAM XSD,** which can't be redistributed.
- **Old hashes.** Hashes quoted in plans before 2026-10-25 are old
  `runs/history.json` hashes, and `out/migration.json` maps them to run
  folders. Results quoted for runs before stage A were measured on the old
  (version 1) geometry.
- **Drape has no grade limit.** lsystem keeps grades under `max_grade` while
  growing, but networks grown without maps (or with `max_grade: 0`) can get
  steep roads on hilly maps.
- **Map pipeline order limits.**
  - Terrain can't see elevation, so forests don't prefer steep or high
    ground. That would need a post-step on the terrain.
  - Style `zones` reads only density and slope, not water or parks.
