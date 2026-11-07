# Polish 1: drop old implementations

Implements section 1 ("Drop or simplify") of
[2026-11-07-polish-todo.md](2026-11-07-polish-todo.md): old code paths
still reachable by a flag after better ones replaced them. Item numbers
(D1–D21) refer to that file.

## Prompts

**2026-11-08, prompt 1**

> Let's start with stage 1 (simplifying). Try to implement at least 3 items at once. Group the related items and ask me for the decisions. we can keep parcelling. also for the traceability, only the timestamped markdown files is ok. you can remove scripts if they rae not used anymore. also, the main focus of the project is on the generated xodr file. The esmini or the pipeline after the xodr is just for presentation. Take decisions accordingly.

**2026-11-08, answers to the decision questions**

> Grid goal: Keep grid goal (Recommended)

> Presets: grid, is it possible for us to generate these presets without using these presets. In other words, does expressibility decrease? Are we able to generate these by changing parameters? If so, we can remove these. However, if we remove these presets, we may not be able to generate them easily. We should be able to create city patterns easily like circular (paris), grid (manhattan), etc. one preset of every pattern will be ok.

> Patches: Is there another way? I mean does the quality of the project and the junctions decrease if we remove it. Otherwise we can keep it and fix the esmini later.

> plans/ files: Only unused scripts

**2026-11-08, prompt 2**

> Let's clarify some information on your mind. We are now in dev/polish-stage branch. When I said you do at least 3 items at once, I meant 3-4 items in one response. Not all items in one prompt. However, this is not a problem. Commit them by grouping the item implementations to this branch. Also, update polish-todo.md file by putting Done to the right of an item. I want to use it as a checklist. Do these first. Then we discuss what is remained in stage 1. also, move copilot-prompts.md into plans folder and rename it properly

## Decisions

| # | Question | Decision | By |
|---|---|---|---|
| 1 | Guiding rule | The generated `.xodr` is the product. esmini and everything after the `.xodr` are for presentation only. | user |
| 2 | `global_goal: grid` (D4) | Keep. Only `curve_mode` goes. | user |
| 3 | Presets (D5) | **One preset per pattern.** A preset is only a named parameter set, so dropping one costs no expressiveness: `--set` or a `--params` file rebuilds it exactly, and the docs show how. Kept: `manhattan_like` (grid), `paris` (radial), `organic_town` (organic), `hill_town` (contour), `suburb` (suburb), `skewed_grid` (skew), and the map cities `city`, `mixed_city`, `highway_corridor`. Dropped: `grid` (another exact grid), `grid_organic` (a noisy grid = `city` without maps), `organic_curved` (the only user of `curve_mode: drift`; `organic_town` replaces it). | user (rule), Claude (which) |
| 4 | Junction patches (D10) | Keep them, and write them **always**. Without them the junction surface in the `.xodr` has holes that no lane or object covers. The only other fix is stretching lanes or corner roads over the holes, which changes the driving lanes, and the refinement review rejected it (decision 4 there). Writing them always makes the `.xodr` independent of whether a scene is written (rule 1). The lighter look in esmini is fixed later. `hdmap.junction_patches` is removed. | user (keep), Claude (always) |
| 5 | `plans/` files | Remove only scripts that nothing uses any more. Baselines, images, artifacts and `esmini-issue/` stay. Scripts kept: `refinement_probe.py` and `poke_through.py` (still the probes for naturalness and ground in this stage), `b9_esmini_repro.py` and `esmini_repro_city.sh` (the esmini issue draft, which stays, uses them). | user (rule), Claude (which) |
| 6 | Parcels (D13) | Keep parcels. The one-building-per-block path (`buildings.parcels: false`) goes. | user |
| 7 | `hierarchy: axis` (D1) | Remove. Old run.yaml files with `hierarchy: axis` now fail with an unknown-value error instead of replaying silently with other lanes. | Claude |
| 8 | `curve_mode` (D2, D3) | Remove both modes. `hill_town` moves to the field goal with the contour pattern. The field goal reads the style map when there is one, which would turn hill_town into mixed districts, so a new switch `style_map` (default true) lets a field preset keep its own `pattern_weights` on input maps. `curve_tortuosity` stays and bows streets in both goals. | Claude |
| 9 | `edge_drop_prob` (D6) | Remove. It created the dead ends that stage R3 removes. `transforms/drop.py` goes with it if nothing else uses it. | Claude |
| 10 | `--no-hd` byte identity (D8) | `--no-hd` stays as "driving lanes only". The promise that its output is byte-identical to before stage B, and `test_hdmap_identity.py`, go. That test already failed for 3 of 4 cases. | Claude |
| 11 | Road marks off with HD on (D9), `priorities: false` (D11), `lane_split` options (D12) | Remove all three switches. Marks: `--no-hd` keeps the plain marks it always had. `<priority>` records are always written with HD. `lane_split` stays at `outer`, the only value any preset or example uses. Each is one less `.xodr` variant. | Claude |
| 12 | Simulation (D14, D15) | *(Changed while implementing.)* `simulate` **stays** as a thin presentation command: it is the only way in the repo to watch a scenario (cars, lights switching) in esmini with `--gui`. What goes is everything that served throughput benchmarking: `pipeline --simulate` (and the `pipeline` config section), the runner's memory cap, peak-memory sampling, load-time and real-time measurement (`run_monitored`, psutil), the `.dat` / `.csv` outputs, `test_runner.py` (with the known failing memory test), and `scenario`'s `explicit_routes`. `scenario`, `view` and `drive-check` (which checks the `.xodr`: every movement drivable) stay. | Claude |
| 13 | `--allow-traps` on the examples (D17) | **Not done.** `hill_town` and `city` still report a few traffic traps (dead ends at water and at the area's edge), on HEAD too, so without the flag those examples fail. It becomes a fix item: the dead-end pass or the trap check must handle them first. | Claude |
| 14 | `replay` (D16) | **Kept.** `replay HASH` (and its shortcut `python -m cli HASH`) and `run FILE` take different inputs; neither replaced the other. | Claude |
| 15 | `copilot_prompts.md` (D20) | Moved to `plans/2026-10-08-copilot-phase-prompts.md` (the date it was added) with a short history note at the top; references updated. | user |

## Groups

1. **Generator** (`lsystem`): D1, D2, D3, D5, D6. The lsystem fingerprints
   are re-recorded once, at the end of the group.
2. **`.xodr` builder**: D8, D9, D10, D11, D12.
3. **After the `.xodr`**: D13, D14, D15, D17.
4. **Repo and tooling**: D16, D20, D21 and the docs.

## Results

### Group 1: generator (D1, D2, D3, D5, D6)

- `lsystem.py`: removed `hierarchy` (the axis rule), `curve_mode`,
  `curve_drift_deg`, `curve_reversion` (the OU walk and
  `contour_deviation`), `edge_drop_prob`; `transforms/drop.py` deleted.
  `curve_tortuosity` now bows streets in both goals. New `style_map`
  (default true).
- Presets: removed `grid`, `grid_organic`, `organic_curved`. `hill_town`
  is now the field goal with `pattern_weights` (0, 0, 0, 1, 0) and
  `style_map: false`. `docs/lsystem.md` lists the presets by pattern and the
  `--set` lines for the layouts that lost their preset.
- Tests: `tests/grids.py` builds grids from parameters (`GRID`,
  `GRID_CLI` = the old `grid` preset); one-way tests identify avenues by
  geometry instead of class; `test_curved_growth.py` rewritten for bows and
  the contour pattern (on a diagonal slope, since on a slope along x the
  contour direction equals the grid's); `test_lsystem.py`'s raw build of
  `city` goes through the HD fit as the pipeline does.
- Fingerprints re-recorded once (`tests/data/lsystem_identity.json`):
  cases `default`, `manhattan_like`, `organic_town`, `city_maps`,
  `hill_town_maps`. `default` and `city_maps` kept their old values, which
  shows the removals did not change those networks.
- `hill_town` seed 1 on maps, before → after the move: dead ends 2.5 % →
  2.9 %, lane jumps where two roads meet 0 → 0, class changes along
  straight lines 10 → 11, body overlaps 0 → 0.

### Group 2: `.xodr` builder (D8, D9, D10, D11, D12)

- Deleted `tests/hdmap_identity.py`, `tests/test_hdmap_identity.py`,
  `tests/data/hdmap_identity.json`. `test_hdmap_default.py` still checks
  that `--no-hd` gives driving lanes only.
- Removed `road_marks.enabled`, `hdmap.junction_patches`,
  `junction_control.priorities`, `opendrive.lane_split` (the split is now
  always the outer lane, none at ramp nodes). Patch objects are always
  written; the pipeline no longer turns them off when it writes a scene.

### Group 3: after the `.xodr` (D13, D14, D15)

- `buildings.parcels` removed: one building per parcel always.
- `roadgen/sim/runner.py` rewritten (283 → 120 lines): run esmini, read the
  log. psutil removed from the dependencies. `pipeline --simulate`,
  `--sim-vehicles`, `--sim-duration`, `simulate --no-record / --no-csv`,
  `scenario --mode` and the `pipeline:` config section removed.
- Building tests rewritten for parcels; `test_acceptance_runs_in_esmini`
  (pipeline --simulate) removed; the banned-turn test now checks the
  sidecar's movements instead of planned routes.

### Group 4: repo

- `plans/scripts/`: 38 scripts deleted; kept `refinement_probe.py`,
  `poke_through.py`, `b9_esmini_repro.py`, `esmini_repro_city.sh`. The two
  references from code and docs now point at the stage B plan.
- CLAUDE.md: identity rules, the presets rule, the ".xodr is the product"
  rule, the plans/scripts rule.

### Tests

Affected tests per group green, apart from F17 (`test_maps.py::test_cli_pipeline_without_maps_writes_none`),
F18 (`suburb` naturalness gate) and F19 (`test_district_angles_turn_the_grid`),
which failed before this change too.

Full suite (2026-11-08): 755 passed, 3 failed (F17, F18, F19), 32 tests
fewer than before (787). The first full run took 464 s because tests that
used `grid_organic` (1500 × 900 m) now used `city` at its full 3000 × 2400 m;
they now set `extent=[1500, 900]`, which brings them back to their old
times (for example the elevation scene pipeline 63 s → 11 s, the organic
builds 34 s → 4 s each). Shortening the suite itself is section 5 of the
polish list.
