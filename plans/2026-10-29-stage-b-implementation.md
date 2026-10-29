# Stage B (HD-map completeness): implementation plan

Status: **approved 2026-10-30; B0 done (2026-10-30), B1 next.** Discussion,
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

### B7 · Crosswalks, speed and curve signs

- **Crosswalks:**
  - `<object type="crosswalk">` with an open `cornerLocal` outline of two corners (ids 0, 1) from curb to curb, and a white `<marking>` with `<cornerReference>` 0 and 1, `width` 3 m (street) / 4 m (avenue). B0 showed esmini draws this as a zebra across the road;
  - zebra stripes (Z 293: `lineLength` 0.5, `spaceLength` 0.5) and sign 350-10 / 350-20 at junctions without lights; at lights, two solid markings 0.3 m wide along the crosswalk's edges (Fußgängerfurt) instead;
  - at the junction border of every arm with sidewalks on both sides;
  - the stop line moves 1 m behind the crosswalk;
  - skipped where the approach is shorter than crosswalk + stop line + 2 m.
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
- **Performance against B0:** build and validation time, `.xodr` size, esmini load time and real-time factor on `city` and `mixed_city`.
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
| Control mix | lights at every at-grade avenue × avenue and highway × avenue junction; yield, stop and right-before-left all present in `city` and `mixed_city` |
| Build time | ≤ 2 × B0 on `city` and `mixed_city` |

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
