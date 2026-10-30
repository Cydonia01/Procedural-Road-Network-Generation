# Stage B (HD-map completeness): implementation plan

Status: **approved 2026-10-30; B0–B7 and the lane-section merge (moved from B9) done (2026-10-30), B8 next.** Discussion,
measurements, options and decisions:
[2026-10-29-stage-b-discussion.md](2026-10-29-stage-b-discussion.md).

## Prompts

**2026-10-29, prompt 1**

> Read `Roadgen AV Readiness Review.pdf` (you wrote it in an earlier chat). Stage A
> (geometry accuracy) and stage C (long-tail diversity) are implemented; their
> prompts, discussions, decisions and results are in `plans/` (start with
> `plans/README.md`, `2026-10-24-stage-a-summary.md`, `2026-10-27-stage-c-summary.md`).
> Now we are going to implement stage B: HD-map completeness. I want a
> comprehensive discussion and plan first, before any implementation.
>
> ## What stage B covers (from the review)
>
> 1. Lane types: shoulders, sidewalks, curbs, parking, bike lanes and medians next
>    to the driving lanes. Today every lane is `type="driving"`.
> 2. Road marks: solid yellow centre line, solid lines on junction approaches as a
>    no-lane-change zone, stop lines, no marks inside junctions.
> 3. Signals, signs and objects, placed by distance along the road (s) and offset
>    from it (t): traffic lights with phase plans, stop and yield signs from the
>    road hierarchy with right of way, speed limit signs, and crosswalks at
>    junction borders.
> 4. Junction realism: turn lanes and turn restrictions from a lane-link solver,
>    and corner radii by road class (about 3, 4.9 and 7.6 m, StreetGen §2.3–2.5).
>
> Before planning, check what stage C already did for item 4 (C5 lane splits,
> `plan_movements`, U-turns, corner handling in `roadgen/opendrive/junctions.py`)
> and only plan what is missing. Stage C also left work for stage B on purpose:
> `metadata["roundabouts"]` exists so roundabout entries can get yield signs, and
> diamond interchange ramp ends need signals or stop signs (see
> `plans/2026-10-26-stage-c-discussion.md`). Include both.
>
> ## What I want in the discussion
>
> For each item give the options with their advantages and disadvantages,
> your recommendation, and an effort estimate, like the stage A and C
> discussions. In particular, discuss:
>
> - **Where each decision lives.** Architecture rule: generators only output a
>   RoadGraph and the builder only consumes one. Decide what belongs in the graph
>   (e.g. per-edge cross-section or sidewalk flags, junction control type,
>   priority road) and what the builder derives on its own (e.g. stop lines from
>   junction borders, priority from road class). Prefer deriving in the builder
>   when the graph already has the information, so generators and the lsystem
>   identity fingerprints (`tests/test_lsystem_identity.py`) stay untouched.
> - **Cross-section model.** A params dataclass with presets per road class
>   (highway / avenue / street): which side lanes, their widths, curb heights,
>   median width. Explain how it works with stage A lane sections and width
>   tapers, lane splits, merges/diverges, bridges, tunnels and roundabouts.
> - **Knock-on effects of wider roads.** Sidewalks and shoulders widen every
>   road, which affects junction trims, `scene/buildings.py`
>   (`road_half_extent`), `scene/ground.py` levelling, `maps/drape.py`,
>   `validate/clearance.py`, bridges/overpass widths and repair of short edges.
>   Measure how many edges in the example networks would become too short, as
>   stage A did with `plans/scripts/bend_room.py`.
> - **Junction control.** How to choose between uncontrolled, priority (yield or
>   stop) and traffic lights: by road class pair, leg count, speed and the
>   population map. How right of way goes into OpenDRIVE (`<junction>` priority
>   records, signal dependencies). Where the light phase plans live: OpenDRIVE
>   `<controller>` only, or also OpenSCENARIO `TrafficSignalController` in
>   `scenario/xosc_builder.py`, so esmini actually switches the lights.
> - **Sign catalogue.** Which country code and sign types to use (OpenDRIVE 1.6
>   signal `country`/`type`/`subtype`), and how esmini and OSI handle them.
> - **Default on or off.** New features must be easy to turn off. Say whether the
>   default output should change and what that does to run history replays
>   (`roadgen/history.py`) and existing tests.
> - **Tooling.** What `scenariogeneration` supports for lane types, road marks
>   (yellow, solid-solid), signals, objects and controllers, and where we must
>   write XML ourselves. Keep it behind our builder classes.
>
> ## Constraints
>
> - OpenDRIVE 1.6, right-hand traffic, the conventions in
>   `.github/copilot-instructions.md` and `CLAUDE.md`.
> - esmini must still load every example and pass `drive_check`. Vehicles in
>   `scenario` and `simulate` must spawn and route only on driving lanes.
>   Sidewalk, parking and bike lanes must not break lane selection at junctions.
> - Pluggable parts follow the existing pattern: frozen params dataclasses with
>   presets, defaults in `roadgen/defaults.yaml` and `roadgen/config.py`,
>   deterministic for a seed, CLI flags, `Optional[...]`, units in brackets.
> - Validation: add checks for the new data, e.g. every signal and object lies
>   within its road's length and lanes, every controlled junction approach has a
>   stop line and a sign or signal, no marks inside junctions, and lane types
>   are consistent across links. Extend `metrics` to count signals, objects and
>   lane types per km.
> - Visual check: render the new elements in `plot-graph`/`view` where
>   practical, and render at least the sidewalks and curbs in the `scene/`
>   ground mesh so buildings stand behind them.
> - Papers: StreetGen (file5) §2.3–2.9 and Muktadir et al. / JunctionArt
>   (file10) Stage 3–4. Read the `.md` files, not the PDFs. Use current
>   practice (MUTCD / Vienna Convention, AASHTO, ASAM OpenDRIVE 1.6 and OSI) where
>   the papers are silent.
>
> ## Deliverables of this step
>
> 1. `plans/2026-10-29-stage-b-discussion.md`: options, pros/cons,
>    recommendations and the questions I need to answer. Ask me the questions
>    whose answer changes the plan (for example: country code, defaults
>    on/off, light phases in xosc, scope of B4).
> 2. After my answers, `plans/2026-10-29-stage-b-implementation.md`: phases
>    B0…Bn, as in stage C. Start with B0, a measurement baseline in
>    `plans/baselines/`. Each phase lists its content, the modules it touches,
>    tests, the docs page to update, effort and dependencies. Each phase ends
>    with the full suite green (apart from the known
>    `test_monitor_memory_cap_and_peak` failure), ruff clean on touched files,
>    example networks rebuilt and opened in esmini, and the plan updated
>    wherever the work deviated from it.
> 3. Add both files to `plans/README.md`, with this prompt quoted verbatim and
>    dated in their Prompts sections.
>
> Do not implement anything until I approve the plan.

The user's answers to the four questions of the discussion (also in its
[Decisions](2026-10-29-stage-b-discussion.md#decisions)):

**2026-10-29, answers**

- Country profile: "DE only"
- Default at the end of stage B: "Flip on in last phase (Recommended)"
- Light phases: "xosc controller + fallback (Recommended)"
- B8 scope: "Turn pockets, Medians, Banned turns"

**2026-10-30, prompt 2**

> Plan approved. Implement B0 and stop: report what B0 found about esmini (light cycling, curbs/crosswalks/stop lines, shoulders) and CARLA sign codes, and update the plan before starting B1.

**2026-10-30, prompt 3**

> Good. Before B1: does the junction corner gap stay open in the .xodr itself (for CARLA/OSI), or do the sidewalk corner roads fully close it? Answer that, then commit B0 and implement B1. Stop after B1 and report.

**2026-10-30, prompt 4**

> Shoulders: end them before junctions in the urban and suburban profiles (like parking), keep them through the corner in the rural profile. Record this decision in the plan. Then commit B1 and implement B2. Stop after B2 and report.

**2026-10-30, prompt 5**

> Commit B2, then implement B3 (close the junction corner gaps in the .xodr). After B3, before B4, do the lane-section merge from B9 (align parking/shoulder taper start and end points) so later phases build on smaller files; record the move in the plan. Stop after that and report sizes and load times against the B2 numbers.

**2026-10-30, prompt 6** (new session after the previous one ran out of context)

> We are in the middle of stage B (HD-map completeness) of
> `Roadgen AV Readiness Review.pdf`. A previous session ran out of context; you
> continue its work on branch `dev/stage-b`.
>
> ## Read first
>
> - `CLAUDE.md` and `.github/copilot-instructions.md` (project rules).
> - `plans/README.md`, then `plans/2026-10-29-stage-b-discussion.md` and
>   `plans/2026-10-29-stage-b-implementation.md`. The implementation plan is the
>   source of truth: the phases B0–B10, every decision (look for "changed after
>   B0", the shoulder decision, B3½), the result of each phase and the open items.
> - `git log --oneline -10` and `git status`. B0 (on dev/stage-b), B1 (75fab39)
>   and B2 (1770ff3) are committed. B3 (junction corner roads that close the
>   corner gaps) and B3½ (lane-section merge: side lanes and medians run the
>   whole road at 0 m where absent, so their tapers don't start new sections)
>   may still be uncommitted. If so: review the diff against the B3 / B3½
>   sections of the plan, run the full suite, and commit them as two commits
>   if they separate cleanly, otherwise one.
>
> ## Status at hand-off
>
> - Done: B0, B1, B2, B3, B3½. Suite: 616 passing plus the known
>   `test_monitor_memory_cap_and_peak` failure. All 11 example networks build and
>   validate with `--hd`; drive-check and simulate pass. With HD off, the output
>   is byte-identical to before stage B (builder identity guard) and the lsystem
>   identity test passes. Keep both true until the default switch in the last phase.
> - Open items, with my decisions:
>   1. esmini loads more slowly because of the B3 corner roads (city 26 → 52 s,
>      manhattan_like 13 → 33 s; stepping is unaffected). Accept it for now. In
>      B9, first try to find the cause (e.g. build esmini with profiling
>      symbols, or bisect by road attributes in a small fixture). If it can't be
>      fixed, add a switch that leaves corner roads out for throughput
>      benchmarks.
>   2. Uncovered surfaces at one-way corners and slivers between turning lanes:
>      approved as a junction-surface fill in B8, as proposed.
>   3. suburb repairs 1.3 % of edges (target 1 %): accepted.
>
> ## Working style (keep it)
>
> - One phase at a time. After each phase: suite green apart from the known
>   failure, ruff clean on touched files, the 11 examples rebuilt and loaded in
>   esmini, screenshots under `out/stage_b/<phase>/`, the phase result and every
>   deviation (with the reason) written into the implementation plan, the matching
>   docs page updated. Then stop and report: what was added, what changed from
>   the plan and why, numbers against the previous phase (sizes, load and
>   pipeline times, repaired edges), and the decisions you need from me.
> - Commit a phase only after I approve it, unless I say otherwise. Never start
>   the next phase in the same change.
> - Numbers quoted in the plan come from scripts in `plans/scripts/` and
>   baselines in `plans/baselines/`.
> - Append this prompt, verbatim and dated, to the Prompts section of the
>   implementation plan.
>
> ## Next
>
> Commit B3/B3½ if needed (above), then implement B4 as the plan describes.
> Stop after B4 and report.

**2026-10-30, prompt 7** (answers to the B4 report)

> 1. Yes, commit B4 as one commit.
> 2. Streets in the urban and suburban profiles get no centre line (German residential practice); keep it on avenues, highways and in the rural profile. Make it a road_marks option so it can be turned back on.
> 3. Curb mark: check esmini's OSI output (--osi_file) on a small fixture. If the curb mark becomes an OSI lane boundary of type curb, keep it and note esmini's white rendering as a known limitation; if not, drop it.
> Record these decisions in the plan, commit, then implement B5. Stop after B5 and report.

**2026-10-30, prompt 8** (answers to the B5 report)

> 1. Yes, commit B5 as one commit.
> 2. Option (a): require yield, stop and right-before-left together on mixed_city only. Also record in the plan, as an item for stage D, that city has no street × street junctions at all (a diversity gap of the generator, not of stage B).
> 3. 301 alone is fine for now. In B7, add 306/307 along priority roads if the road can be traced through consecutive junctions with the existing chain data at reasonable cost; otherwise leave it as an open item.
> Record these decisions in the plan, commit, then implement B6. Stop after B6 and report.

**2026-10-30, prompt 9** (answers to the B6 report)

> 1. Yes, commit B6 as one commit.
> 2. Single-phase light junctions: combine (a) and (b). In B7, where such a junction gets crosswalks, keep the lights and make the extra phase the pedestrian phase; where it gets none, annotate demotes it from lights to priority control. Record this in the plan.
> Then implement B7. Stop after B7 and report.

**2026-10-31, prompt 10** (answers to the B7 report)

> Yes, commit B7 as one commit.
> One realism change before B8: in Germany zebras are rare at unsignalised junctions. Keep crossings at every light-controlled arm, but at unsignalised junctions put zebras only on avenue arms in dense areas (reuse the population thresholds from B5; without maps, avenues only), and none on street × street junctions. Make it an option, record the decision in the plan, and report the new crosswalk counts. Commit that separately, then implement B8. Stop after B8 and report.

## Context

Stage A (geometry) and stage C (diversity) of `Roadgen AV Readiness
Review.pdf` are done. Stage B makes the maps complete as HD maps: lane types
beside the driving lanes, road marks, signals, signs and objects, junction
control with right of way and light phases, and junction realism (corner
radii, lane use, turn pockets, medians, banned turns). Today every lane is
`driving`, there are no signals or objects, and junctions have no right of
way (see "What exists today" in the discussion).

Decided: German catalogue only (StVO / Vienna Convention, white marks);
features off while they are built and switched on by default in the last
phase; light phase plans also in OpenSCENARIO; all of item 4.

## Shared conventions for every phase

- **Architecture.** Generators and transforms are not touched; the lsystem
  identity test stays green. The builder derives everything from the graph.
  The graph gains two optional overrides, left out of the JSON when unset
  (`roadgen/graph/io.py::_OPTIONAL`):
  - `Edge.cross_section: Optional[str]` (profile name, `None` = derive from the road class);
  - `Node.control: Optional[str]` (control type, `None` = derive).

  They are set only by the new `annotate` step (from the maps) or by hand.
  Map-dependent work stays out of the builder.
- **One switch.** `hdmap.enabled` (`defaults.yaml`, false until B10). With it
  off, the builder's `.xodr` and sidecar are **byte-identical** to B0's; a new
  builder identity guard (B0) checks this in every phase.
- **New config sections** in `roadgen/defaults.yaml`, each with a frozen
  params dataclass that reads its defaults from there (`roadgen/config.py`,
  the config test checks they match), presets, `Optional[...]`, units in
  brackets:
  - `hdmap` (enabled);
  - `cross_section` (profiles, parking and shoulder ends, corner radii, medians);
  - `road_marks`;
  - `junction_control`;
  - `traffic_lights`;
  - `turn_lanes`;
  - `annotate`.
- **Bundled builder params.** The builder takes them as one
  `HdMapParams(enabled, seed, cross_section, marks, control, lights,
  turn_lanes)`, next to `DesignParams` and `JunctionParams`.
- **Randomness** (stop share, light offsets, banned turns) comes from the
  run seed the pipeline passes to the builder, in salted sub-streams
  `np.random.default_rng([seed, SALT])`, one salt per feature.
- **XML** goes through scenariogeneration where it can (lane types, heights,
  marks, signals, validity, objects, outlines, markings). We write by hand,
  in `OpenDriveBuilder.write` next to `_add_structures`:
  - junction `<priority>`;
  - top-level `<controller>`;
  - junction `<controller>`;
  - signal `<dependency>`.
- **Sign encoding** (B0): `subtype="-1"` for codes without a DE subtype, but an empty `subtype` when the sign carries a `value` (esmini looks signs up as `type[.subtype][-value]`, so `274` with subtype "-1" and value 50 matches nothing). `value` is written as an integer string ("50", not "50.0", which misses esmini's `de_274-50.osgb` model). Codes with a DE subtype (350-10, 103-10, 209-20, …) are written as the standard requires (`subtype="10"`). esmini then cannot type them for OSI, because its table spells them "350-10"; this is accepted as an esmini limitation. scenariogeneration's `Object` writes `subtype="None"` when none is given, so the builder always passes one.
- **Country:** `country="DE"` for signs and marking objects,
  `country="OpenDRIVE"` for lights (1000001), as esmini's sample does.
- **Example set** (rebuilt at the end of every phase; the same networks as
  `plans/scripts/side_room.py`):
  - without maps: `grid` s0, `manhattan_like` s1, `paris` s1, `suburb` s1, `organic_town` s1;
  - on maps: `city` s7, `mixed_city` s1, `highway_corridor` s1;
  - single junctions: `junction` presets `k`, `roundabout` and `diamond` (s0).

  The script `plans/scripts/stage_b_examples.sh` (B0) builds them all into
  `out/` with the phase's features on.
- **Each phase ends with:**
  - the full suite green (`docker compose run --rm -T dev python -m pytest -q`), apart from the known `test_monitor_memory_cap_and_peak` failure;
  - `ruff check` clean on the touched files;
  - the example set rebuilt, every validator passing, the drive-check passing on every example except the interchanges (still open since C9);
  - every example loaded in esmini: a short headless `simulate`, plus an odrviewer screenshot with `view --screenshot` (B0), so changes can be seen without the GUI; the `view` commands listed in `runs/README.md` for a look in the GUI;
  - `metrics --hd` compared with the B0 baseline, and the numbers quoted in the phase's result;
  - the matching docs page updated;
  - this plan updated wherever the work deviated from it, as a "Bn result" subsection.

## Phases

| Phase | Content | Review item | Effort | Needs |
|---|---|---|---|---|
| B0 | HD metrics and baseline, builder identity guard, esmini / OSI spike, density calibration, `view --screenshot` | all | 2 d | — |
| B1 | Cross-section core: params, lane roles, trims with corner radii, cross-section-aware repair | 1, 4 | 3.5 d | B0 |
| B2 | Side lanes and medians in the builder; `plot-xodr` | 1 | 3.5 d | B1 |
| B3 | Knock-on: buildings, ground, drape, grade separation; streetscape mesh; sidewalk corners | 1 | 2.5 d | B2 |
| B3½ | Lane-section merge (moved here from B9 by prompt 5) | 1 | 0.5 d | B3 |
| B4 | Road marks (DE) | 2 | 1.5 d | B2 |
| B5 | Junction control, right of way, signs, stop and yield lines; `annotate` step | 3 | 4 d | B2, B4 |
| B6 | Traffic lights, controllers, phase plans, OpenSCENARIO controllers | 3 | 3 d | B5 |
| B7 | Crosswalks, speed and curve signs | 3 | 1.5 d | B5 |
| B8 | Lane use and arrows, turn pockets, medians at junctions, banned turns | 4 | 4.5 d | B1, B2, B5, B6 |
| B9 | Validation and metrics completed, sweeps, performance | all | 1.5 d | B8 |
| B10 | Default flip, docs, examples, final measurement, supervisor summary | all | 2 d | all |

About 29.5 days (6 weeks). B3 and B4 can run in either order; B7 can come
before or after B6. Each of B2, B4, B5–B6 and B8 is usable on its own behind
its switch.

---

### B0 · Baseline, identity guard, esmini spike

- **HD metrics** in a new `roadgen/metrics/hdmap.py`, exposed as `metrics --hd`. It reads the `.xodr`, plus the sidecar where there is one, and reports:
  - length of each lane type per km of road;
  - marks by type and colour;
  - signals by country / type / subtype, and objects by type, per km and per junction;
  - controllers and phases;
  - the junction control mix (from the sidecar, from B5 on);
  - crosswalks per junction;
  - turn pockets and arrows.

  On today's output everything but driving lanes is zero. That zero is the baseline.
- **Baseline:**
  - `plans/baselines/2026-10-29-stage-b-before-hd.json`: `metrics --hd`, the geometry metrics (advisory share, turn speeds) and `metrics --graph` for the example set;
  - per example: build time, `.xodr` size, esmini load time and real-time factor (`simulate`, 50 cars, 30 s, on `city` and `mixed_city`).
- **Builder identity guard:** `tests/hdmap_identity.py` and `tests/test_hdmap_identity.py`. They record SHA-256 hashes of the `.xodr` and the sidecar for `grid`, `manhattan_like`, `organic_town` and `city` on maps, built with today's code. From then on these must match whenever `hdmap.enabled` is false (with `--no-hd` after B10).
- **esmini / OSI spike:** `plans/scripts/esmini_spike.py` writes small hand-made fixtures to `out/spike/`:
  - a straight road with every side lane type, `<height>` and `level`;
  - marks: `solid_solid`, broken, `curb`, `laneChange="none"`, white;
  - a crosswalk object with outline and zebra markings;
  - a `roadMark` object as a stop line;
  - signals 205, 206, 274-50, 306, 294 and 1000001 with validity;
  - a controller, a junction controller and junction priorities;
  - a 4-leg junction with sidewalk-only corner connecting roads, and a median;
  - two `.xosc`: one with a RoadNetwork `TrafficSignalController`, one with storyboard light events.

  For each it records:
  - esmini's load log (errors, warnings);
  - an odrviewer screenshot;
  - whether odrviewer's and our scenario's traffic stays on driving lanes (esmini `.dat` → csv positions vs lane types);
  - whether the light changes state, read from esmini's OSI output (`--osi_file`, decoded with the `osi3` package, added to the dev dependencies if needed) or, failing that, from screenshots at two times;
  - which OSI sign types come out.

  The findings decide:
  - curb and sidewalk heights in the `.xodr` vs. in the scene mesh (B3);
  - sidewalk corner roads vs. corner fills (B3);
  - stop-line form (B5);
  - controller vs. storyboard lights (B6).

  They are written into this plan.
- **`view --screenshot PNG [--at SECONDS]`:** odrviewer's `--capture_screen` under the dev container's Xvfb (the `gui-mac` entrypoint already starts one). If odrviewer cannot capture headless, esmini with a minimal `.xosc` and `--capture_screen` does the same. If neither works, the phase checks fall back to the GUI and say so.
- **Density calibration:** `plans/scripts/control_mix.py`. It extends `side_room.py` with the share of each class pair above candidate thresholds on `city`, `mixed_city` and `highway_corridor`, and picks:
  - `signal_density` and `stop_density` (target: lights at roughly a quarter of `city`'s avenue × street junctions);
  - `urban_density` and `suburban_density` for the profiles.
- **Modules:**
  - new: `roadgen/metrics/hdmap.py`, `tests/hdmap_identity.py`, `tests/test_hdmap_identity.py`, `tests/test_hdmap_metrics.py`, `plans/scripts/esmini_spike.py`, `plans/scripts/control_mix.py`, `plans/scripts/stage_b_examples.sh`;
  - changed: `cli.py` (`metrics --hd`, `view --screenshot`).
- **Tests:** HD metrics on a hand-made `.xodr` with known counts; identity guard; screenshot option (skipped where there is no display).
- **Docs:** `docs/cli-reference.md` (`metrics --hd`, `view --screenshot`), `docs/development.md` (the builder identity guard).

#### B0 result (done 2026-10-30)

Delivered as planned, with these deviations:

- **`view --screenshot` renders with esmini, not odrviewer.** odrviewer can capture frames (`--capture_screen`), but its camera always follows a car and has no camera choice. `roadgen/sim/screenshot.py` instead runs esmini on a scenario without vehicles, with a fixed custom camera (`--camera_mode custom --custom_fixed_camera x,y,z,h,p`; pitch π/2 gives a top view), under `xvfb-run` in the dev container. Options: `--camera X,Y,Z[,H,P]` and `--at S`.
- **OSI was not decoded.** The container has no `osi3` package, and esmini's own sign lookup log and source settled the questions, so none was added. Light switching was checked with screenshots instead.
- **The baseline file is dated 2026-10-30:** `baselines/2026-10-30-stage-b-before-hd.json`. The calibration output is `baselines/2026-10-30-stage-b-control-mix.json`.
- `stage_b_examples.sh` writes its own log to `NAME.run.log`, since esmini's simulate log is `NAME.log`.

**Builder identity guard:** `tests/test_hdmap_identity.py` with hashes in `tests/data/hdmap_identity.json`, for `grid`, `manhattan_like`, `organic_town` and `city` on maps (25 s).

**Baseline** (`stage_b_examples.sh out/stage_b/b0`): all 11 examples build, validate and, except the interchange and the highway corridor, pass the drive-check. In every network:

- only `driving` lanes (2.3–3.7 lane-km per road-km);
- only white `solid` / `broken` marks;
- no marks on connecting roads;
- no signals, objects or controllers.

| Example | Pipeline [s] | `.xodr` [MB] | Road [km] |
|---|---|---|---|
| grid | 9.0 | 2.4 | 26.4 |
| manhattan_like | 35.1 | 6.5 | 83.3 |
| paris | 21.1 | 5.3 | 43.4 |
| suburb | 21.3 | 5.6 | 28.9 |
| organic_town | 9.3 | 2.4 | 19.4 |
| city (maps) | 78.9 | 18.3 | 64.8 |
| mixed_city (maps) | 170.0 | 23.2 | 67.6 |
| highway_corridor (maps) | 8.5 | 7.5 | 32.9 |
| junction k / roundabout / diamond | 2.2 / 2.2 / 2.0 | 0.1 | 0.7 / 0.7 / 6.7 |

esmini (50 cars, 30 s): `city` loads in 17.3 s and steps at 87× real time, peak 134 MB; `mixed_city` loads in 48.1 s and steps at 64× real time, peak 147 MB; 0 errors in both. Pipeline times include the drive-check, and the full suite ran in parallel, so they are upper bounds.

**Density calibration** (`control_mix.py`, `city` s7, `mixed_city` s1, `highway_corridor` s1):

| Parameter | Value [people/km²] | Effect on these networks |
|---|---|---|
| `signal_density` | 5,500 | lights at 26 % (`city`), 21 % (`mixed_city`) and 24 % (`highway_corridor`) of avenue × street junctions |
| `stop_density` | 1,500 | stop instead of yield at the sparsest 10–20 % of them |
| `urban_density` | 3,000 | 55–72 % of road length urban |
| `suburban_density` | 1,500 | 9–14 % of road length rural |

**esmini 3.9 spike** (`esmini_spike.py`; fixtures, screenshots and `report.json` in `out/spike/`):

| Question | Finding | Consequence |
|---|---|---|
| Light cycling | esmini's `parseRoadNetwork` reads only `LogicFile` and `SceneGraphFile`, so **RoadNetwork `TrafficSignalController`s are ignored**, and `TrafficSignalControllerAction` logs "not supported yet". Screenshots: with a controller the light stays dark at 1.5 s and 4.5 s; with storyboard `TrafficSignalStateAction`s (name = signal id, `"on;off;off"` / `"off;off;on"`) it is red, then green. | B6 always writes storyboard events; the controller stays in for other tools |
| Lane types | Drawn: driving, parking and shoulder as asphalt; sidewalk and biking light grey; border and median grass green; a 0.15 m curb lane is too thin to see | as planned |
| Curbs | **`<height>` is honoured:** with 0.15 m on curb and sidewalk lanes esmini draws a curb face; without it the sidewalk is flush (side-by-side screenshots) | B3: heights in the `.xodr`, no slabs or curb faces in the scene |
| Junction surface | With side lanes, the area between the outermost turn paths and the sidewalk corners is not covered by any road, so the sky shows through | B3 (revised after prompt 3): close it in the `.xodr`, with the corner roads built on the right-turn path |
| Sidewalk corner roads | drawn; topology and continuity checks pass; every movement passes the drive-check | B3 option (a) adopted |
| Crosswalks | Drawn as a zebra only as an outline with corner ids plus a `<marking>` with two `<cornerReference>`s; a "side" marking needs the object's `length` / `width` box, which the first try lacked (nothing drawn) | B7 encoding |
| Stop lines | The same encoding draws a white 0.5 m line. An outline without markings is a grey box. DE 294 has no OSI type in esmini, and CARLA has no 294 | B5: `roadMark` object only, no 294 signal |
| Signs | Models only for `de_274-30/-50` (and the light); other signs are grey placeholder boxes. OSI typing uses the key `type[.subtype][-value]`: 205, 206, 306, 215 typed; `274` typed only with an empty subtype and `value="50"`; 350-10 and other subtype signs never typed (esmini's table spells them "350-10"); lights have no sign type (they are OSI traffic lights) | sign encoding rule in the shared conventions; no sign posts in the scene |
| Shoulders and other non-driving lanes | esmini's `IsDriving()` uses `LANE_TYPE_ANY_DRIVING` = driving, entry, exit, on / off / connecting ramp and bidirectional; odrviewer spawns only there (`GetNumberOfDrivingLanes`). Our random-lanes scenario on the fixture: 24,060 logged positions, **0 off driving lanes, 0 on sidewalk corner roads** | shoulders, parking and bike lanes are safe; ramps must stay `driving`, since our scenario builder filters on `type="driving"` |
| Medians | A median lane at ±1 (driving -2, -3): esmini's lane centres match ours (lane drive), validators pass | B2 medians as planned |
| Junction `<priority>`, `<controller>` | load without warnings | as planned |
| scenariogeneration quirks | `Object` without a subtype writes `subtype="None"`; `Signal` writes `value="50.0"` | handled by the encoding rule |

**CARLA** (`LibCarla/source/carla/road/SignalType.cpp`, master): signs are identified by German codes: 101, 121, 133, 138, 150, 205 yield, 206 stop, 209, 211, 214, 215, 222, 250–267, 272, 274 speed, 276, 277, 283, 286, 301, 306, 307, 310, 311, 330, 357, 380, 381. Lights are 1000001 / 1000002 (and others) by `IsTrafficLight`. There is **no 294** (the discussion had assumed one), and the country is not part of the check. So the DE catalogue serves CARLA too; stop lines come from geometry, not signals.

Exit of B0: full suite 570 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); ruff clean on the new files apart from the project's `Optional[...]` style (`cli.py` unchanged: 5 findings before and after); docs updated (`cli-reference.md`, `validation.md`, `development.md`, `architecture.md`, `README.md`).

### B1 · Cross-section core: params, lane roles, trims, repair

- **`roadgen/geometry/cross_section.py`** (neutral, no OpenDRIVE code):
  - `SideLane(kind, width [m], height [m])`, kinds `parking | biking | curb | sidewalk | shoulder | border`.
  - `ClassLayout(sides: tuple[SideLane, ...], median [m])`.
  - `CrossSectionParams`, frozen, with:
    - `profile` (default for edges without `Edge.cross_section`);
    - per-profile layouts for street / avenue / highway, presets `urban`, `suburban`, `rural`, `none` with the widths of the discussion's section 1;
    - `parking_clear` 10 m, `side_taper` 5 m, `tunnel_shoulder` 1.0 m;
    - `corner_radius` street 3.0 / avenue 4.9 / highway 7.6 m;
    - `max_corner_share` 0.3;
    - `median_min_lanes` 2.
  - Functions: `edge_layout(edge, params)` (side lanes per side seen along the edge, median); `curb_half_widths(edge, params, at_junction)`; `outer_half_widths(...)`. The junction variants leave out side lanes that end before junctions (parking always; shoulders at ramp nodes).
- **Graph:** `Edge.cross_section` and `Node.control` (`model.py`, `io.py::_OPTIONAL`, `docs/road-graph.md`). The builder rejects an unknown profile name with a clear error.
- **Trims** (`geometry/junction_space.py`): `node_trims(graph, node, margin, cross_section=None)`.
  - `Arm` gains curb and outer widths and its class radius.
  - `junction_trims` takes the curb-line crossing plus the corner fillet's tangent length `r / tan(θ/2)`, with r = the smaller of the two arms' radii (StreetGen §2.4).
  - The radius is shrunk until the tangent point lies within `max_corner_share` of the shorter arm (StreetGen §2.5), and the effective radius is returned for the sidecar.
  - Outer edges must not overlap (no fillet).
  - `cross_section=None` is today's code path, unchanged.
- **Alignment** (`geometry/alignment.py`): `network_alignment(..., cross_section=None)` passes it to the trims. With it set, the road clearance uses the outer half width. The speed model keeps `half_width` (driving lanes only).
- **Lane roles:**
  - `geometry/profiles.py::LaneLayout` sections hold typed pieces `LanePiece(kind, index, width)`. In B1 they are driving only, which keeps the output identical.
  - `opendrive/junctions.py::RoadEnd` gets explicit incoming and outgoing driving lane ids and each lane centre's offset t, taken from the layout at the road end, instead of `count × width`; `lane_pose` uses them.
  - This is the generalisation that medians and turn pockets need (discussion, section 1).
- **Repair with cross-sections:** `generators/repair.py` functions take `cross_section` and pass it to `node_trims` / `network_alignment`. A new `fit_graph(graph, cfg)` in `cli.py` runs `repair_short_edges` with the active cross-section, before the build:
  - in `pipeline` and `build-xodr` when `hdmap.enabled`;
  - the pipeline saves the graph after it, as it does after `align` / `drape` / `separate`;
  - `generate` and the generators are unchanged.
- **Measure:** the real trims on the example set with `urban` / `suburban` / `rural`: edges repaired, stubs, mean trim, how often the corner radius was shrunk. Compare with `side_room.py`; target at most 1 % of junction roads repaired.
- **Modules:**
  - new: `roadgen/geometry/cross_section.py`;
  - changed: `roadgen/graph/model.py`, `roadgen/graph/io.py`, `roadgen/geometry/junction_space.py`, `roadgen/geometry/alignment.py`, `roadgen/geometry/profiles.py`, `roadgen/opendrive/junctions.py`, `roadgen/opendrive/builder.py` (passes params), `roadgen/generators/repair.py`, `roadgen/config.py`, `roadgen/defaults.yaml`, `cli.py`.
- **Tests:**
  - params and presets match `defaults.yaml`;
  - trims with fillets at 90°, 60° and 30°, shrink-to-fit, outer edges;
  - `None` gives identical trims;
  - `RoadEnd` ids and offsets equal today's for plain roads;
  - the repair makes `suburb` s1 buildable with `urban` + parking;
  - graph JSON round-trip with the new optional fields;
  - identity guards (lsystem and builder) pass.
- **Docs:**
  - `docs/geometry.md` (cross-sections, trims, corner radii);
  - `docs/road-graph.md` (the new fields);
  - `docs/cli-reference.md` (`cross_section` parameters, `--hd`).

#### B1 result (done 2026-10-30)

Delivered as planned (`geometry/cross_section.py`, `Edge.cross_section`, `Node.control`, lane roles, trims with curb returns, the cross-section-aware fit, `--hd` / `--no-hd`, `HdMapParams` in `roadgen/opendrive/hdmap.py`). It needed these changes, each found on the example networks:

- **Trim baseline on curb lines.** The baseline (every arm trimmed to the widest half cross-section at the node) now uses the curb lines, not the outer edges. With sidewalks in it, every roundabout ring arc lost the approaching avenue's full 11.65 m at its entry, and its bend became too tight. Sidewalks now only must not overlap, which the pairwise outer-edge check covers.
- **No curb returns at two-road nodes or where a loop starts.** A bend or lane-count change kept as a junction is a road continuing, not a street corner. At roundabout entries and cul-de-sac stems, the stage C transforms designed the corners and sized the rings for today's trims. Bike lanes end before a loop starts (FHWA roundabout guide), like parking before every junction.
- **Loops in B1, not B2.** Ring and cul-de-sac loop edges (from the graph metadata) keep only curb and sidewalk outside and a curb on the island side. Their widths feed the trims and the alignment's clearance, so this could not wait for B2. `EdgeCrossSection` therefore holds the two sides separately.
- **The fit repairs only what the side lanes make worse:**
  - it leaves the stub roads the network already has (`keep_stubs`); today's `suburb` has 90, mostly cul-de-sac loop arcs, and the first version collapsed 96 edges there;
  - it also treats a dead-end road with less than 5 m left as a stub (`dead_ends`; `city` had one 0.52 m long that broke the continuity check);
  - an edge the alignment finds too short for a bend it keeps as a junction counts as too short (`ShortEdgeError`);
  - it never joins more roads at a node than the network already does (at least 5);
  - it records `metadata.fit`.

  The generators' own repair is unchanged (the lsystem fingerprints hold).
- `RoadEnd.lane_t` (explicit driving lanes) is passed only where a road has non-driving lanes, so plain roads keep their `count × width` offsets bit for bit (the builder guard holds).
- `--cross-section PROFILE` stays in B2, as planned.

**Shoulders at junctions (user decision, prompt 4):** in the `urban` and `suburban` profiles shoulders end before every junction, like parking; in the `rural` profile they run through the corner (and still end before ramp nodes in every profile). This was the open question of the first B1 report: highway shoulders reaching at-grade junctions caused most of `city`'s too-short edges (`b1_short_causes.py`: 16 too short with them, 3 without).

**Measurement** (`b1_trims.py` → `baselines/2026-10-30-stage-b-b1-trims.json`, after the shoulder rule): every network builds with every profile. Repaired edges as a share of junction road ends:

| Network | Today's mean trim [m] | `urban`: too short / repaired / share / mean trim [m] | `suburban` repaired | `rural` repaired |
|---|---|---|---|---|
| grid | 9.0 | 0 / 0 / 0 % / 12.1 | 0 | 0 |
| manhattan_like | 15.1 | 0 / 0 / 0 % / 17.5 | 0 | 0 |
| grid_organic | 9.2 | 0 / 0 / 0 % / 12.7 | 0 | 0 |
| organic_town | 9.2 | 0 / 0 / 0 % / 12.0 | 0 | 0 |
| paris | 9.4 | 0 / 2 / 0.3 % / 13.3 | 2 | 3 |
| suburb | 8.5 | 0 / 12 / 1.3 % / 11.1 | 10 (1.1 %) | 19 (2.0 %) |
| city (maps) | 10.1 | 3 / 10 / 0.9 % / 14.7 | 10 | 23 (2.0 %) |
| mixed_city (maps) | 9.8 | 3 / 11 / 0.7 % / 13.7 | 11 | 16 (1.1 %) |
| highway_corridor (maps) | 9.3 | 0 / 2 / 0.4 % / 13.0 | 2 | 2 |

Curb returns were shrunk to fit at 4 of about 900 corners (`city`). Before the shoulder rule `city` needed 23 repairs (2.0 %) with `urban`, `mixed_city` 12.

- **Target** (≤ 1 % repaired) met with `urban` everywhere but `suburb` (1.3 %), and with `suburban` everywhere but `suburb` (1.1 %).
- `suburb` has no highways, so its repairs come from short streets between its many T junctions and loops.
- `rural` keeps shoulders through corners by decision, so its 2.0 % on `city` and `suburb` is the price of that.

**Example set with `--hd`** (`stage_b_examples.sh out/stage_b/b1 --hd`, run before the shoulder rule): all 11 networks build and validate; the drive-check passes everywhere it ran (`city` 3,151 movements and 1,882 lanes; `mixed_city` 4,170 and 2,222); esmini simulates `city` and `mixed_city` with 0 errors. Screenshots: `out/stage_b/b1/*.view.png`.

| | B0 | B1 with `--hd` |
|---|---|---|
| `city` esmini load | 17.3 s | 19.4 s |
| `mixed_city` esmini load | 48.1 s | 60.0 s |
| `city` / `mixed_city` / `suburb` fit | — | 18.6 / 26.6 / 20.0 s |

- **Fit cost:** each repair step re-aligns the whole network.
- **Slow machine late in the phase:** run alone, the drive-check of `mixed_city` took about 1.5 h, for both the B1 file and the unchanged B0 file (under 170 s this morning). So this is the machine, not B1. The measurement fits of 1,000–1,900 s in the table's JSON (`suburban` and `rural` on `suburb`, `city`, `mixed_city`) come from the same slowdown; the `urban` fits of the same networks took 18–126 s.

Exit of B1: full suite 587 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); both identity guards (lsystem, builder) pass; ruff clean on touched files apart from the project's `Optional[...]` style and the existing B008 params defaults; docs updated (`geometry.md` cross-sections and curb returns, `road-graph.md`, `generators.md`, `cli-reference.md`). Helper scripts: `b1_trims.py`, `b1_short_causes.py`, `b1_stubs.py`.

### B2 · Side lanes and medians in the builder; `plot-xodr`

- **Lane layout** (`geometry/profiles.py::lane_layout(graph, ca, cross_section)`) adds the side pieces of each edge's profile:
  - **parking:** ends `parking_clear` before every junction end of the chain and before bridges, with a `side_taper` smoothstep (a curb extension);
  - **shoulders:** end before ramp nodes; narrow to `tunnel_shoulder` inside tunnels;
  - **medians:** on two-way highways with `median_min_lanes` or more each way (avenues if the profile says so); they taper to 0 before junctions (B8 turns them into pockets);
  - **one-way roads:** side lanes on both edges; the left edge's become positive lanes;
  - **roundabout rings and cul-de-sac loops:** recognised from `metadata["roundabouts"]` / `metadata["culdesacs"]`; outer side as the arms' profile, inner side a curb only.
- **Lanes** (`opendrive/lanes.py`): `make_lane_sections` writes OpenDRIVE lane types (`parking`, `biking`, `curb`, `sidewalk`, `shoulder`, `border`, `median`).
  - Curb and sidewalk heights go in `<height>` if B0 shows esmini uses them (else 0 here, with the height in the scene mesh, B3).
  - Lane links connect lanes **by role** (same type, same index within the type), so a sidewalk whose id shifts at a lane drop stays linked to the sidewalk.
  - Marks stay as today until B4.
- **Builder:**
  - `RoadEnd` from the layout (B1);
  - connecting roads unchanged, driving lanes only;
  - sidecar `cross_sections` (road id → profile) when HD is on.
- **CLI:** `pipeline` / `build-xodr` `--hd` / `--no-hd` and `--cross-section PROFILE` (forces one profile on every edge).
- **`plot-xodr`** (new command, `roadgen/opendrive/plot.py`): draws the built lanes as polygons coloured by type from the `.xodr`, using the validators' road evaluator (`validate/continuity.py`). Later phases add marks, signals, objects and control glyphs. The pipeline writes `PREFIX_hd.png` when HD is on.
- **Modules:**
  - new: `roadgen/opendrive/plot.py`;
  - changed: `roadgen/geometry/profiles.py`, `roadgen/opendrive/lanes.py`, `roadgen/opendrive/builder.py`, `cli.py`.
- **Tests:**
  - every profile's lane types and widths;
  - role links continuous through tapers and lane drops (the continuity validator passes);
  - parking width 0 at the junction end;
  - shoulders gone at ramp nodes;
  - one-way side lanes;
  - medians: driving ids shift, movements and drive-check still pass;
  - **scenario spawn slots and routes only on driving lanes** with side lanes and medians;
  - drive-check on `grid`, `roundabout` and `diamond` with HD on;
  - HD off identical.
- **Docs:** `docs/opendrive.md` (Lanes: side lanes, medians, role links), `docs/cli-reference.md` (`--hd`, `--cross-section`, `plot-xodr`), `docs/architecture.md` (module map).

#### B2 result (done 2026-10-30)

Delivered as planned: lane layout with side lanes and medians, lane types and curb / sidewalk heights in `make_lane_sections`, `--cross-section`, `plot-xodr` (and `PREFIX_hd.png` from `pipeline --hd`), and sidecar `cross_sections`. Changes against the plan, each found on the examples:

- **The median lane runs the whole road, 0 m wide where there is no median.** Planned: medians taper to 0 before junctions. As a lane that then disappears, the median shifted the driving lane ids along the road (−1 mid-road, the first driving lane near the junction). Everything that follows a lane by id broke: the scenario's whole-road lanes and routes, esmini's drive-check (the `diamond` and the median test failed). With the lane kept at 0 m the driving lanes are −2, −3 … along the whole road; the median still tapers out (MUTCD shift taper) to end 5 m before every junction. `metrics --hd` leaves out lanes that are 0 m wide over a whole section.
- **Shoulders and bike lanes that end at a junction narrow to 0 m at the road's end**, so they are still present, 0 m wide, in the last lane section. Parking ends 10 m before, as planned.
- **Abrupt lane ends closed.** A cut (junction end, bridge) next to an internal width change (a street-to-avenue chain: parking 2.2 → 2.4 m) dropped the width-change piece and left parking ending at full width mid-road (`suburb`: 2 roads, continuity errors). Every run of a side lane that starts or ends inside the road now tapers over `side_taper` (`_close_runs`).
- **Drive-check fixes** (`roadgen/sim/drive_check.py`), both found on `city`:
  - **Full-width run checks the lane type.** The run before the junction and after it now stops where the lane id is not a driving lane. Before the taper of an added driving lane, its id is the bike lane's.
  - **Jumps measured against the lane's own path.** esmini moves 0.5 m along the reference line, so a lane 5 m off it on the inside of a tight corner covers only 0.31 m per step. Wider trims squeezed one such corner (curvature 0.035 1/m) into the last 9.5 m of a road. esmini's positions and roadgen's own lane centres matched to the millimetre there, yet the check flagged 3 movements. A step is now compared with the lane-centre path between its positions (onto the next road: to the first lane's end plus from the next lane's start). A new test confirms a 0.3 m gap between two roads is still caught.
- `--cross-section PROFILE` sets the **default** profile; edges with their own `Edge.cross_section` keep it. That is not "forces one profile on every edge": the `annotate` step's per-edge profiles (B5) must win.
- The sidecar's `cross_sections` is keyed by **edge id** (an edge has one profile, a chain road can have several).
- Side lanes carry no road marks yet (B4); driving lanes keep theirs, the solid line on the outermost driving lane found by role.

**Example set with `--hd`** (`stage_b_examples.sh out/stage_b/b2 --hd`, `baselines/2026-10-30-stage-b-after-b2-hd.json`): all 11 networks build, validate, and pass the drive-check (movements and whole lanes) wherever it runs. esmini simulates `city` and `mixed_city` with 0 errors.

Lane km per road km, e.g. `city`: driving 3.17, sidewalk and curb 1.48, parking 1.20, bike 0.64, shoulder 0.52, median 0.23. `grid`: driving 2.42, sidewalk and curb 2.00, parking 1.72, bike 0.42.

| | B0 | B2 with `--hd` |
|---|---|---|
| `.xodr` size | grid 2.4, manhattan_like 6.5, city 18.3, mixed_city 23.2 MB | 5.8, 19.9, 27.2, 34.3 MB |
| pipeline (incl. drive-check) | city 79 s, mixed_city 170 s | 147 s, 250 s |
| esmini load, 50 cars | city 17.3 s, mixed_city 48.1 s | 26.3 s, 68.5 s |
| esmini stepping | 87× / 64× real time | 74× / 59× real time |

Files grow 1.5–3× because every lane section repeats all of a road's lanes, and parking tapers add four sections per road.

**Open points:**
- File size and load time are the price of the side lanes. A lane section per taper is what OpenDRIVE needs, but aligning tapers (e.g. parking and shoulders ending at the same stations) could cut sections. To weigh in B9's performance check.
- The corner gaps at junctions remain until B3, as planned.

Exit of B2: full suite 604 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's `Optional[...]` style and B008; docs updated (`opendrive.md` lanes and sidecar, `validation.md` drive-check, `cli-reference.md` `--cross-section` and `plot-xodr`, `architecture.md`).

### B3 · Knock-on effects and the streetscape

- **Buildings** (`scene/buildings.py`): `road_half_extent` becomes the outer half width from the cross-section. `setback` (3 m, unchanged) is now measured from the outer edge, so buildings stand behind the sidewalks. With HD off this is identical.
- **Ground** (`scene/ground.py`): levelling reaches the outer half width. Under sidewalks the ground sits below the sidewalk top.
- **Drape** (`maps/drape.py`, with HD on only): bridge spans where the road's full width, not just its centre line, is over water.
- **Grade separation** (`geometry/grade_separation.py`): the flat top uses the lower road's outer half width.
- **Clearance validator:** a test confirms that it reads the side lanes from the `.xodr`.
- **Curbs and sidewalks are raised in the `.xodr` itself** (`<height>` on curb and sidewalk lanes): B0 showed esmini draws the curb face from it. No slabs or curb faces in the scene. *(Changed after B0: the plan had slabs as an option.)*
- **The junction corner gap is closed in the `.xodr`, not in the scene** *(changed 2026-10-30, prompt 3)*. `plans/scripts/junction_gaps.py` measured it. On the B0 spike junction, four 53.5 m² areas (one per corner) are enclosed by lanes but owned by none. On today's builder output (no side lanes, default margin) there are none, because the corner is simply off the road. So the gap appears exactly when sidewalk corner roads wrap the corner. A scene fill would hide it only in esmini; CARLA meshes the road from its lanes, and OSI describes it through lanes, so both would see a hole. The fix: each sidewalk corner road uses as its reference line **the right-turn path itself** (the turn from the outermost incoming lane of arm a to the outermost outgoing lane of arm b, the same geometry as that movement's connecting road), with a `laneOffset` of half the turn lane's width (linear when the width changes, as in `_connecting_road`). Its lanes, outward from there: `biking` where both arms have bike lanes at the junction end, `curb`, `sidewalk`, with widths blending linearly between the two arms. The curb then starts exactly at the right-turn lane's outer edge, so there is no gap. Where no such right turn exists (a one-way arm, a ramp node, a banned turn), the reference line is `turn_path` between the two carriageway-edge poses, and the remaining gap is measured and reported. The curb-return radius is therefore the right-turn lane's outer-edge radius; B1's class radius sets the room (trims) it gets.
- **`scene/streetscape.py`** (new, smaller than planned): only what esmini does not draw that is not road surface. After B0 that is nothing urgent, so it is written only if the B3 screenshots show a need. *(Changed after B0: no slabs, curb faces or sign posts; esmini raises `<height>` lanes and draws a placeholder for every sign.)*

  If written, it goes into the one `SceneGraphFile` esmini takes (`PREFIX_scene.osgt` with ground, `PREFIX_buildings.osgt` without), or alone into a flat `PREFIX_scene.osgt` with the new `--streetscape` flag.
- **Sidewalk corners** (B0: esmini draws them, the topology, continuity and drive-checks pass, and 24,060 logged vehicle positions never touched them): sidewalk-only connecting roads around each corner where both facing sides have sidewalks, along the curb return offset by half the sidewalk width, linked to both sidewalk lanes. They are recorded as sidecar `sidewalk_connections`, not as movements; the topology and continuity validators accept them.
- **Modules:**
  - new: `roadgen/scene/streetscape.py`;
  - changed: `roadgen/scene/buildings.py`, `roadgen/scene/ground.py`, `roadgen/maps/drape.py`, `roadgen/geometry/grade_separation.py`, `roadgen/opendrive/junctions.py` (corner roads), `cli.py`.
- **Tests:**
  - no building footprint overlaps a cross-section;
  - the ground lies below every sidewalk;
  - bridges cover the width;
  - `junction_gaps.py`'s check as a test: no enclosed hole larger than 0.5 m² at a 4-leg, a 3-leg and a skewed junction with sidewalks;
  - corner roads never overlap a connecting road of a movement;
  - corner roads are `sidewalk` only and are never linked from a driving lane;
  - drive-check unchanged.
- **Docs:** `docs/scene.md`, `docs/heights.md` (drape), `docs/geometry.md` (grade separation), `docs/opendrive.md` (sidewalk corners).

#### B3 result (done 2026-10-30)

- **Corner roads** close the junction corners in the `.xodr`, as planned (`junctions.py::corner_roads`, sidecar `corner_roads`):
  - **reference line:** the right-turn path, with a `laneOffset` of half the turn lane's width;
  - **lanes:** bike lane, curb and sidewalk (shoulder in rural profiles), blended between the two arms;
  - **links:** to both arms' side lanes.

  On the B0 spike junction the four 53.5 m² corner holes are gone. Changes against the plan:
  - **Smooth blends.** Widths and the lane offset blend with a smoothstep (zero slope at both ends), not linearly. A linear blend tilted the offset lanes at the joins and the continuity check reported heading mismatches up to 0.15 rad.
  - **Link rule relaxed.** A lane only one arm has (a bike lane) narrows to 0 m and has no link at that end. The continuity validator now accepts a connecting-road lane without a link where it is 0 m wide.
  - **`closes` per corner road:** whether a movement drives the turn its curb follows. Where none does, mostly between two one-way roads in the same direction, surface between the curb and the movements stays without a lane: the remaining gap the plan foresaw, measured below.
  - **Not "sidewalk-only":** a corner road carries every side lane the arms have at the junction (bike, curb, sidewalk, shoulder).
- **Knock-on** (pipeline with `--hd`; the stand-alone `drape`, `separate` and `ground` commands have no `--hd` yet):
  - **buildings** stand `setback` behind the sidewalks;
  - **ground** is levelled to the roads' outer edges;
  - **drape** makes a bridge wherever any part of the road's width is over water;
  - **grade separation** spans the overpass's flat top over the lower road's side lanes.

  All use the cross-section's trims.
- **Clearance validator: no change needed.** It never reads widths: it checks the vertical gap where two reference lines cross, which side lanes do not change. The planned test "it reads the side lanes" rested on a wrong assumption (discussion, section 2) and was dropped.
- **Repair fix** found on `mixed_city`: the fit runs after drape, and a collapse that moved a node left an edge 16 m shorter than its bridge ("bridge outside the edge"). Bridges and tunnels of the edges whose length changes now keep their share of the edge.
- **No streetscape mesh:** nothing in the screenshots needed it.

**Junction surface without a lane** (`b3_gaps.py` → `baselines/2026-10-30-stage-b-b3-gaps.json`; holes in the union of all lane polygons inside a junction's hull):

| Network | Corner roads (closing) | By a closing corner | By a non-closing corner | Slivers between turning lanes (B0 for comparison) |
|---|---|---|---|---|
| grid | 352 (352) | 0 | 0 | 32 holes, 137 m² (32, 31 m²) |
| manhattan_like | 1,440 (460) | 0 | 980, 8,322 m², max 19 m² | 390, 2,206 m² (320, 1,296 m²) |
| paris | 726 (726) | 0 | 0 | 198, 943 m² (184, 200 m²) |
| suburb | 604 (569) | 6, 8 m², max 2.5 m² | 2, 19 m² | 146, 1,201 m² |
| city | 596 (595) | 0 | 1, 23 m² | 332, 3,125 m² (228, 343 m²) |
| mixed_city | 1,131 (1,107) | 1, 1.1 m² | 5, 90 m² | 311, 2,388 m² (209, 673 m²) |
| highway_corridor | 402 (401) | 0 | 1, 25 m² | 105, 461 m² |

- **Every corner whose right turn is driven is closed,** apart from a few seams up to 2.5 m² (`suburb`, `mixed_city`).
- **Open: one-way corners.** In the one-way Manhattan grid two thirds of the corners have no turn along them, and each leaves about 8.5 m².
- **Open: slivers between turning lanes.** These were there before stage B, but wider junctions make them 2–9 times larger.

Both are junction surface between connecting roads, not corner gaps. Closing them needs a junction-surface step (a filler lane, or an outline OpenDRIVE does not have in 1.6), proposed for B8 (junction realism).

Example set with `--hd` (`out/stage_b/b3`): all networks build and validate (`mixed_city` after the repair fix). The sizes and load times are reported with the lane-section merge below, which also fixed folding corner sidewalks found then.

Exit of B3: full suite 615 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's style; docs updated (`opendrive.md` corner roads and sidecar, `validation.md` continuity, `scene.md`, `heights.md`).

### Lane-section merge (moved from B9 by prompt 5; done 2026-10-30)

**Why moved:** B2 made files 1.5–3× larger, and every later phase builds on them.

**What was asked:** align the parking and shoulder tapers' start and end points, so fewer lane sections are needed.

**What was done instead,** and why:
- **Counting the boundaries** in B2's `city` showed why alignment alone would not help. Of 2,605 sections, 1,272 boundaries came from parking alone: two per road end with parking, at the start of its 5 m taper and of its 10 m gap. Shoulders caused at most ~320, and on the grids (`grid`, `manhattan_like`) parking was everything. Aligning shoulders to parking would have removed under an eighth of `city`'s boundaries and nothing on the grids.
- **One section can hold several widths.** OpenDRIVE allows several `<width>` records per lane in one lane section. So side lanes and medians now run the whole road, 0 m wide where they are absent, like the median since B2. Their tapers, curb extensions, bridge and tunnel cuts stay inside a section as several width records (`WidthPieces`).
- **Sections now break only where a driving lane changes, or where a lane starts or ends.** A road with parking at both ends has one section instead of five.
- **Driving lanes keep one width record per section,** which the drive-check's full-width runs and the scenario's lane slots rely on.
- `metrics --hd` counts lane-km only where a lane's width records are not all zero.

**Also found and fixed on the merged examples: folding corner sidewalks.** The side lanes of a corner road lie on the inside of the right turn, towards its centre of curvature. At tight turns they reached past it, and the sidewalk's outer edge folded over itself: 141 of `city`'s 596 corner roads (24 %, `corner_folds.py`). CARLA's mesh and OSI's lane boundaries would get self-intersecting sidewalks there. Now the outermost corner lane narrows where needed, so the lanes reach at most `CORNER_REACH` (85 %) of the way to the centre of curvature. It is a cubic Hermite chain of width records through 17 stations, flat at both ends. The curb and bike lane stay at the turn lane's edge, so the corner stays closed. Folds afterwards: 0. Test: `test_corner_lanes_never_fold_inside_tight_turns`.

**Sizes** (`.xodr`, MB; B3 before the merge where it was built):

| Example | B0 | B2 | B3 | merged |
|---|---|---|---|---|
| grid | 2.4 | 5.8 | 6.8 | 4.6 |
| manhattan_like | 6.5 | 19.9 | 23.9 | 13.2 |
| paris | 5.3 | 11.6 | 13.6 | 9.2 |
| suburb | 5.6 | 9.8 | 11.5 | 8.9 |
| organic_town | 2.4 | 5.2 | 6.2 | 4.2 |
| city | 18.3 | 27.2 | 30.7 | 23.9 |
| mixed_city | 23.2 | 34.3 | — | 33.1 |
| highway_corridor | 7.5 | 12.1 | 14.5 | 11.2 |

The merge saves 23–45 % against B3 and leaves every file 3–34 % smaller than in B2, despite the corner roads added since.

**esmini load times** (`esmini_load.py`: esmini headless on a scenario without vehicles, best of 3, B2 and merged files timed back to back in one container):

| Example | B2 | merged |
|---|---|---|
| grid | 2.4 s | 3.5 s |
| manhattan_like | 13.1 s | 32.8 s |
| city | 26.2 s | 52.3 s |
| mixed_city | 68.1 s | 72.5 s (runs 72–142 s) |

- **The merge does not change load time.** The merged `city` without its corner roads loads in 25.9 s, the same as B2. esmini samples every lane along its length (OSI points, 1 m steps), whatever the sections.
- **The extra time is the corner roads' (B3).** Seven variants of the merged `city` (`esmini_load.py --variants`, `RM_Init` only: 28.7 s without corner roads, 53.1 s with) rule out curb heights, width blends and lane offset, lane links (still ≥ 40 s), the junction connections (40.5 s without; they explain about 10 s), lane count (one lane each: 43.9 s) and curve shape (straight lines: 45.6 s). Narrowing the sidewalks further did not change it either.
- **The cost per corner road is uneven:** about 44 ms on `city`, 14 ms on `manhattan_like`, 3 ms on `grid`. The cause lies in esmini's RoadManager (it always computes OSI points at load) and was not found without a profiler, which the container does not have.
- **Open, for B9's performance check** (or a report to esmini), with three options: profile esmini's load; a switch to leave corner roads out of throughput benchmarks; accept the cost. Stepping speed is not affected (B2: 74× real time on `city`; the load is a one-off per run).

**Example set** (`out/stage_b/merge`, final code): all 11 networks build and validate. The drive-check passes on every network where it runs (18 runs, movements and whole lanes). esmini simulates `city` and `mixed_city` with 0 errors. The junction gaps are unchanged by the merge (`baselines/2026-10-30-stage-b-b3-gaps.json`, the B3 table above).

**Exit:** full suite 616 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's style; docs updated (`opendrive.md`, `geometry.md`). Helper scripts: `esmini_load.py`, `corner_folds.py`.

### B4 · Road marks (DE)

- **Catalogue in `opendrive/lanes.py`** (or a new `marks.py` if it gets long). `RoadMarkParams` in the `road_marks` section. All marks are white:
  - **Centre line of two-way roads:**
    - broken, Z 340: 3 m line / 6 m gap in towns, 6 / 12 m on highways;
    - solid, Z 295, on roads with 2 + 2 lanes or more;
    - `solid_solid` with a median of 0, or the median's own edge lines.
  - **Lane lines:** broken.
  - **No-lane-change zone:** solid lane lines with `laneChange="none"` over the last `no_change_length` of each approach (street 20 m, avenue 30 m, highway 50 m). Written as a second `<roadMark sOffset>` in the last lane section(s), and also correct when that section is shorter than the zone.
  - **Edge lines:** solid between the outer driving lane and a parking, bike or shoulder lane.
  - **Other lanes:**
    - a bike lane gets a solid line on its driving side;
    - curb lanes get a `curb` mark;
    - sidewalks get none.
- **Inside junctions** (already true): the first check of the new `validate/hdmap.py` says no `<roadMark>` on connecting roads.
- **`plot-xodr`** draws the marks.
- **Modules:** `roadgen/opendrive/lanes.py`, `roadgen/validate/hdmap.py` (new), `roadgen/opendrive/plot.py`, `cli.py` (validator call).
- **Tests:**
  - marks per class and lane kind;
  - the approach zone's `sOffset` with short last sections and tapers;
  - no marks inside junctions;
  - HD off identical.
- **Docs:** `docs/opendrive.md` (Road marks), `docs/validation.md`.

#### B4 result (done 2026-10-30)

Delivered as planned: `roadgen/opendrive/marks.py` (`RoadMarkParams`, section `road_marks`, in `HdMapParams.marks`); `make_lane_sections(layout, marks)`; the new `validate/hdmap.py`, run by `pipeline` and `build-xodr` with HD on; marks in `plot-xodr`. With HD off, nothing changes (both identity guards pass). Changes against the plan, and choices it left open:

- **Own switch `road_marks.enabled`** (true): with HD on, false keeps the plain marks of before stage B. The plan wants B4 usable on its own; this also makes it removable on its own.
- **Edge line on every outermost driving lane,** also next to a curb (plan: next to parking, bike or shoulder lanes). Side lanes run the whole road, 0 m wide where absent (B3½), so "what lies outside" changes along a lane. One solid line on the carriageway edge is what the builder wrote before stage B, and it keeps the lane border visible. The bike lane's line on its driving side is this edge line; bike lanes have no mark of their own.
- **No-lane-change zones only at junctions of 3 or more roads.** A two-road junction is a road continuing (B1), and a solid stretch there would sit in the middle of a road. The zone also makes a **broken centre line** solid (Z 295 before junctions is German practice); a road shorter than its zone is solid all along.
- **`solid solid` is written with two explicit `<line>`s** (0.15 m each, 0.15 m apart, `<type>` width 0.45 m), so no reader has to guess the gap. esmini draws two lines (`out/stage_b/b4/closeups/diamond_double_line.png`). It appears only where a median lane is 0 m wide: 5 m before junctions on highways with medians (68 records in `city`).
- **Line widths** (not in the plan): 0.12 m on streets and avenues, 0.15 m on highways (RMS Schmalstrich / Autobahn); before stage B every line was 0.15 m.
- **Validator:** besides "no marks on connecting roads", it checks that each lane's mark `sOffset`s lie inside their section and ascend. The zones depend on that, and it costs nothing.
- **No centre line on streets in the `urban` and `suburban` profiles** *(user decision, prompt 7: German residential practice)*. Avenues, highways and streets in the `rural` profile (and in `none`) keep theirs. The option `road_marks.no_street_centre` (default `[urban, suburban]`; `[]` turns them back on) is read per edge, from `Edge.cross_section` or the default profile. Such a street has a `none` centre mark, also in the no-lane-change zone.
- **No `curb` mark** *(user decision, prompt 7, after an OSI check)*. The first B4 build put one on every curb lane, and esmini drew it as a white line. `b4_osi_curb.py` ran esmini 3.9 with `--osi_file` on a 200 m urban street, with and without the marks, and decoded the ground truth with the OSI bindings esmini ships (`/opt/esmini/scripts/osi3`, plus `protobuf` installed into a scratch directory, not into the project). With the mark, the curb's boundary is `TYPE_SOLID_LINE` / `COLOR_WHITE`; without it, `TYPE_NO_LINE`. Neither is `TYPE_CURB`, so the mark only adds a false painted line, and it was dropped. The curb lane and its `<height>` describe the curb. Also seen: esmini reports `curb` lanes as OSI `SUBTYPE_BORDER` (sidewalk and parking keep their subtypes).

**Marks per km of road** (`b4_marks.py` → `baselines/2026-10-30-stage-b-after-b4-hd.json`, final B4 code; lane borders and centre lines together; `none` = centre lines of streets in the urban and suburban profiles and stretches beside medians):

| Network | broken | solid | solid solid | none |
|---|---|---|---|---|
| grid | 0.21 | 2.42 | — | 0.79 |
| manhattan_like (one-way) | 0.94 | 2.58 | — | — |
| city | 0.74 | 3.12 | 0.008 | 0.55 |
| mixed_city | 0.52 | 2.83 | 0.001 | 0.57 |
| highway_corridor | 0.69 | 2.78 | 0.001 | 0.63 |

All white; 0 marks on connecting roads.

**Example set with `--hd`** (`stage_b_examples.sh out/stage_b/b4 --hd`): all 11 networks build and validate (the HD check included) and pass the drive-check wherever it runs. esmini simulates `city` and `mixed_city` with 0 errors. Repaired edges are unchanged (marks do not touch the geometry). Screenshots: `out/stage_b/b4/*.view.png`, close-ups in `out/stage_b/b4/closeups/`.

| | lane-section merge | B4 |
|---|---|---|
| `.xodr` size, grid / manhattan_like / city / mixed_city | 4.57 / 13.2 / 23.9 / 33.1 MB | 4.61 / 13.4 / 24.1 / 33.2 MB (+0.3–1.6 %) |
| pipeline (incl. drive-check), city / mixed_city | 202 / 458 s | 198 / 381 s |
| esmini load (`esmini_load.py`, back to back; first B4 build, with curb marks and street centre lines), grid / manhattan_like / city | 3.5 / 31.7 / 51.8 s | 3.5 / 34.3 / 51.5 s |
| simulate wall time, 50 cars, 30 s, city / mixed_city | 50.6 / 138 s | 49.8 / 138 s |

The load and simulate differences are within the run-to-run spread seen in B3½.

Exit of B4: full suite 639 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's `Optional[...]` / `Union` style and B008; docs updated (`opendrive.md` Road marks (HD maps), `validation.md` HD-map content, `cli-reference.md`, `architecture.md`). Helper scripts: `b4_marks.py`, `b4_osi_curb.py`.

### B5 · Junction control, right of way, signs, stop lines; `annotate`

- **`opendrive/control.py`** (new): `JunctionControlParams` (section `junction_control`) with:
  - `signal_density` 5,500 and `stop_density` 1,500 [people/km²] (calibrated in B0);
  - `stop_speed` 16.7 m/s, `stop_skew_deg` 30, `stop_share` 0.15;
  - `right_before_left` true;
  - `priorities` true;
  - presets `default`, `dense`, `rural`.

  `junction_control(node, ends, graph, params, rng) -> Control` (kind, priority road, role per approach), first match wins:

  | Junction | Control |
  |---|---|
  | two-road junction | none |
  | ramp node | none |
  | roundabout entry (`metadata["roundabouts"]`: entries, ring) | the arm yields, the ring has priority |
  | diamond terminal (`metadata["interchanges"][*]["terminals"]`) | lights if the crossing road is an avenue or more, else stop on the ramp |
  | `Node.control` set | as set |
  | both crossing roads avenue or more, or ≥ 5 legs with an avenue | lights (B6) |
  | major × minor | priority road (the two highest-ranked, roughly opposite arms; rank = class, then lanes, then speed); the minor road yields, or stops when the major speed ≥ `stop_speed`, the skew > `stop_skew_deg`, ≥ 5 legs, or the seeded `stop_share` |
  | street × street | right before left |
- **`opendrive/furniture.py`** (new): pure placement returning records (`SignalRecord`, `ObjectRecord`: road, s, t, orientation, country, type, subtype, value, unit, validity, dynamic, size, z, id). The builder turns them into scenariogeneration `Signal` / `Object`. This phase places:
  - **stop line:** a `roadMark` object (name `stopLine`, `<validity>` over the approach lanes), 1 m before the road end: an open outline of two corners (ids 0 and 1) from the inner to the outer edge of the approach lanes, and one white `<marking>` with `<cornerReference>` 0 and 1, `width` 0.5 m and `spaceLength` 0 (solid). *(Changed after B0: no DE 294 signal. esmini has no OSI type for it, and CARLA does not know 294. A marking with corner references is what esmini draws as paint; "side" markings need a `length`/`width` box, and an outline alone is drawn as a grey volume.)*
  - **yield line:** the same object with name `yieldLine` and a broken marking (Z 341: 0.5 m dashes, 0.25 m gaps, 0.3 m wide);
  - **signs:** 205 / 206 on the right roadside (t = carriageway edge + 0.5 m); 306 after the junction on the major road, 301 before priority junctions without 306; 215 at roundabout entries.
  - Ids are unique per file, numbered like the bridge / tunnel objects.
- **Right of way:**
  - `junctions.py::conflicts(movements, paths)`: pairs of movements whose turn paths cross or end in the same lane.
  - `<junction><priority high low>` for each conflicting pair whose approaches have different priority, plus left turns against the oncoming straight and right turns.
  - Sidecar: per movement `control`, `priority`, `yields_to`; a `junctions` list with the control kind, priority roads and approach roles.
  - `<priority>` count and file size are measured on the examples. If the `.xodr` grows by more than 30 %, `priorities` defaults to false and only the sidecar keeps the conflicts.
- **`annotate` step** (`roadgen/maps/annotate.py`, new; CLI command `annotate`; run by `pipeline` with maps when HD is on, after `separate`). `AnnotateParams`: `urban_density` 3,000, `suburban_density` 1,500 [people/km²] (B0), plus the style map's suburb zone. It sets:
  - `Edge.cross_section`: urban / suburban / rural by population density at the edge's midpoint;
  - `Node.control`: lights where density ≥ `signal_density` and the major road is an avenue or more.

  Not run inside `generate`.
- **Validation** (`validate/hdmap.py`):
  - every signal and object within its road's length and outer edge (signs: + 2 m roadside), validity lanes exist at s;
  - ids unique;
  - every controlled approach has a stop or yield line and a facing sign within 5 m of the road end;
  - priority records reference their own junction's connecting roads.
- **`plot-xodr`:** a control glyph per junction, sign markers, stop lines.
- **Modules:**
  - new: `roadgen/opendrive/control.py`, `roadgen/opendrive/furniture.py`, `roadgen/maps/annotate.py`;
  - changed: `roadgen/opendrive/junctions.py`, `roadgen/opendrive/builder.py`, `roadgen/validate/hdmap.py`, `roadgen/opendrive/plot.py`, `cli.py`, `roadgen/defaults.yaml`, `roadgen/config.py`.
- **Tests:**
  - each rule on synthetic junctions;
  - roundabout entries yield and the ring has priority;
  - diamond terminals;
  - `Node.control` overrides the rules;
  - `annotate` on a hand-made density map;
  - conflicts are symmetric and complete on a 4-leg 2+2 junction;
  - every DE code used is in esmini's `de_traffic_signals.txt` (a frozen copy of the codes used), and the encoding rule below holds for each;
  - esmini loads the examples without new warnings;
  - HD off identical.
- **Docs:**
  - `docs/opendrive.md` (Junction control and right of way, signals and signs);
  - `docs/maps.md` (`annotate`);
  - `docs/cli-reference.md`;
  - `docs/validation.md`.

#### B5 result (done 2026-10-30)

Delivered as planned: `opendrive/control.py` (`JunctionControlParams`, section `junction_control`, presets `default`, `dense`, `rural`; the rule table; conflicts and priority pairs), `opendrive/furniture.py` (pure placement of lines and signs), `maps/annotate.py` with the `annotate` command and pipeline step, the builder's `<priority>` records, signals and line objects, sidecar `junctions` and `movement_control`, the new validator checks, and lines, signs and control glyphs in `plot-xodr`. With HD off nothing changes (both identity guards pass). Changes against the plan, and choices it left open:

- **Sign 301 only, no 306 / 307.** The plan has 306 after the junction on the priority road and 301 where no 306 stands. A 306 holds until a 307 cancels it, so it needs the priority road traced across junctions, including where it stops being one. Sign 301 regulates exactly the next junction, so one per major approach is legally complete per junction. It stands 30 m before the junction (at most half the road), not on roundabout rings. 306 / 307 can follow when needed.
- **215 stacked above 205** on the same post (`zOffset` 0.7 m higher), not as a separate sign.
- **Signal approaches get a stop line now; the facing-light check waits for B6.** The validator requires a 205 / 206 sign only on yield and stop approaches.
- **The sidecar keeps the movement records unchanged.** The per-movement `control`, `priority` and `yields_to` go into a separate `movement_control` map keyed by connecting road, so the HD-off sidecar stays byte-identical. `junctions` also counts each junction's conflicts and priority records.
- **`annotate` takes the two densities as numbers** (`junction_control.signal_density`, `stop_density`), so `roadgen/maps` does not import the OpenDRIVE package. It runs after grade separation and before the cross-section fit (the fit must see the profiles). Drape and grade separation still size bridges and overpasses with the default `urban` profile, the widest one, so they stay wide enough. In style-map suburb districts an edge is at most `suburban`.
- **Lines follow the cross slope** (found in the close-ups). A stop line across a banked road end (superelevation up to 0.04 rad where a road meets a sloped junction plane) sank half into the surface in esmini. The two outline corners now get heights `v · tan(superelevation)`.
- **Signs stand beside the road at their own station** (found by the new validator on the three map networks): the first version took the carriageway edge at the road end, where a road can be wider (a lane added before the junction), and put some 301 signs 30 m back up to 4.5 m off the road.
- **Ids:** signals 1, 2, …; line objects from (C + 1) · 1000 on (C chain roads), above every bridge and tunnel id (road id · 1000 + k).
- **User decisions on this result (prompt 8):** right before left is required together with yield and stop only on `mixed_city` (exit criteria changed); 301 alone is fine for now, and B7 adds 306 / 307 along priority roads if they can be traced through consecutive junctions with the chain data at reasonable cost (else an open item); `city`'s missing street × street junctions go to stage D (see "Open items for later stages").
- **`<priority>` records stay on** (`priorities: true`): they take 0.6–2.4 % of the files, far below the plan's 30 % switch-off rule.

**Control on the examples** (`b5_control.py` → `baselines/2026-10-30-stage-b-after-b5-hd.json`; junctions of 3+ roads):

| Network | junctions | priority | signal | right before left | roundabout | none | signs 205 / 206 / 215 / 301 | stop / yield lines | `<priority>` (share of file) |
|---|---|---|---|---|---|---|---|---|---|
| grid | 96 | 96 | — | — | — | — | 121 / 39 / — / 192 | 39 / 121 | 2,384 (2.4 %) |
| manhattan_like | 380 | 360 | 20 | — | — | — | 268 / 62 / — / 420 | 112 / 268 | 3,200 (1.1 %) |
| paris | 226 | 217 | 4 | 5 | — | — | 220 / 77 / — / 434 | 96 / 220 | 3,646 (1.8 %) |
| suburb | 228 | 178 | 1 | 15 | 34 | — | 222 / 63 / 35 / 355 | 67 / 222 | 3,185 (1.7 %) |
| organic_town | 120 | 103 | 1 | — | 16 | — | 114 / 18 / 16 / 206 | 23 / 114 | 1,125 (1.2 %) |
| city (maps) | 339 | 188 | 151 | — | — | — | 121 / 109 / — / 376 | 609 / 121 | 3,062 (0.6 %) |
| mixed_city (maps) | 420 | 267 | 118 | 16 | 19 | — | 243 / 143 / 20 / 533 | 564 / 243 | 5,395 (0.8 %) |
| highway_corridor (maps) | 137 | 102 | 33 | — | — | 2 | 92 / 40 / — / 204 | 148 / 92 | 1,596 (0.7 %) |

- **Annotate** set profiles on `city` (urban 443, suburban 233, rural 104 edges), with 84 junctions to lights and 41 to stop. `mixed_city`: 534 / 323 / 106 edges, 94 lights, 36 stop.
- **Lights at avenue × street junctions:** 46 of 192 in `city` (24 %) and 69 of 316 in `mixed_city` (22 %), as calibrated in B0 (target about a quarter). Every junction where the crossing road is an avenue or more has lights, by the rule.
- **Yield and stop** are both present everywhere. **Right before left** needs a junction of streets only: `mixed_city` has 16 and `suburb` 15, but **`city` has none**. All of `city`'s junctions touch an avenue or a highway, so the exit criterion "right-before-left present in `city`" cannot hold with the planned rule (open point below).

**Example set with `--hd`** (`stage_b_examples.sh out/stage_b/b5 --hd`): all 11 networks build and pass every validator (the new HD checks included) and the drive-check wherever it runs. esmini simulates `city` and `mixed_city` with 0 errors. The only warnings are esmini's run-time "no connecting lane" lane-following messages (3 and 4), the same kind as in B4 (1 and 3); none concerns signals, objects or priorities. Repaired edges are unchanged. Close-ups: `out/stage_b/b5/closeups/` (a stop line and 206 in `city`, a roundabout yield line with 205 / 215, the stop lines at a diamond terminal).

| | B4 | B5 |
|---|---|---|
| `.xodr` size, grid / manhattan_like / city / mixed_city | 4.61 / 13.4 / 24.1 / 33.2 MB | 4.96 / 14.2 / 24.5 / 33.7 MB (+1.0–7.6 %) |
| pipeline (incl. drive-check), city / mixed_city | 198 / 381 s | 197 / 447 s |
| esmini load (`esmini_load.py`, back to back; first B5 build), grid / manhattan_like / city | 3.5 / 33.8 / 49.9 s | 3.5 / 35.2 / 48.1 s |
| simulate wall time, 50 cars, 30 s, city / mixed_city | 49.8 / 138 s | 48.7 / 144 s |

The differences in time are within the run-to-run spread (B4's `mixed_city` pipeline varied between 381 and 453 s).

Exit of B5: full suite 676 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's `Optional[...]` / `Union` style and B008; docs updated (`opendrive.md` junction control and right of way, sidecar keys; `maps.md` annotate; `cli-reference.md`; `validation.md`; `architecture.md`). Helper script: `b5_control.py`. Test data: `tests/data/esmini_de_signs.txt` (esmini's table lines for the codes used).

### B6 · Traffic lights, controllers, phase plans, OpenSCENARIO

- **`opendrive/lights.py`** (new), `TrafficLightParams` (section `traffic_lights`):
  - `min_green` 10 s;
  - yellow from ITE `1.0 + v / (2 · 3.0)` with v in m/s, at least 3 s (DE 3 s at 50 km/h);
  - `all_red` 2 s;
  - cycle within [60, 90] s;
  - `overhead_min_lanes` 3;
  - offsets seeded per junction.
- **Lights:** a 1000001 light per approach on the right roadside at the stop line, plus one over the lanes (`zOffset` 5.5 m) on approaches with `overhead_min_lanes` or more.
- **Phase plan:**
  - approaches are grouped by opposite pairs (heading difference within 180 ± 30°); leftover arms get their own phase;
  - permissive lefts (protected ones once B8 adds pockets);
  - green split by lane count;
  - sidecar `signal_plans`: junction, controller, cycle, offset, phases as `{duration, states: {signal id: "off;off;on"}}`.
- **OpenDRIVE:** top-level `<controller>` with `<control signalId>` per light; `<junction><controller>` for every junction with lights.
- **OpenSCENARIO** (`scenario/xosc_builder.py`): reads `signal_plans` and writes the plans in two forms:
  - a `TrafficSignalController` per junction in the `RoadNetwork` (phases with `TrafficSignalState`, the offset as `delay`). This is the standard form for other tools; **esmini 3.9 ignores it** (B0);
  - **storyboard events, always** (B0: the only form esmini switches): one event per phase change with a `TrafficSignalStateAction` per light (`name` = signal id, states `"on;off;off"` red, `"off;on;off"` yellow, `"off;off;on"` green) and a `SimulationTimeCondition`, generated up to the scenario's duration, in one act. *(Changed after B0: the plan had these as a fallback.)*
  - `scenario --no-lights` leaves them out.
  - Vehicles still ignore lights (esmini's default controller); the docs say so.
- **Validation:**
  - every light is in exactly one controller;
  - every junction with lights has a controller;
  - every plan covers its lights;
  - no two conflicting movements are green in one phase (permissive lefts excepted);
  - every phase lasts at least the minimum green.
- **Check in esmini:** `city` with 50 cars for 120 s; the light states change, shown by `view --screenshot`-style frames of one light at two phase times, as in B0. (OSI is not decoded: the dev container has no `osi3` package.)
- **Modules:**
  - new: `roadgen/opendrive/lights.py`;
  - changed: `roadgen/opendrive/builder.py`, `roadgen/scenario/xosc_builder.py`, `roadgen/validate/hdmap.py`, `roadgen/opendrive/plot.py`, `cli.py`.
- **Tests:**
  - phase grouping for a T, a 4-leg, a 5-leg and a skewed junction;
  - timings;
  - controller records in the `.xodr`;
  - controllers in the `.xosc`;
  - an esmini run where a light changes state (marked slow like the other esmini tests).
- **Docs:** `docs/opendrive.md` (lights and controllers), `docs/scenarios-and-simulation.md` (lights in the `.xosc`, what esmini does with them).

#### B6 result (done 2026-10-30)

Delivered as planned:
- `opendrive/lights.py`: `TrafficLightParams` (section `traffic_lights`), phase groups, fixed-time plans, state changes;
- lights from `furniture.py`: OpenDRIVE 1000001 on the roadside at the stop line, plus one over the lanes on approaches with 3 or more lanes;
- a top-level `<controller>` per junction with lights, and a `<junction><controller>` naming it;
- sidecar `signal_plans`;
- in `scenario`: a RoadNetwork `TrafficSignalController` per junction and storyboard events (`--no-lights` leaves both out);
- validator checks, light markers in `plot-xodr`.

With HD off nothing changes (both identity guards pass). Changes against the plan, and choices it left open:

- **Opposite arms share a phase only if their movements are compatible** (found by the new validator on `paris`, `city`, `mixed_city`). At skewed and Y-shaped junctions, two arms within 180 ± 30° can have crossing left turns (41° and 97°) or crossing straight movements. The B5 rule (a left turn yields to oncoming straight and right turns) gives them no priority record, so one phase would show both green. Approaches with a non-permissive conflict now get separate phases (`clash` in `phase_groups`).
- **Timing details the plan left open:**
  - cycle 60 s for two phases, plus 15 s per further phase, within [60, 90] s;
  - greens to 0.1 s, the group with the most lanes taking the rounding rest, so the cycle hits its target;
  - `min_green` may stretch the cycle past 90 s (not on the examples);
  - yellow 3.3 s at 13.9 m/s, 3.8 s at 16.7 m/s;
  - offsets are whole seconds.
- **Plan records:** each group gives three phases in the sidecar (`kind` green / yellow / all_red, with `group`, `duration`, `states`). The plan also carries `min_green` and the junction's `conflicts` and `permissive` pairs, so the validator can check it without the geometry.
- **One controller per junction** (`type="0"`, `sequence="0"`), between the last road and the first junction (OpenDRIVE 1.6 element order).
- **`delay` of the `TrafficSignalController`** = cycle − offset, where the cycle starts. The storyboard's first event, at 0 s, sets every light.
- **Two scenariogeneration quirks:**
  - **Upper-cased country:** it writes `country="OPENDRIVE"`; the builder restores `OpenDRIVE`.
  - **Silent renumbering of ids:** it keeps the signal and object ids it has seen for the whole process, and renumbers any repeat (with a `print`). One CLI build was unaffected. A second build in the same process (tests, sweeps) got shifted ids, so controllers named the wrong signals, and B5's line objects were renumbered too, unnoticed until now. The builder now resets that registry before numbering; test `test_ids_stay_the_same_when_a_process_builds_twice`.
- **Light heads:** 0.8 × 0.3 m, 3 m high on the roadside and 5.5 m over the lanes. esmini logs "1000001 ... no OSI type defined" at info level. Lights are OSI traffic lights, not signs (B0).

**Lights on the examples** (`b6_lights.py` → `baselines/2026-10-30-stage-b-after-b6-hd.json`):

| Network | junctions with lights | lights (overhead) | phase groups 1 / 2 / 3 / 4 / 5 | cycle min / median / max [s] | `.xodr` growth over B5 |
|---|---|---|---|---|---|
| manhattan_like | 20 | 60 (10) | 10 / 10 / – / – / – | 60 / 60 / 60 | +0.17 % |
| paris | 4 | 19 | – / – / 2 / 2 / – | 75 / 82.5 / 90 | +0.07 % |
| suburb, organic_town | 1 each | 4, 5 | three phases | 75 | +0.02 %, +0.04 % |
| city (maps) | 151 | 500 | – / 133 / 13 / 4 / 1 | 60 / 60 / 90 | +0.80 % |
| mixed_city (maps) | 118 | 421 | – / 93 / 22 / 3 / – | 60 / 60 / 90 | +0.48 % |
| highway_corridor (maps) | 33 | 108 | – / 32 / 1 / – / – | 60 / 60 / 75 | +0.37 % |
| junction_diamond | 2 | 6 | – / 2 / – / – / – | 60 | +1.4 % |

`grid`, `junction_k` and `junction_roundabout` have no lights. The `city` scenario of the example set (50 cars, 30 s) has 151 controllers and 613 light events with 1,220 actions; it is 1.53 MB.

**esmini check** (`b6_light_check.py`, `city`, 50 cars, 120 s): 0 errors, load 55.3 s, stepping 65.5× real time. Light 1 at junction 608 is red at 5.7 s and green at 48 s: `out/stage_b/b6/closeups/city_light_1_5.7s_red.png`, `..._48s_green.png`; another light in the first frame shows yellow. Vehicles do not stop at red (esmini's default controller), as documented.

**Open point: single-phase plans.** 10 of `manhattan_like`'s 20 junctions with lights have one phase group. Only one opposite pair arrives there, and the third arm is an outgoing one-way avenue. The lights then show green except for a 5.3 s yellow and all-red per cycle, with nothing to give way to. They become the pedestrian phase once B7 adds crosswalks; otherwise such junctions could drop their lights. **User decision (prompt 9):** both. In B7, a single-phase junction with crosswalks keeps its lights, and the extra phase becomes the pedestrian phase; one without crosswalks is demoted from lights to priority control.

**Example set with `--hd`** (`stage_b_examples.sh out/stage_b/b6 --hd`): all 11 networks build and pass every validator (the light checks included) and the drive-check wherever it runs. esmini simulates `city` and `mixed_city` with 0 errors and the same run-time warnings as in B5 (3 and 4 "no connecting lane"). Repaired edges are unchanged.

| | B5 | B6 |
|---|---|---|
| `.xodr` size, manhattan_like / city / mixed_city | 14.16 / 24.49 / 33.73 MB | 14.18 / 24.69 / 33.89 MB |
| pipeline (incl. drive-check), city / mixed_city | 197 / 447 s | 197 / 444 s |
| esmini load (`esmini_load.py`, back to back), manhattan_like / city | 35.4 / 47.9 s | 35.0 / 40.9 s |
| simulate wall time, 50 cars, 30 s, city / mixed_city | 48.7 / 144 s | 47.7 / 132 s |

The load and simulate differences are within the run-to-run spread.

Exit of B6: full suite 693 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's `Optional[...]` / `Union` style and B008; docs updated (`opendrive.md` traffic lights and controllers, sidecar `signal_plans`; `scenarios-and-simulation.md` traffic lights; `validation.md`; `cli-reference.md`; `architecture.md`). Helper scripts: `b6_lights.py`, `b6_light_check.py`.

### B7 · Crosswalks, speed and curve signs

- **Crosswalks:**
  - `<object type="crosswalk">` with an open `cornerLocal` outline of two corners (ids 0, 1) from curb to curb, and a white `<marking>` with `<cornerReference>` 0 and 1, `width` 3 m (street) / 4 m (avenue). B0 showed esmini draws this as a zebra across the road;
  - zebra stripes (Z 293: `lineLength` 0.5, `spaceLength` 0.5) and sign 350-10 / 350-20 at junctions without lights; at lights, two solid markings 0.3 m wide along the crosswalk's edges (Fußgängerfurt) instead;
  - at the junction border of every arm with sidewalks on both sides;
  - the stop line moves 1 m behind the crosswalk;
  - skipped where the approach is shorter than crosswalk + stop line + 2 m.
- **Single-phase light junctions** *(added by prompt 9)*: where a junction with lights has one phase group (B6), it keeps its lights if it gets crosswalks, with a pedestrian phase (all vehicle lights red while pedestrians cross) as its second phase; without crosswalks it is demoted from lights to priority control.
- **306 / 307 along priority roads** *(added by prompt 8)*: where the priority road can be traced through consecutive priority junctions with the existing chain data (`junctions` records: priority roads per junction, roads shared by neighbouring junctions) at reasonable cost, 306 after the first junction of the run and 307 before it ends, replacing the 301s inside the run. Otherwise this stays an open item.
- **Speed signs:**
  - 274 (value in km/h, rounded down to 10) at every change of the `<type>` speed in its travel direction;
  - also after each junction on roads longer than 100 m.
- **Curve warnings:**
  - 103-10 / 103-20 (left / right), or 105 for two advisory corners within 200 m;
  - placed the advisory deceleration lead before the corner (`profiles.ADVISORY_DECEL`);
  - plus a 274 where the advisory speed is at least 10 km/h below the limit.
- **`plot-xodr`** draws crosswalks and signs.
- **Modules:** `roadgen/opendrive/furniture.py`, `roadgen/opendrive/builder.py`, `roadgen/opendrive/plot.py`, `roadgen/validate/hdmap.py`.
- **Tests:**
  - crosswalks lie within the road and span exactly the carriageway;
  - stop lines sit behind them;
  - speed signs match the `<type>` records;
  - a curve warning precedes every advisory corner;
  - esmini loads the examples.
- **Docs:** `docs/opendrive.md` (objects and signs).

#### B7 result (done 2026-10-30)

Delivered as planned:
- crosswalks (`<object type="crosswalk">`): zebras with 350-10 at junctions without lights, two edge lines at lights;
- the stop or yield line 1 m behind the crosswalk;
- speed limit signs 274 and curve warnings 103 / 105 (`furniture.py`);
- crosswalks and the new signs in `plot-xodr`.

Both items added by the user are done too:
- **306 / 307 along priority roads** (prompt 8) proved cheap to trace with the junction records: `control.priority_runs`;
- **single-phase light junctions** (prompt 9).

With HD off nothing changes (both identity guards pass). Changes against the plan, and choices it left open:

- **Single-phase light junctions are demoted in the builder, not in `annotate`.** `annotate` runs only with maps, and phase groups need the movement geometry, which only the builder has. The case at hand, `manhattan_like`, has no maps. The effect is the one decided:
  - with crosswalks, the lights stay and get a pedestrian phase: every vehicle light red, long enough to cross the longest crosswalk at `walk_speed` 1.2 m/s, at least `min_green`;
  - without crosswalks, the junction becomes a priority junction, with the opposite pair as the priority road.
  On the examples all ten such junctions (`manhattan_like`) have crosswalks and got a pedestrian phase. Pedestrian lights themselves are not placed.
- **306 / 307:** traced per direction of travel along each priority junction's two priority-road arms, for runs through 2 or more junctions:
  - 306 stands 10 m after the first junction (once, not repeated after every junction);
  - 307 stands 30 m before the junction where the run ends, if it ends at one rather than at a dead end;
  - the 301s at the junctions inside the run are dropped;
  - a run that closes into a loop is cut where it closes.
  A run through one junction keeps its 301.
- **Crosswalk details:**
  - placed 0.5 m before the road end, 3 m deep on streets, 4 m on avenues and highways;
  - only at junctions with control (not at two-road junctions or ramp nodes);
  - also on outgoing one-way arms;
  - 350-10 faces each direction of travel. The plan's 350-20 is the left-side variant, which right-hand traffic needs only on one-way roads; not used.
  - The room rule ("crosswalk + stop line + 2 m") applies to half the road where both its ends are junctions.
- **The validator's approach reach grew from 5 to 8 m:** the stop line now stands up to 5.75 m before the road end, behind a 4 m crosswalk.
- **Speed signs:**
  - km/h to the nearest km/h, then down to 10, because limits are stored rounded (22.2 m/s for 80 km/h would otherwise be posted as 70);
  - "an advisory speed at least 10 km/h below the limit" compares the unrounded speeds;
  - the sign after a junction is left out within 20 m of a change sign;
  - no along-road signs on roundabout rings and cul-de-sac loops.
- **Curve warnings:** at the deceleration lead, at least 10 m before the corner, always 0.7 m above a 274 position (on its post).
- **Line and crosswalk corners now lie on the road surface** (found in the close-ups). esmini puts each outline corner at the object's height plus its `z`. A crossing at lights on an 8 % grade had one edge line buried and the other z-fighting, whatever single slope term was used. Four variants were rendered on that crossing (cross slope only, ± grade, none). Now each corner's `z` is the exact surface under it (elevation and superelevation at s + u) relative to the object's, plus 2 cm (`MARK_LIFT`). This replaces B5's roll-only formula for stop lines.
- **esmini logs at info level that it has no OSI type for 350.10, 103.x and 105.x,** the B0 limitation (its table spells them "350-10"). It draws them as placeholder boxes; 274-30 / -50 have models.

**On the examples** (`b7_signs.py` → `baselines/2026-10-30-stage-b-after-b7-hd.json`):

| Network | crosswalks (at lights) | share of controlled arms | 274 per km | curve warnings / advisory corners | 301 / 306 / 307 | `.xodr` growth over B6 |
|---|---|---|---|---|---|---|
| grid | 352 (0) | 100 % | 7.1 | 8 / 4 | 16 / 16 / 16 | +10.2 % |
| manhattan_like | 1,440 (60) | 100 % | 3.9 | 8 / 4 | 14 / 14 / 14 | +11.6 % |
| paris | 751 (17) | 98 % | 7.0 | 42 / 27 | 122 / 100 / 82 | +11.2 % |
| suburb | 603 (5) | 80 % | 6.1 | 100 / 93 | 107 / 74 / 56 | +9.3 % |
| organic_town | 321 (4) | 86 % | 8.3 | 20 / 27 | 40 / 34 / 27 | +10.5 % |
| city (maps) | 643 (251) | 58 % | 9.5 | 190 / 117 | 156 / 82 / 66 | +4.5 % |
| mixed_city (maps) | 1,079 (309) | 76 % | 8.8 | 200 / 131 | 179 / 122 / 94 | +5.0 % |
| highway_corridor (maps) | 369 (101) | 83 % | 8.9 | 90 / 58 | 66 / 58 / 43 | +5.4 % |

- **Curve warnings** count both directions of travel; advisory corners (sidecar `corners`) count once. Corners on rings and loops get none (`organic_town`, the junction presets).
- **Crosswalks are fewer on the map networks,** where rural and highway edges (`annotate`) have no sidewalks.
- **Junctions with lights** are unchanged, since none was demoted. `manhattan_like`'s ten single-phase junctions got a pedestrian phase.

**Example set with `--hd`** (`stage_b_examples.sh out/stage_b/b7 --hd`): all 11 networks build and pass every validator and the drive-check wherever it runs. esmini simulates `city` and `mixed_city` with 0 errors and the same run-time warnings as before (3 and 4 "no connecting lane"). Repaired edges are unchanged. Close-ups (`out/stage_b/b7/closeups/`): a zebra, a crossing at lights on an 8 % grade (both edge lines and the stop line behind them drawn), a 274-30 sign.

| | B6 | B7 |
|---|---|---|
| `.xodr` size, manhattan_like / city / mixed_city | 14.18 / 24.69 / 33.89 MB | 15.83 / 25.79 / 35.59 MB |
| pipeline (incl. drive-check), city / mixed_city | 197 / 444 s | 197 / 451 s |
| esmini load (`esmini_load.py`, back to back), manhattan_like / city | 34.9 / 36.9 s | 34.7 / 48.8 s |
| simulate wall time, 50 cars, 30 s, city / mixed_city | 47.7 / 132 s | 48.9 / 132 s |

`city`'s load time varies between runs: the same B6 file measured 36.9, 40.9 and 47.7 s on three runs, and B7's previous run 48.3 s against B6's 47.7 s. So no load cost of B7 can be separated from the spread; B9's performance check measures it properly.

Exit of B7: full suite 704 passed, 1 failed (the known `test_monitor_memory_cap_and_peak`); identity guards pass; ruff clean on touched files apart from the project's `Optional[...]` / `Union` style and B008; docs updated (`opendrive.md` crosswalks, priority roads, along-road signs, pedestrian phase, corner heights; `validation.md` approach reach). Helper script: `b7_signs.py`.

#### Zebra rule (prompt 10, 2026-10-31)

**User decision:** zebras are rare at German junctions without lights. Crossings stay at every arm with lights. Without lights, zebras go only on avenue arms in dense areas, and none on street × street junctions; it is an option.

**Implementation:** `junction_control.zebras`:
- `dense_avenues` (default): a zebra only on avenue arms in the `urban` profile. "Dense" reuses B5's population threshold through the profile: on maps `annotate` sets `urban` from `urban_density` (3,000 people/km²); without maps every edge has the default profile `urban`, which gives "avenues only". Streets never get one, so street × street junctions have none.
- `all`: the B7 behaviour.
- `none`: no zebras.

Junctions with lights keep a crossing on every arm with sidewalks, so the single-phase rule (prompt 9) is unchanged.

**Crosswalk counts** (`b7_signs.py out/stage_b/b7 out/stage_b/b7z` → `baselines/2026-10-31-stage-b-b7-zebras.json`):

| Network | crosswalks B7 → now | zebras B7 → now | at lights | 350-10 signs B7 → now | `.xodr` size |
|---|---|---|---|---|---|
| grid | 352 → 176 | 352 → 176 | 0 | 704 → 352 | 5.46 → 5.22 MB |
| manhattan_like | 1,440 → 780 | 1,380 → 720 | 60 | 1,500 → 840 | 15.83 → 15.09 MB |
| paris | 751 → 363 | 734 → 346 | 17 | 1,468 → 692 | 11.03 → 10.49 MB |
| suburb | 603 → 288 | 598 → 283 | 5 | 1,194 → 564 | 10.44 → 10.00 MB |
| organic_town | 321 → 173 | 317 → 169 | 4 | 634 → 338 | 4.96 → 4.75 MB |
| city (maps) | 643 → 348 | 392 → 97 | 251 | 784 → 194 | 25.79 → 25.37 MB |
| mixed_city (maps) | 1,079 → 483 | 770 → 174 | 309 | 1,540 → 348 | 35.59 → 34.74 MB |
| highway_corridor (maps) | 369 → 194 | 268 → 93 | 101 | 536 → 186 | 11.97 → 11.72 MB |
| junction_k / roundabout / diamond | 8 / 7 / 4 → 2 / 2 / 4 | 8 / 7 / 0 → 2 / 2 / 0 | 0 / 0 / 4 | 16 / 13 / 0 → 4 / 4 / 0 | |

- On the networks without maps the zebras halve: only avenue arms keep theirs.
- On the map networks a quarter remain: only avenues in `urban` areas.
- `manhattan_like` keeps its ten pedestrian phases: their junctions have lights.

All 11 examples build, validate and pass the drive-check (`out/stage_b/b7z`). Suite: 709 passed, 1 failed (the known one). Docs: `opendrive.md` (crosswalks).

### B8 · Junction realism: lane use, turn pockets, medians, banned turns

- **Lane-use solver** (`opendrive/junctions.py`):
  - `lane_use(end, exits, pockets, bans) -> dict[lane, frozenset[MovementKind]]` writes today's rules down explicitly: right from the rightmost lane, left from the leftmost, straight lane to lane, `fill_pair`, lane splits.
  - `plan_movements` generates the movements from it; without pockets and bans its output is identical (test).
  - Sidecar `lane_use` per approach.
  - **Arrows:** DE Z 297 `roadMark` objects, 20 m and 40 m before the stop line in each lane (and as signals if esmini's table has 297).
- **Turn pockets** (`TurnLaneParams`, section `turn_lanes`):
  - left pockets on, right pockets off;
  - `pocket_length` street 25 / avenue 40 / highway 60 m (storage + deceleration, AASHTO);
  - `pocket_taper` 15 m (RASt).
  - **Where:** on approaches with a left exit at junctions with lights or priority control, where the edge leaves room for the pocket, its taper, the no-lane-change zone and the far junction.
  - **Decided from the graph before the trims,** and the trims count the pocket's width, so there is no circular dependency.
  - **Geometry:** in `lane_layout` an innermost lane appears over the last `pocket_length` with a taper. With a median it takes the median's width (the median tapers into it); without one, the other lanes shift outward.
  - The pocket is left-only (and U-turn where allowed). B6's phase plans get a protected left phase where pockets exist.
- **Medians at junctions:** the median ends in a pocket or a taper before every junction; roundabout entries keep no median (splitter islands are left for later).
- **Banned turns** (`junction_control.ban_left_highway` true, `ban_share` 0.05):
  - **Right-in / right-out** where a minor road meets an at-grade highway with 2 + 2 lanes or more: the minor approach keeps right turns only, and the highway's left turn into the minor road is banned.
  - **Seeded bans:** left turns at a `ban_share` of other junctions with lights or priority control.
  - **Connectivity:** a ban is applied only if the lane-level movement graph stays strongly connected, checked with the connectivity validator at lane level.
  - **Signs:** 209-20 (right only) or 214 (straight and right) facing the approach.
  - Sidecar `banned`.
- **Validation:**
  - lane use matches the movements;
  - pockets are left-only;
  - every ban is signed;
  - the lane graph is strongly connected;
  - right-turn paths stay inside the curb returns.
- **Modules:** `roadgen/opendrive/junctions.py`, `roadgen/geometry/profiles.py`, `roadgen/geometry/cross_section.py`, `roadgen/opendrive/lights.py`, `roadgen/opendrive/furniture.py`, `roadgen/validate/hdmap.py`, `roadgen/validate/connectivity.py`, `roadgen/opendrive/plot.py`.
- **Tests:**
  - `lane_use` reproduces today's movements;
  - pocket geometry, ids and taper;
  - drive-check through a pocket (esmini's selector turns left out of it);
  - median and pocket together;
  - protected-left phases;
  - bans keep connectivity and are signed;
  - `explicit_routes` scenarios never use a banned movement.
- **Docs:** `docs/opendrive.md` (Movements: lane use, pockets, bans), `docs/scenarios-and-simulation.md` (routes).

### B9 · Validation and metrics completed

- **`validate/hdmap.py`** completed and run by `build_and_validate` whenever HD is on:
  - lane types consistent across every lane link (between lane sections and between roads);
  - connecting roads of movements are driving only;
  - no junction connection starts or ends in a non-driving lane;
  - every check from B4–B8.
- **`metrics --hd`** completed. `metrics --sweep 200` builds with HD on and counts failures for `city` and `mixed_city` (target 0).
- **Performance against B0:** build and validation time, `.xodr` size, esmini load time and real-time factor on `city` and `mixed_city`. *(The lane-section merge planned here was moved before B4, see its result; the corner roads' esmini load cost found there is open for this phase.)*
- **Modules:** `roadgen/validate/hdmap.py`, `roadgen/metrics/hdmap.py`, `roadgen/metrics/sweep.py`, `cli.py`.
- **Tests:** each validator check on a broken fixture (a sign outside the road, a missing stop line, a sidewalk linked to a driving lane, conflicting greens).
- **Docs:** `docs/validation.md`, `docs/cli-reference.md`.

### B10 · Default flip, docs, examples, final measurement

- **Flip:** `hdmap.enabled: true` in `defaults.yaml`; `HDMAP_VERSION = 1` in `roadgen/opendrive/__init__.py`, written to the sidecar (and `0` / absent with HD off); `--no-hd` gives today's output.
- **Tests:** those that assume driving-only default builds set `hdmap.enabled=false` explicitly or are updated. The builder identity guard runs with HD off. The lsystem identity test is unaffected.
- **History:** documented in `docs/cli-reference.md#run-folders`:
  - runs recorded before the flip give HD output when replayed;
  - a new run of the same command replaces its old folder (same spec, same hash);
  - `--set hdmap.enabled=false` (or `--no-hd`) reproduces the old output.

  No migration of old `run.yaml` files.
- **Docs:**
  - every page touched above;
  - `docs/README.md` index;
  - `docs/architecture.md` module map and output files;
  - `.github/copilot-instructions.md` repo layout;
  - `docs/development.md#project-history`.
- **Examples:** a stage B section in `runs/examples.sh`, with `view` commands in `runs/README.md`.
- **Final measurement:** `plans/baselines/<date>-stage-b-after-hd.json` (`metrics --hd` and the geometry and network metrics of the example set); sweeps of 200 seeds with HD on.
- **Summary:** `plans/<date>-stage-b-summary.md`, a supervisor summary like those of stages A and C.

## Exit criteria

| Metric | Target |
|---|---|
| Builder output with HD off | byte-identical `.xodr` and sidecar to B0 (guard networks), through B9; with `--no-hd` after B10 |
| lsystem identity test | unchanged |
| Build failures, 200 seeds, `city` and `mixed_city`, HD on | 0 |
| Edges removed or merged by the cross-section repair | ≤ 1 % of junction roads per example |
| Validators (incl. `hdmap`) | pass on every example |
| Drive-check | passes on every example it ran on before stage B (interchanges still open) |
| Vehicles in `scenario` / `simulate` | only on driving lanes (positions checked on `city`) |
| Controlled approaches with a stop or yield line and a sign or light | 100 % |
| Lights switch in esmini | yes, shown in OSI output or screenshots |
| Control mix | lights at every at-grade avenue × avenue and highway × avenue junction; yield and stop present in `city` and `mixed_city`; yield, stop and right-before-left together in `mixed_city` *(changed by prompt 8: `city` has no street × street junctions)* |
| Build time | ≤ 2 × B0 on `city` and `mixed_city` |

## Open items for later stages

- **Stage D (diversity of the generator):** `city` (lsystem preset `city`, seed 7) has no street × street junctions at all: every junction touches an avenue or a highway (B5 measurement, `baselines/2026-10-30-stage-b-after-b5-hd.json`), so right before left never occurs there. That is a gap of the generator's diversity, not of stage B *(prompt 8)*.

## Risks

| Risk | Handling |
|---|---|
| esmini draws some lane types, heights or objects badly, or odrviewer's traffic uses shoulders | settled by B0: heights, crosswalks and stop lines draw; traffic keeps to driving lanes; the junction fill gap is handled in B3 |
| esmini does not cycle RoadNetwork light controllers | confirmed in B0; storyboard events always written (B6) |
| Role-based lane links break stage A tapers or the drive-check | B1 introduces lane roles with driving lanes only (identical output, guard); B2 adds side lanes with continuity tests |
| Wider trims cause more stubs and advisory corners | measured in B1; repair; metrics after every phase |
| `<priority>` records make files too large | measured in B5; switch to sidecar only |
| Turn pockets and trims depend on each other | pockets decided from the graph before the trims (B8) |
| Banned turns trap traffic | applied only if the lane graph stays strongly connected |
| Larger files slow `simulate` and `bench` | measured in B9 before the flip |
| Many tests assume driving-only defaults | the flip is one phase (B10) with the tests moved to explicit settings |

## Verification

- `docker compose run --rm -T dev python -m pytest -q` (all green apart from
  the known bench test) and `ruff check` on the touched files after each phase.
- `bash plans/scripts/stage_b_examples.sh` (B0), then for each example:
  - `python -m cli metrics out/<hash>/<name>.xodr --hd --json …`;
  - `python -m cli view out/<hash>/<name>.xodr --screenshot …`.
- End to end, after B10:
  - `python -m cli pipeline lsystem --preset city --maps --seed 7 --ground --buildings --drive-check --simulate -o city_hd`;
  - `python -m cli plot-xodr out/<hash>/city_hd.xodr`;
  - `docker compose run --rm gui python -m cli view out/<hash>/city_hd.xodr --scenegraph out/<hash>/city_hd_scene.osgt`.
- With `--no-hd`, the same commands give the B0 outputs (guard).
