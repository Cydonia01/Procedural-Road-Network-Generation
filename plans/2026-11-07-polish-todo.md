# Polish stage: the complete to-do list

One list of everything left to do before any new feature. Every source
below is merged in here, and items outside the project's scope are struck
out:

- the open items of stage R;
- the open issues from stages A, B and C;
- `Roadgen AV Readiness Review.pdf`;
- `proje yapılacaklar.txt`;
- the review [2026-11-07-remaining-work-and-open-issues.md](2026-11-07-remaining-work-and-open-issues.md).

Nothing under `roadgen/` or `tests/` changes in this step.

## Prompts

**2026-11-07, prompt 1**

> after stage-r implementation, tell me what is remain for implementation? look at plans folder. besides that, go into details of open issues from earlier stages.

**2026-11-07, prompt 2**

> Currently, we will not add a new feature. Before adding new features, we polish the current features. Let me tell you something: The aim of the project is to generate the road network. Simulation of traffic or training a car is not our aim. in project-explanation it says openstreetmap but we will not use it. scalability means making the cities bigger. So, it is already satisfied because we generate 4000x4000. Maybe we focus on it later. In this stage, I want you also analyze what can be dropped or removed because some implementations have been replaced with better ones be the old ones had not been removed. They are accessible by flags. So, firstly, I want you to give me a total list of what should be done, which includes the missing parts you told me previous response and the features remaining from roadgen av readiness pdf and proje yapılacaklar.txt file. I want to see all of them in one place. Before preparing this file, don't forget to drop existing problems or implementations. Also, tests take so much time. We can also consider compacting the tests into meaningful ones. Do not change code. Just give me a markdown or artifact.

**2026-11-08, prompt 3**

> [...] update polish-todo.md file by putting Done to the right of an item. I want to use it as a checklist. [...]

Each item table has a Status column for that.

## Decisions

| # | Decision | By |
|---|---|---|
| 1 | No new features in this stage; only polish what exists. | user |
| 2 | The project generates road networks. Traffic simulation and training a car are **not** aims. | user |
| 3 | No OpenStreetMap; `project-explanation.md` is out of date there. | user |
| 4 | Scalability means bigger cities. 4000 × 4000 m already works, so it is deferred. | user |
| 5 | Items that only serve simulation or training are struck out (section 0), not fixed. Code that only serves them is listed for removal in section 1 when the cost of keeping it is visible: failing tests, test time, code paths. | Claude |
| 6 | Every "drop" item is a proposal. Each says what replaces it and what breaks, so the user can tick it or not. | Claude |

## How to read this list

- **P1** must happen in this stage: it is a bug, or it blocks a cleanup.
- **P2** should happen in this stage.
- **P3** is nice to have, or waits for a decision.
- **Source** gives where the item comes from: R = stage R plan, A / B / C =
  that stage's plan, PDF = AV readiness review, TXT = `proje yapılacaklar.txt`,
  NEW = found in this review.

---

## 0. Struck out (out of scope after decisions 2–4)

These are not done and not tracked any more. Struck out, not forgotten:

| Item | Source | Why struck |
|---|---|---|
| Traffic obeys lights, stop and yield signs | B6, TXT | Simulation behaviour, not the network. The lights, plans and rules stay in the map. |
| Cars merged by force when their lane ends | A | Same: esmini's driver, not the map. |
| Ego vehicle, manoeuvre routes, reactive traffic, SUMO, pedestrians, TTC-based critical cases | PDF §E | Training data. |
| Export to Argoverse 2 / Waymo / ScenarioNet, OSI ground truth, CARLA / RoadRunner assets | PDF §E | Training data. |
| Per-junction difficulty score, ordering data by difficulty | PDF §D | Its purpose is curriculum training. |
| OpenStreetMap | `project-explanation.md` | Never used; only the text needs fixing (item H1). |
| Scalability (bigger cities), the throughput benchmark | user | Deferred: 4000 × 4000 m works. |
| esmini load-time profiling (gprof build of RoadManager) | B9 | Only matters for simulation throughput. The issue draft is item H5. |

---

## 1. Drop or simplify: old implementations still behind flags

**Status 2026-11-08** ([2026-11-08-polish-drops.md](2026-11-08-polish-drops.md)):
done: D1, D2, D3, D5 (`grid`, `grid_organic`, `organic_curved` removed),
D6, D8, D9, D10 (patches kept and always written), D11, D12, D13 (parcels
kept, blob path removed), D14 (monitoring and `pipeline --simulate` removed;
`simulate` kept for presentation), D15, D21 (unused scripts deleted).
Kept: D4 (user), D16 (two entry points, not legacy). D20: moved to
`plans/2026-10-08-copilot-phase-prompts.md` (user).
Moved to fixes: D17 (F20). D18 and D19 done 2026-11-08, and `simulate`
was removed completely (D14). D22: `scenario` removed too (user, 2026-11-08). Section 1 is closed apart
from D7, kept as is.
F1 and F2 (the two failing tests) went with D8 and D14.

Each item below was replaced by a better one, but the old path is still
reachable by a flag or parameter. Keeping it costs code paths, tests
(identity guards in particular) and docs.

### 1a. Generator (`lsystem`)

| # | Old path (flag) | Replaced by | Cost of keeping | Proposal | Pri | Status |
|---|---|---|---|---|---|---|
| D1 | `hierarchy: axis`: lanes by heading | `hierarchy: strokes` (R1) | A second lane-assignment rule in `lsystem.py`. `test_hdmap_identity.py` uses it, and that test fails anyway (F1). Old run.yaml files replay through it. | Remove. Old runs then replay with the new hierarchy (or from their git commit, H3). | P1 | Done |
| D2 | `curve_mode: drift`, the grid goal's curved streets (A9) | Field goal, `organic` pattern (C2) | Two curved-street systems: the OU random walk, the bow nodes and their own checks, next to the field streamlines. Only preset `organic_curved` uses `drift`, and `organic_town` does the same job better. | Remove `drift` and the preset `organic_curved`. Keep `curve_tortuosity`, which also works with the field goal. | P2 | Done |
| D3 | `curve_mode: contour` (A9) | Field goal, `contour` pattern (C2) | The same rule implemented twice (slope weight, full pull from 8 %). Preset `hill_town` uses the old one. | Move `hill_town` to `global_goal: field` with contour weight, then remove `curve_mode` entirely. The naturalness gate must still pass on the moved preset. | P2 | Done |
| D4 | `global_goal: grid` | Field goal with `pattern_weights` (1,0,0,0,0) and `organic: 0` keeps exact grids (docs) | Two growth paths through `resolve` / `acceptable` / branching. `grid`, `manhattan_like`, `grid_organic`, `city` and `highway_corridor` use it. | **Decision needed.** Moving them to the field goal makes one growth path, but changes their output (re-record fingerprints) and must keep exact grids exact. Do D2 and D3 first; decide D4 after measuring whether field-grid reproduces `grid`. | P3 | Kept (user) |
| D5 | Presets that duplicate others | — | Each preset is a fingerprint, an example, a test case and docs. | `organic_curved` goes with D2. `grid_organic` vs `city` without maps (both a noisy grid): keep one. `grid` vs `manhattan_like`: keep `manhattan_like` only if one-way and ring roads matter to you (decision 1 of the refinement review: grids are not a target). | P2 | Done |
| D6 | `edge_drop_prob` | Dead-end pass, cul-de-sacs, `branch_prob` (R3, C) | Drops street segments after growth, so it creates the dead ends R3 removes. | Remove, unless you use it for sparse grids. | P3 | Done |
| D7 | `body_check: false`, `dead_ends: false` | On by default (R2, R3) | Only switches back to the networks R2 and R3 fixed. | Keep them as parameters (cheap, useful when debugging growth), but no tests or examples with them off. | — | Kept (no change needed) |

### 1b. OpenDRIVE builder and HD maps

| # | Old path (flag) | Replaced by | Cost of keeping | Proposal | Pri | Status |
|---|---|---|---|---|---|---|
| D8 | `hdmap.enabled: false` (`--no-hd`) must be **byte-identical** to before stage B | HD maps on by default (B10) | `test_hdmap_identity.py` (failing for 3 of 4 cases, F1), the `hierarchy=axis` dependency (D1), and the rule "with HD off nothing changes" holding back every builder change. | Keep `--no-hd` as "driving lanes only" (fast, plain), but **drop the byte-identity promise** and its test. Replace it with a small test that `--no-hd` has driving lanes only. | P1 | Done |
| D9 | `road_marks.enabled: false`: pre-stage-B marks with HD on | German catalogue marks (B4) | A second mark writer for a combination nobody uses. | Remove. `--no-hd` keeps its own simple marks. | P2 | Done |
| D10 | `hdmap.junction_patches` (`<object type="patch">`) | Scene junction fill (R5) | Patch objects are only written when no scene is written. They draw lighter than the lanes (screenshot 3), the very problem R5 fixed. | Remove patches, and always write the fill through the scene. **Or** keep them for `.xodr`-only users. Decision needed: does anyone open an `.xodr` without its scene? | P3 | Done |
| D11 | `junction_control.priorities: false` (sidecar only) | `<priority>` records on (B5: 0.6–2.4 % of the file) | The off switch was for file size, which B5 measured as negligible. | Remove the switch. | P3 | Done |
| D12 | `opendrive.lane_split: inner / none` | `outer` (default) | Two alternatives with no preset or example using them. | Remove, unless they were meant for diversity. | P3 | Done |
| D13 | `buildings.parcels: false`: one rounded blob per block | Parcels (R6) | A second building path. | Remove. | P2 | Done |

### 1c. Simulation and run tooling (decision 2)

| # | What | Cost of keeping | Proposal | Pri | Status |
|---|---|---|---|---|---|
| D14 | `simulate` command, `pipeline --simulate`, the runner's memory/CPU monitor (`sim/runner.py`) | The known failing test `test_monitor_memory_cap_and_peak`, esmini runs in tests, the `pipeline` section `sim_*`. | **Decision needed.** Option a: remove `simulate`, the monitor and their tests; keep `scenario` (an `.xosc` with cars, to look at the network). Option b: keep it frozen and fix the test (F2). | P1 (decide) | Done (simulate removed) |
| D15 | `scenario` extras: `spawn_mode: explicit_routes`, light storyboard events, `TrafficSignalController` in the `.xosc` | Parametrised tests per mode; the light events only matter when something drives. | With D14a: keep `random_lanes` only. Lights stay in the `.xodr` either way. | P3 | Done |
| D16 | `replay HASH`, `run run.yaml`, `python -m cli HASH`: three ways to rerun | Three code paths and docs for one job. | Keep `run` and the bare `HASH` shortcut; fold `replay` into them. | P3 | Kept (two entry points) |
| D17 | `--allow-traps` on every example (`runs/examples.sh:46`) | Hides the dead ends R3 was meant to remove (R3 says the examples run without it). | Remove from the examples. Keep the flag for experiments. | P2 | Moved to F20 |
| D18 | `metrics --sweep` default `--jobs 1`, plots on | Sweeps are slow (also P6). | `--jobs` = CPU count, no plots in sweeps. | P3 | Done |

### 1d. Repository leftovers

| # | What | Proposal | Pri | Status |
|---|---|---|---|---|
| D19 | `out/migration.json` and pre-2026-10-25 hashes quoted in plans | Keep the plans as history; delete the migration map once no old hash is needed. | P3 | Done |
| D20 | `copilot_prompts.md` (Phase 1–10 prompts, OSM-era wording) | Move into `plans/` as history, or delete. It still describes the axis rule as current. | P3 | Done |
| D22 | `scenario` command and `roadgen/scenario/` (NEW, after `simulate` was removed) | Nothing in roadgen runs an `.xosc` any more; `view` presents with odrviewer's own cars. The scenario builder (random cars, the light plans as storyboard events) only serves people who run esmini by hand. **Decision needed:** keep, or remove with its tests and docs. | P2 | Done (removed) |
| D21 | `plans/scripts/*` one-off scripts that import removed parameters (after D1–D13) | Keep (traceability), but mark them as "ran on commit X" so nobody expects them to run on HEAD. | P3 | Done |

---

## 2. Fix: bugs and open issues

| # | Issue | Detail | Source | Pri | Status |
|---|---|---|---|---|---|
| F17 | **Pipeline crashes in the body-overlap validator** (NEW, found by the test run) | `test_maps.py::test_cli_pipeline_without_maps_writes_none`: `pipeline lsystem` on a 2 × 2 grid with only an fbm elevation map raises `TypeError: Array should be of object dtype` in `check_body_overlaps_tree` (`roadgen/validate/clearance.py:110`, shapely). The R2 validator does not handle this network: likely an empty or one-road `bodies` list, not yet confirmed. Any small network can crash the whole pipeline. | R2 | P1 |  |
| F18 | **Naturalness gate fails on `suburb` seed 1** (NEW) | `test_network_metrics.py`: dead-end share 3.28 % > 3 %. Either the dead-end pass misses suburb streets that are not meant as cul-de-sacs, or the gate must exclude intended cul-de-sacs. | Q, R3 | P1 |  |
| F19 | **Field-goal grid no longer keeps one angle** (NEW) | `test_style.py::test_district_angles_turn_the_grid`: headings mod 90° are {19.6, 49.6, 79.6} instead of {19.6}. Some edges now run 30° off the district's grid, probably dead-end extensions or connectors (R3) at free angles. Decide whether those may leave the grid angle, then fix the code or the test. | R3 | P1 |  |
| F20 | Map examples still report traffic traps (NEW, from D17) | `hill_town` and `city` seed 1 / 7 report a few dead-end traps (water, area edge) on HEAD too, so `runs/examples.sh` still needs `--allow-traps`. Make the dead-end pass handle them, or count intended ones as allowed, then drop the flag from the examples. | R3, D17 | P2 |  |
| F21 | **No lane dashes on many two-lane roads** (NEW, user screenshot 2026-11-08) | Two-way roads with one lane each way show only edge lines, no broken centre line. It appeared after the stage R hierarchy fixed the 1 → 2 → 1 lane jumps. Likely cause (not yet checked): `road_marks.no_street_centre` (urban, suburban) gives `street`-class roads no centre line by design (German residential practice), and the hierarchy turned many former avenues into local `street` roads. Check which class and profile the roads in the screenshot have, then decide whether local roads with traffic should get a centre line (e.g. only collectors and up keep it, or drop the rule). | B4, R1, user | P1 | |
| F1 | `test_hdmap_identity.py` fails (manhattan_like, organic_town, city_maps) | Already broken on 3ae2780, before stage R; only `grid` matches the B0 hashes. Resolved by D8 (drop the byte-identity test). Without D8: bisect between B0 and 3ae2780. | R | P1 | Done (with D8) |
| F2 | `test_monitor_memory_cap_and_peak` fails on Linux | `RLIMIT_AS` makes the child exit 1 with `MemoryError` instead of being reported as a memory kill. Goes away with D14a; otherwise a one-line fix in the runner or the test. | A–R | P1 | Done (with D14) |
| F3 | Roundabout spurs: `mixed_city` fails on 10 of 200 seeds with `--no-hd` | A 13–24 m spur between a roundabout entry and a tight bend is shorter than its two trims (9 + 9 m). It builds with HD only because the HD fit straightens the bend. Stage R changed the roundabout fit (R-12) and never reran the sweep, so first rerun it, then fix the transform (spur length or bend). | B9 | P1 |  |
| F4 | The `mixed_city` 200-seed sweep from stage C never finished | Needed for F3 anyway; the sweep is slow (D18, P2–P5). | C | P2 |  |
| F5 | Interchange drive-check postponed since C9 | `diamond`, `cloverleaf` and `highway_corridor` run `--drive-check` in the examples, but whether it passes, or how long it takes, was never recorded. The drive-check checks the **network** (every junction movement is drivable), so it stays in scope. | C9, B | P1 |  |
| F6 | "No connecting lane" warnings in esmini (3–4 per run since B4) | Possibly a missing lane link in our junctions, which would be a network bug. Trace them to road and lane ids with the drive-check. | B4 | P2 |  |
| F7 | Roads of the new hierarchy look untextured in esmini | From TXT; not reproduced. Check: (a) esmini cannot find `resources/models` when started by `view` (the runner counts "Texture file not located" as ignored `asset_warnings`); (b) a lane type or width the stroke classes produce that esmini draws without material; (c) compare one road before and after R1 with `view --screenshot`. | TXT | P1 |  |
| F8 | Class changes along straight lines at 3+-road junctions | city 11, mixed_city 5, hill_town 10 after R1, mostly highway ↔ street. R-4 accepted them, but they are the "unnatural joins" of the screenshots (TXT). Options: let a highway continue as an arterial stroke, or end highways at a junction only where the classes are compatible. | R1, TXT | P2 |  |
| F9 | `city` seed 7 had no street × street junctions | So right-before-left never appears there. The strokes hierarchy has probably fixed it; re-measure, and close or fix. | B5 | P3 |  |
| F10 | Sidewalks end abruptly at "bare road ends" | B9 drops the side lanes at junction ends that are too short with them (city 3, mixed_city 21 of 200 seeds). Better: taper the side lanes over `side_taper` before the end instead of cutting them. | B9 | P3 |  |
| F11 | Advisory speeds on grid corners | Tight grid corners get a lower advisory speed instead of a larger radius; only curved presets avoid it. Re-measure after R1–R3 (fewer jumps, different classes). | A | P3 |  |
| F12 | `align` does not check the cross slope under a curve | Banking on a grade can exceed the design limit unseen. Add the check to `align` or `validate/dynamics.py`. | A | P3 |  |
| F13 | Drape has no grade limit | Networks grown without maps (or with `max_grade: 0`) get steep roads when draped. Cap the grade in `drape`, or warn in validation. | dev docs | P3 |  |
| F14 | Ground poke-through is logged only | It is not part of `naturalness` or any test gate. Add it to the Q metrics so R4 cannot regress. | R (Q) | P2 |  |
| F15 | Probe overlaps on grade-separated crossings | highway_corridor's 12 "overlaps" are bridges; the probe does not read heights. Make the probe and metric height-aware, so the number means something. | R2 | P3 |  |
| F16 | Map order limits | Terrain cannot see elevation, so forests don't prefer steep ground. Style `zones` ignores water and parks. Both are documented limits; fix only if they show in the look. | dev docs | P3 |  |

---

## 3. Finish: half-done parts of existing features

| # | Item | What is left | Source | Pri | Status |
|---|---|---|---|---|---|
| R4a | Ground: footprint cut | Cut road and junction footprints out of the ground (constrained triangulation) and stitch the ground to the road edges. Today triangles are pressed down under the roads; the edges still look jagged. | R4 | P2 |  |
| R4b | Ground: shoreline | Stair-step shoreline from per-vertex nearest class on the 20 m grid; smooth the class boundary. | R4, screenshot 4 | P2 |  |
| R4c | Ground: slopes | Embankment and cut slopes where the road leaves the terrain, instead of a vertical step. | R4 | P3 |  |
| R4d | Structures | Bridge decks, piers and tunnel portals in the scene; esmini draws none of them. | R4, screenshot 4 | P3 |  |
| R4e | Sidewalks and curbs as raised geometry | The 20 m ground mesh cannot show a 2 m sidewalk. | refinement review | P3 |  |
| R7 | Materials and Blender export | glTF road mesh per lane type with UVs (s, t), material per lane type, marks as decals, a CC0 texture fetch script. In the stage R plan and in TXT ("blender taşıması"). **Decision needed:** it is a new output format, so it may belong to the next stage under decision 1. | R7, TXT | P3 (decide) |  |
| V1 | Validation after every pipeline step | TXT "stage stage validation". Today validators run on the final `.xodr` (plus the graph checks in the builder). Add cheap graph checks after `generate`, `align`, `drape`, `separate`, `annotate`, so a failure names the step that caused it. | TXT | P2 |  |
| V2 | End-to-end review of the examples | TXT. Rebuild `runs/examples.sh`, look at every example in esmini (top view and street level), and redo the six screenshots of the refinement review on the same views, before and after. | TXT | P1 |  |

---

## 4. Performance (the same pipeline, faster)

| # | Change | Expected | Pri | Status |
|---|---|---|---|---|
| P0 | Time every pipeline step and put the table in the report (TXT: "her aşamanın kaç saniye sürdüğü"). The pipeline already logs step times. Collect them per example into one table, against the B9 baseline. | the measurement every P item is checked against | P1 |  |
| P2 | Parse the written `.xodr` once and pass the tree to every validator (today 7 parses). | validate 9.4 → 3–5 s on city | P2 |  |
| P3 | Vectorize `RoadEval` and `lane_polygons`. | 3–5 s (validate, lane plot, junction fill) | P2 |  |
| P4 | Compute `network_alignment` once per graph and pass it on. | 1–3 s per reuse | P2 |  |
| P5 | Repair checks local to the two end nodes. | most of the remaining fit time | P2 |  |
| P6 | Sweeps in parallel, no plots (D18). Corner roads optional when only viewing (27 s of city's esmini load). Rounded coordinates in the `.xodr`. | sweeps; esmini load | P3 |  |

---

## 5. Tests: compacting

**Measured** on HEAD 9369fdc, 2026-11-07: `docker compose run --rm -T dev python -m pytest -q --durations=0 --durations-min=1.0`.

- 787 tests, **316 s** (5 min 16 s); 780 passed, 7 failed (F1, F2, F17–F19).
- **42 tests take 267 s (85 %).** The other 745 tests take about 50 s together.
- Compacting the many small tests therefore saves little time. The time is in
  a few tests that grow, build, validate or drive a whole network.
- No test shares a network with another: there are no module- or
  session-scoped fixtures, so the same presets are grown again and again
  (`city` + maps in at least 5 files).

**The slowest tests (≥ 4 s, 132 s together):**

| s | Test | Note |
|---|---|---|
| 42.2 | `test_style.py::test_mixed_city_is_more_varied_than_a_grid` | grows full `mixed_city` + maps, and a grid, only to compare two metrics |
| 23.7 | `test_lsystem_identity.py::test_network_unchanged[hill_town_maps]` | fingerprint; `hill_town` changes with D3 anyway |
| 17.2 | `test_lsystem_grid.py::test_cli_pipeline_preset_passes` | full CLI pipeline |
| 16.1 | `test_lsystem_identity.py::test_network_unchanged[city_maps]` | fingerprint |
| 13.6 | `test_side_lanes.py::test_drive_check_with_side_lanes[grid]` | esmini drive-check of the whole grid |
| 13.1 | `test_hdmap_identity.py::test_build_unchanged[city_maps]` | failing (F1); goes with D8 |
| 8.3 | `test_junction_control.py::test_annotate_command_and_pipeline` | full CLI pipeline |
| 8.2 | `test_scenario.py::test_too_many_vehicles` | simulation (D14) |
| 8.0 | `test_elevation.py::test_cli_pipeline_drapes_and_writes_the_scene` | full CLI pipeline |
| 7.9 / 7.8 | `test_lsystem_maps.py::test_city_on_generated_maps_builds_valid_xodr[0/1]` | `city` + maps, two seeds |
| 7.4 | `test_lsystem.py::test_acceptance_runs_in_esmini` | esmini |
| 7.2 | `test_hdmap_default.py::test_pipeline_default_is_hd_and_no_hd_is_plain` | full CLI pipeline, twice |
| 7.2 | `test_corners.py::test_buildings_stand_behind_the_sidewalks` | HD build + buildings |
| 6.5 | `test_style.py::test_cli_pipeline_builds_style_only_for_the_field_goal` | full CLI pipeline |
| 6.2 | `test_cross_section.py::test_pipeline_hd_fits_and_builds` | full CLI pipeline |
| 5.0 | `test_hdmap_identity.py::test_build_unchanged[manhattan_like]` | failing (F1) |
| 4.7 | `test_repair.py::test_sweep_seed_60_now_builds` | one old sweep seed |
| 4.7 | `test_lsystem.py::test_acceptance_pipeline_lsystem_grid` | full CLI pipeline |
| 4.4 | `test_side_lanes.py::test_pipeline_writes_lane_plot_and_cross_section_flag` | full CLI pipeline |

Per file: style 50 s, lsystem_identity 40 s, hdmap_identity 22 s,
lsystem_maps 20 s, side_lanes 20 s, lsystem 19 s, lsystem_grid 17 s;
everything else under 10 s per file.

**Proposals:**

| # | Change | Saves (est.) | Pri | Status |
|---|---|---|---|---|
| T1 | **Shared networks.** Add a session-scoped fixture in `conftest.py` that grows each needed preset once (`grid`, `city` + maps seed 1, `suburb`, `organic_town`, `mixed_city`), builds it once with and once without HD, and caches graph, `.xodr` and sidecar. Identity, naturalness, `lsystem_maps`, `style`, `corners` and `hdmap_default` tests then only assert on the cached results. Tests must not mutate them (pass copies). | 60–90 s | P1 |  |
| T2 | **One end-to-end CLI test** instead of 8 that each run `pipeline` (lsystem_grid, lsystem acceptance, hdmap_default, elevation, annotate, cross_section, style, side_lanes lane plot). Run one small `pipeline lsystem --preset grid --maps --ground --buildings --lane-plot` and assert every output and flag effect on it. Keep separate CLI tests only for argument errors (fast). The functions behind each flag are already tested directly. | 40–50 s | P1 |  |
| T3 | **Drop tests of dropped code** with section 1: `test_hdmap_identity.py` (D8, 22 s, failing), the `axis` cases (D1), `test_curved_growth.py` drift (D2), the `contour` curve-mode tests (D3), `parcels: false` (D13), marks off (D9), `test_runner.py` memory monitor and `test_too_many_vehicles` (D14), `explicit_routes` parametrisations (D15). | 35–45 s | P1 | Done |
| T4 | **Smaller cases where size isn't the point.** `test_mixed_city_is_more_varied_than_a_grid` needs only the two metrics: use the shared `mixed_city` (T1) or a 1500 × 1200 m extent. `lsystem_identity`: fingerprint the map presets on a smaller extent. One seed instead of two in `test_city_on_generated_maps_builds_valid_xodr`. | 30–40 s | P2 |  |
| T5 | **esmini once per feature family.** Today 10 files call esmini or the RoadManager. Keep one drive-check per family on its smallest network: one junction kind, side lanes on `diamond` (1.9 s) instead of the whole `grid` (13.6 s), one interchange, one roundabout. | 15–20 s | P2 |  |
| T6 | **Fast and full runs.** Mark the remaining slow tests `@pytest.mark.slow`. `pytest -m "not slow"` is the daily run; the full run happens before a merge. Optionally add `pytest-xdist` (`-n auto`) to the dev image, a new dev dependency: decision needed. | daily run under 1 min | P2 |  |
| T7 | **Merge trivial tests for readability, not time.** For example the parameter-validation tests (`ValueError` on bad params), one per params class across many files, become table-driven tests per module. The ~745 small tests stay about 50 s either way. | ~0 s | P3 |  |

Expected after T1–T5: the full suite at about 100–140 s, the fast run
(T6) under a minute. Each step should be measured with the same command.

---

## 6. Docs, report and housekeeping

| # | Item | Pri | Status |
|---|---|---|---|
| H1 | `project-explanation.md`: remove OpenStreetMap; state the aim (generated road networks for esmini, not traffic simulation); scalability = city size, deferred. | P1 |  |
| H2 | Update the docs after the drops in section 1: `lsystem.md` (curve_mode, axis, grid goal), `opendrive.md` (patches, marks off), `scene.md`, `cli-reference.md`, `scenarios-and-simulation.md`, CLAUDE.md (identity rules). | P1 | Done |
| H3 | Run history records the git commit, a dirty flag and package versions (PDF §F). After the drops, old run.yaml files no longer replay as they were, so the commit is what makes a run reproducible. | P2 |  |
| H4 | Write the report (TXT "raporu yaz"): what each stage did, the naturalness numbers, the timing table (P0), before/after screenshots (V2). | P1 (end of stage) |  |
| H5 | esmini issue draft (`2026-10-31-esmini-issue-draft.md`): file it or close it. With simulation out of scope it matters only for viewing time. | P3 |  |
| H6 | ruff warnings: most conflict with the `Optional[...]` style; configure ruff to ignore those rules (UP007, B008) so the real ones stand out. | P3 |  |
| H8 | Instructions: the aim (the `.xodr` is the product; no simulation or training), the pipeline and the module map in `.github/copilot-instructions.md`; `simulate` gone and the known-failing test note removed in CLAUDE.md. | P1 | Done |
| H7 | Schema check: always skipped without the ASAM XSD. Either document that as accepted, or try the ASAM Quality Checker (PDF §F). | P3 |  |

---

## 7. Later: new features, not this stage

These stay listed so they are not lost. They are new features (decision 1).

| Item | Source |
|---|---|
| Comparison with real-city statistics (intersections and dead ends per km², block sizes, orientation entropy, curvature): numbers only, no geometry. The metrics exist (`metrics/network.py`); the reference values and the comparison don't. | PDF §D |
| Generate until every junction-type and curvature bin is filled | PDF §D |
| Divided carriageways, trumpet interchanges, roundabout splitter islands, cloverleafs from lsystem, right-turn pockets | B, C |
| Lanes from demand (population weight in the stroke rank) | refinement review, option C |
| Free-flight camera for viewing ("serbest uçuş") | TXT |
| openx-assets: ASAM 3D models (signs, lights, vehicles, buildings) in the scene | TXT |
| Bigger cities, scalability | user, decision 4 |

---

## Suggested order

1. **Decide.** D4, D10, D14, R7, and the test proposals T1–T6 marked
   "decision".
2. **Fix the three new failures** F17–F19 (stage R regressions), so the
   suite is green apart from the items being dropped.
3. **Measure first.** P0 timing table, V2 screenshots, F3/F4 sweep: the
   "before" of this stage.
4. **Drop.** D1 + D8 together (they unblock F1 and simplify every later
   change), then D2, D3, D5, D9, D13, D14, D17, then the rest of section 1.
   Re-record the lsystem fingerprints **once**, after all generator drops.
5. **Compact the tests** (section 5) right after the drops, since many
   slow tests belong to dropped paths.
6. **Fix.** F5, F7, F3, F6, F8, F14, then P3 items.
7. **Finish.** R4a, R4b, V1, then performance P2–P5.
8. **Docs and report.** H1–H4 last, with the "after" screenshots and timing.
