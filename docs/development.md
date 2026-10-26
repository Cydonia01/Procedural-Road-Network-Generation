# Development guide

## Tests

```bash
pytest                       # whole suite (~240 tests, under a minute)
pytest tests/test_junctions.py -k acute
docker compose run --rm dev pytest      # same, in the container
```

`pyproject.toml` configures pytest: `testpaths = ["tests"]`, the repo root
is on the path, and matplotlib/pyparsing deprecation noise is filtered.

| Test file | Covers |
|-----------|--------|
| `test_graph.py` | RoadGraph model, validation, JSON round trip, plotting |
| `test_registry.py` | Generator registry, presets, params files, `--set` |
| `test_lsystem.py` | Local constraints, `grid` preset is an exact lattice, organic determinism |
| `test_lsystem_grid.py` | One-way patterns, ring road, edge drop, `manhattan_like`, traffic connectivity, mixed one-way junctions |
| `test_lsystem_identity.py` | Existing lsystem presets grow exactly the networks recorded before stage C (`tests/lsystem_identity.py`) |
| `test_patterns.py` | Pattern fields (tensor blend, skew, organic knob) and the field goal |
| `test_style.py` | Style maps (zones, image) and blending them in the field goal |
| `test_culdesac.py` | Connectivity knob (detour ratio) and cul-de-sac bulbs |
| `test_network_metrics.py` | Network metrics: legs, skew, offset T, CNR, orientation, blocks |
| `test_junction_variety.py` | 5-7-leg stars, Y, K, skewed crossings, lane splits, staggered crossings; built, validated and driven |
| `test_junction_generator.py` | The `junction` generator: presets, leg-count coverage, bulbs, determinism |
| `test_opendrive.py` | Builder output, id scheme, sidecar, topology and continuity |
| `test_junctions.py` | Trims, movement planning, lane filling, connecting-road geometry on every fixture |
| `test_scenario.py` | Spawning, gaps, routes, `.xosc` structure, determinism, esmini log parsing |
| `test_buildings.py` | Blocks, footprints, triangulation, `.osgt` / `.obj` output |
| `test_bench.py` | Config loading, resumption, statuses, process monitor, plots |

Tests that need esmini (`*_runs_in_esmini`, `test_esmini_drives_*`,
`test_tiny_real_sweep`) are marked `skipif` and skip themselves when esmini
or its RoadManager library isn't found. A green run without esmini therefore
doesn't cover the simulator integration.

### Fixtures

- `tests/data/`: minimal graphs: `three_nodes.json` and single roads with
  different lane layouts (`road_1_1`, `road_2_0`, `road_2_2`).
- `tests/fixtures/`: junction shapes: `single_T`, `acute_T`, `angled_Y`,
  `single_4way`, `skewed_4way`, `4way_oneway`, `4way_avenue_x_street`,
  `star_5way`, `mixed_5way`.

All are plain [RoadGraph JSON](road-graph.md#json-format). Add a new fixture
whenever you fix a junction bug.

## Coding rules

From `.github/copilot-instructions.md`:

- Python 3.10+, type hints everywhere, dataclasses for models
  (frozen for params).
- Small, pure functions, no global state, deterministic output for a seed.
- Log with `logging`, never `print` (except `generators` listing output).
- Every new module gets pytest tests.
- Keep the architecture boundaries: generators → RoadGraph only; builder ←
  RoadGraph only; esmini paths only through `roadgen/sim/esmini.py`.
- Don't implement features from later phases unless asked.

Optional tools in the Docker image: `ruff check .`, `mypy roadgen`.

## Project phases

The project was built phase by phase from the prompts in `copilot_prompts.md`.
Each phase had acceptance checks before the next one started.

| Phase | Content | Main modules |
|-------|---------|--------------|
| 0 | Setup and RoadGraph data model | `graph/` |
| 1 | Single road → OpenDRIVE → esmini | `opendrive/builder.py`, `lanes.py`, `validate/` |
| 2 | Junctions | `opendrive/junctions.py`, `geometry/planar.py`, `geometry/junction_space.py`, `sim/drive_check.py` |
| 3 | Manhattan grid generator (removed in stage C; lsystem grows the same grids) | `generators/manhattan.py` |
| 4 | Manhattan realism: one-way, multi-lane, presets, ring road, connectivity | `manhattan.py`, `validate/connectivity.py` |
| 5 | Agent scenarios | `scenario/`, `sim/runner.py` |
| 6 | Scalability experiments | `bench/` |
| 7 (optional) | Pluggable generators and 3D | `generators/base.py`, `lsystem.py`, `scene/` |
| 8 | Procedural input maps (terrain, elevation, population) | `maps/` |
| 9 | Map-driven growth: highways, bridges, terrain / density / grade constraints | `lsystem.py`, `generators/base.py` |
| 10 (stage A) | Geometry accuracy: fitted curves, design speeds, banking, junction planes, clothoid turns, repair, curved growth (plan in `plans/`) | `geometry/`, `metrics/`, `maps/align.py`, `generators/repair.py`, `validate/dynamics.py` |
| 11 (stage C0–C4) | Network metrics; manhattan folded into lsystem; pattern fields, style maps, connectivity knob, cul-de-sacs (plan in `plans/`) | `metrics/network.py`, `lsystem.py`, `patterns.py`, `maps/style.py`, `generators/transforms/` |

## Known issues

- **`test_monitor_memory_cap_and_peak` fails on Linux.** There, `run_monitored`
  also sets `RLIMIT_AS`, so the test's 300 MB allocation fails immediately
  with `MemoryError` and the child exits with code 1. The run is never
  reported as `killed == "memory"`. The test passes on macOS, where no
  kernel limit is set. Either the runner should treat this exit as a memory
  kill or the test should accept it on Linux.
- **The schema check needs the ASAM XSD**, which can't be redistributed.
  Without it, the check is skipped with a warning.
- **Hashes quoted in plans before 2026-10-25 are the old `runs/history.json`
  hashes.** The run folders (`out/<hash>/`) hash runs differently;
  `out/migration.json` maps the old hashes to the new ones for the example
  runs. Results quoted for runs recorded before stage A (e.g. benchmark rows)
  were measured on version 1 geometry networks.
