# Stage B (HD-map completeness): discussion and decisions

Source: `Roadgen AV Readiness Review.pdf` (repo root), part **B · HD-map
completeness**. Implementation plan:
[2026-10-29-stage-b-implementation.md](2026-10-29-stage-b-implementation.md).
The options below were written before the user's answers; where an answer
removed an option (the US profile, all-way stops), the
[Decisions](#decisions) table says so.

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

## What part B of the review asks for

1. **Lane types:** shoulders, sidewalks, curbs, parking, bike lanes and
   medians next to the driving lanes.
2. **Road marks:** a solid yellow centre line, solid lines on junction
   approaches as a no-lane-change zone, stop lines, no marks inside
   junctions (Muktadir et al. Stage 4).
3. **Signals, signs and objects**, each placed by distance along the road
   and offset from it: traffic lights with phase plans, stop and yield signs
   from the road hierarchy with right of way, speed limit signs, and
   crosswalks at junction borders (StreetGen §2.9).
4. **Junction realism:** turn lanes and turn restrictions from a lane-link
   solver, and corner radii by road class (about 3, 4.9 and 7.6 m in Paris;
   Muktadir et al. Stage 3, StreetGen §2.3–2.5).

## What exists today

Checked in the code on `main` (1cc11cc) and in
`out/56d99e5ffd78/mixed_city_full.xodr`.

| Area | Today | Where |
|---|---|---|
| Lane types | every lane `driving`; one-way roads have all lanes on the right | `opendrive/lanes.py` |
| Road marks | centre line solid white (`standard`), lane lines broken white (3 m / 9 m), outermost lane solid white; 909 / 1115 / 1779 marks of these kinds in `mixed_city_full` | `opendrive/lanes.py` |
| Marks inside junctions | **none already**: connecting roads get no `<roadMark>` (0 of 4100 in `mixed_city_full`) | `opendrive/junctions.py::_connecting_road` |
| Signals, objects | none; only `<objects><bridge>` / `<tunnel>` written by hand (scenariogeneration has neither) | `opendrive/builder.py::_add_structures` |
| Right of way | none: `<junction>` has connections only, no `<priority>`, no `<controller>` | `opendrive/junctions.py` |
| Lane-link rules | `plan_movements`: right turns from the rightmost lane, left from the leftmost, straight lane-to-lane, `fill_pair` gives every incoming lane an exit, two-arm junctions continue every lane | `opendrive/junctions.py` |
| Lane splits (C5) | a straight exit with more lanes than its entry is fed by the outer (default) or inner lane (`opendrive.lane_split`) | `straight_pairs` |
| U-turns (C7) | optional (`opendrive.u_turns`, off), only where in and out lane centres are ≥ 2 × 5 m apart | `u_turn_lane` |
| Ramp nodes (C9) | no left turns, no ramp to ramp, no lane split, straight within 5° | `plan_movements(ramp=True)` |
| Corner radii | **not implemented.** StreetGen's radii were cited in the stage A and C plans but never used. Trims are the widest half cross-section, or the crossing of neighbouring road edges at acute angles, plus a 2 m margin; turn paths are lane-centre clothoids between the trimmed lane ends | `geometry/junction_space.py`, `turn_path` |
| Turn lanes | no turn pockets; lane use is implicit in the movements and not recorded as arrows or lane-use data | — |
| Turn restrictions | only the ramp-node rules | — |
| Left for stage B | `metadata["roundabouts"]` (`entries`, `ring` node ids, radius, lanes); `metadata["interchanges"]` (`terminals`, `ramp_nodes`, `ramps`) | `transforms/roundabout.py`, `transforms/interchange.py` |
| Consumers that already filter on `type="driving"` | scenario spawn slots and lane graph, drive-check of whole lanes, `metrics` lateral acceleration | `scenario/xosc_builder.py`, `sim/drive_check.py`, `metrics/geometry.py` |
| Code that assumes driving lanes are -1..-n at uniform width | `RoadEnd.in_lanes` / `out_lanes` / `lane_pose`, `make_lane_sections` lane links by position, `half_width`, `road_half_extent`, `oriented_lanes` widths | `opendrive/junctions.py`, `opendrive/lanes.py`, `geometry/alignment.py`, `scene/buildings.py`, `geometry/junction_space.py` |

So for item 4, stage C delivered the lane-link rules and lane splits. Still
missing: corner radii, explicit lane use (arrows, lane-use records), turn
pockets, and turn restrictions beyond ramp nodes.

## Measurements (2026-10-29)

Script: [`scripts/side_room.py`](scripts/side_room.py), output
[`baselines/2026-10-29-stage-b-side-room.json`](baselines/2026-10-29-stage-b-side-room.json).
It regenerates nine example networks in-process (same presets and seeds as
the stage C examples) and recomputes every junction's trims the way
`junction_space.junction_trims` does, with extra width outside the driving
lanes. With corner radii, it adds a curb-return fillet between neighbouring
arms: the arm must reach past the fillet's tangent point, R / tan(θ/2)
beyond the point where the two curb lines cross. Straight edges, no curve
fitting, so the counts are estimates. "Today / no radii" reproduces the
builder (0 failures everywhere).

Cross-sections tried (per side, outside the driving lanes):

| Scenario | street | avenue | highway |
|---|---|---|---|
| curb + sidewalk | curb 0.15 + sidewalk 2.0 | curb 0.15 + sidewalk 3.0 | shoulder 2.5 |
| full | parking 2.2 + curb + sidewalk 2.0 | bike 1.5 + parking 2.4 + curb + sidewalk 3.0 | shoulder 3.0 + median 2.0 (1.0 per side) |
| full, no parking at junctions | as full, but parking stops before each junction | same | same |

Corner radii (StreetGen, the smaller of the two roads' values): street 3.0,
avenue 4.9, highway 7.6 m.

**Edges the builder would reject / stub roads under 5 m** (mean trim in m),
out of the roads ending at junctions:

| Network (roads at junctions) | today | today + radii | curb + sidewalk | + radii | full | + radii | full, no parking at junctions | + radii |
|---|---|---|---|---|---|---|---|---|
| grid (176) | 0 / 0 (9.0) | 0 / 0 (10.5) | 0 / 0 (12.2) | 0 / 0 | 0 / 0 (16.1) | 0 / 0 | 0 / 0 (13.7) | 0 / 0 |
| manhattan_like (720) | 0 / 0 (15.1) | 0 / 0 | 0 / 0 (18.3) | 0 / 0 | 0 / 0 (22.2) | 0 / 0 | 0 / 0 (19.8) | 0 / 0 |
| grid_organic (122) | 0 / 0 (9.2) | 0 / 0 | 0 / 0 (12.5) | 0 / 0 | 0 / 0 (16.6) | 0 / 0 | 0 / 0 (14.0) | 0 / 0 |
| organic_town (195) | 0 / 0 (9.2) | 0 / 0 | 0 / 0 (12.5) | 0 / 0 | 0 / 11 (16.5) | 0 / 11 | 0 / 0 (14.0) | 0 / 0 |
| paris (400) | 0 / 0 (9.4) | 0 / 0 | 0 / 0 (12.9) | 0 / 0 | 3 / 5 (17.0) | 3 / 6 | 0 / 2 (14.3) | 0 / 2 |
| suburb (419) | 0 / 0 (8.9) | 0 / 2 | 1 / 14 (12.2) | 1 / 14 | 19 / 12 (16.1) | 20 / 11 | 3 / 16 (13.5) | 3 / 16 |
| city, maps (615) | 0 / 0 (10.1) | 4 / 6 | 0 / 0 (13.5) | 13 / 7 | 2 / 4 (17.1) | 20 / 4 | 0 / 4 (15.2) | 18 / 3 |
| mixed_city, maps (770) | 0 / 0 (9.9) | 2 / 4 | 1 / 4 (13.5) | 3 / 4 | 9 / 18 (17.6) | 13 / 22 | 4 / 3 (15.0) | 5 / 8 |
| highway_corridor, maps (255) | 0 / 0 (9.3) | 2 / 0 | 2 / 0 (12.7) | 2 / 0 | 2 / 0 (16.7) | 2 / 0 | 2 / 0 (14.3) | 2 / 0 |

What this means:

- **Sidewalks alone are cheap:** trims grow by about 3.5 m and only 0–2
  edges per network fail (suburb, mixed_city, and highway_corridor, where
  the shoulders meet at the 25° ramp nodes).
- **Parking is what breaks networks** (up to 20 failures in `suburb`).
  Ending parking lanes before junctions (curb extensions, as real streets do:
  StVO §12 forbids parking within 5 m of a junction, US codes 20–30 ft)
  brings failures back to 0–4 per network.
- **Corner radii barely matter on grids with sidewalks** (the outer
  sidewalk edges dominate), but **highway 7.6 m fillets at acute at-grade
  highway junctions** cause 4–20 failures on `city`. StreetGen's own answer
  (§2.5): shrink the radius to the largest one that fits. With that, radii
  never make a network unbuildable.
- The rest (0–5 edges, plus stubs) is what a cross-section-aware repair has
  to fix: about 1 % of junction roads in the worst case (`suburb`).

**Junction-control cases** in the same networks (junctions by highest ×
lowest road class meeting there; median population density at them where
maps exist):

| Network | Junctions | Class pairs | Roundabouts | Interchanges | Median density [people/km²] |
|---|---|---|---|---|---|
| grid | 96 | avenue×street 96 | 0 | 0 | — |
| manhattan_like | 380 | avenue×street 360, avenue×avenue 20 (640 one-way edges) | 0 | 0 | — |
| paris | 228 | avenue×street 222, street×street 5, avenue×avenue 1 | 0 | 0 | — |
| suburb | 251 | avenue×street 203, street×street 30, avenue×avenue 18 | 9 | 0 | — |
| city, maps | 352 | avenue×street 196, highway×street 69, highway×avenue 63, highway×highway 24 | 0 | 0 | 3,400–4,100 |
| mixed_city, maps | 435 | avenue×street 334, highway×street 43, highway×avenue 21, street×street 22, avenue×avenue 12 | 5 | 0 | 1,700 (street×street) – 6,700 |
| highway_corridor, maps | 139 | avenue×street 131, highway×* 8 | 0 | 1 | 4,200–7,100 |

So a rule on class pairs alone would give almost every city the same
answer (avenue × street everywhere); the population map is what can
separate a signalised downtown from a priority-controlled edge. Two-thirds
of `city`'s highway junctions are at grade with streets (the `city` preset
keeps `highway_crossings: at_grade`).

## Tooling: what we can use

`scenariogeneration` 0.16.7 (the version in the container) and esmini
v3.9.0, checked in the dev container on 2026-10-29.

| Need | scenariogeneration | We write XML ourselves |
|---|---|---|
| Lane types | `xodr.LaneType`: shoulder, border, sidewalk, curb, parking, biking, median, … all OpenDRIVE 1.6 types | — |
| Curb / sidewalk height | `Lane.add_height` | `level` attribute (not checked yet) |
| Road marks | `RoadMark(type, width, …, color, laneChange, soffset)`; types incl. `solid_solid`, `broken_solid`; colours incl. `yellow`; several marks per lane via `soffset` | — |
| Signals | `Signal(s, t, country, Type, subtype, value, unit, dynamic, orientation, …)` with `add_validity`; `SignalReference` | `<dependency>` (the class exists but `Signal` has no `add_dependency`) |
| Objects | `Object(s, t, Type, …)` with `add_outline` (`cornerLocal` / `cornerRoad`), `add_marking` (crosswalk stripes), `add_object_roadside` (repeated objects) | — |
| Junction priority | no | `<junction><priority high low>` |
| Controllers | no | top-level `<controller><control signalId>`, `<junction><controller>` |
| OpenSCENARIO lights | `xosc.TrafficSignalController` + `Phase.add_signal_state`, `RoadNetwork.add_traffic_signal_controller`, `TrafficSignalStateAction`, `TrafficSignalControllerAction` | — |

Everything stays behind our builder: lane and mark construction in
`opendrive/lanes.py`, signals and objects in a new `opendrive/furniture.py`
(placement is pure, XML via scenariogeneration), priority and controller
records added to the element tree in `OpenDriveBuilder.write`, as
`_add_structures` already does for bridges and tunnels.

esmini 3.9 (resources and binaries in the container):

- **Sign catalogues:** `resources/traffic_signals/` maps codes to OSI sign
  types for **DE** (397 entries: 205 give way, 206 stop, 274 speed limit,
  278 end of limit, 301 / 306 / 307 right of way, 215 roundabout, 350-10/-20
  zebra crossing, 209 / 211 / 214 prescribed directions, 102 right before
  left, …), SE (13) and CN (2), plus generic `OpenDRIVE` codes (1000001
  vehicle light, 1000002 pedestrian light, …). **There is no US table**, so
  US signs would reach OSI untyped.
- **3D models:** only `de_123`, `de_274-30`, `de_274-50`, a few SE / CN signs
  and the `opendrive_1000001` light. Stop and yield signs have no model, so
  esmini shows them only in OSI, not on screen (we can add simple posts in
  our scene).
- **Lights:** the shipped example (`fabriksgatan_traffic_lights.xodr` +
  `traffic_lights.xosc`) uses `country="OpenDRIVE" type="1000001"`, and
  switches lamps with storyboard `TrafficSignalStateAction`s, states as
  `"off;off;on"` (red;yellow;green). The binary contains
  `TrafficSignalController` and `TrafficSignalControllerAction`. Whether it
  runs a RoadNetwork controller's phases on its own still has to be tested
  (B0).
- The binaries know `sidewalk`, `curb`, `crosswalk`, `markings`,
  `cornerLocal`, `laneChange`, `yellow`, `solid solid`, `priority`,
  `dependency`, `validity`; `fabriksgatan.xodr` uses sidewalk lanes with
  `<height>` 0.12 m and `border` lanes. How each renders, and whether
  odrviewer's traffic stays off shoulders, is for the B0 spike.
- **esmini's default controller ignores lights and signs.** Vehicles will
  not stop at red lights or yield. Switching lights is for the picture,
  OSI ground truth and other tools; reactive traffic is stage E.

## Where each decision lives

Principle (as asked): the builder derives everything it can from what the
graph already holds. The graph gets only **optional overrides** that default
to "derive", so existing graph JSON, generators and the lsystem fingerprints
stay as they are. Information that needs the input maps (which the builder
never sees) is written by a **new pipeline step**, not by the generators,
like `align` writes `Node.radius_cap` today.

| Decision | Lives in | Why |
|---|---|---|
| Road class, lanes, speed, one-way | graph (exists) | — |
| Side lanes, widths, curb height, median, corner radii per class | **builder params**, in a neutral module `roadgen/geometry/cross_section.py` (frozen `CrossSectionParams` with presets) | trims, alignment clearance, repair, ground and buildings all need the same widths; the geometry package is already shared without OpenDRIVE code |
| Which profile an edge uses (urban / suburban / rural / none) | derived from class (default); optional **`Edge.cross_section: Optional[str]`** | lets a map-aware step or a hand-made graph choose; `None` = derive |
| Junction control type | derived (class pair, legs, speed, node kind, roundabout and interchange metadata); optional **`Node.control: Optional[str]`** | density only exists in the maps |
| Priority road at a junction | derived: the two highest-ranked arms (class, then lanes, then speed) that are roughly opposite | no generator knows more than that |
| Stop lines, yield lines, crosswalks, signs, lights | derived from the trims, the control type and the lane layout | pure functions of the built junction |
| Light phase plans | derived (builder) → sidecar `signal_plans` and OpenDRIVE `<controller>`; the xosc builder reads the sidecar | the scenario builder already reads the sidecar's movements |
| Lane use (arrows), turn pockets, turn restrictions | derived (lane-link solver in `junctions.py`) | movements are planned there today |
| Speed limit and curve signs | derived from the `<type>` speed records and stage A's corners | already computed |
| What is a roundabout / an interchange | graph metadata (exists since stage C) | read by the builder |
| Random choices (stop vs yield share, light offsets, banned turns) | builder, from a `seed` the pipeline passes in, salted sub-streams per feature | deterministic for a seed, like `BuildingParams.seed` |

**The new pipeline step** (working name `annotate`, also a CLI command like
`align` / `drape` / `separate`): with maps, it sets `Edge.cross_section`
from population density and the style map's zones (dense core → urban with
parking, suburb zone → suburban without parking, low density → rural
shoulders) and `Node.control` where density calls for lights. Without maps
it does nothing. It runs after `separate`, never inside `generate`, so the
identity test (which calls the generator directly) is unaffected.

Options considered:

- **(a) Everything derived in the builder (recommended, plus the optional
  overrides above).** No generator change, no fingerprint change; one place
  for the rules. Downside: without maps every street of a class looks alike.
- **(b) Generators write cross-sections and controls.** Generators know the
  zones while growing, but every generator and transform must learn HD
  details, the graph JSON of every preset changes, and the architecture
  rule (generators stay HD-agnostic) bends.
- **(c) Builder reads the maps.** Breaks "the builder only consumes a
  RoadGraph".

## 1 · Cross-section model

### The params

`CrossSectionParams` (frozen, presets, defaults in `defaults.yaml` under a
new `cross_section` section) describes, per road class and per profile, the
lanes outside the driving lanes from the inside out, plus a median:

| Profile | street (per side) | avenue (per side) | highway (per side) |
|---|---|---|---|
| `urban` | parking 2.2, curb 0.15 (h 0.15), sidewalk 2.0 | bike 1.5, parking 2.4, curb 0.15, sidewalk 3.0 | shoulder 3.0; median 2.0 when 2+2 lanes or more |
| `suburban` | curb 0.15, sidewalk 1.8 | bike 1.5, curb 0.15, sidewalk 2.0 | shoulder 3.0 |
| `rural` | shoulder 1.0 | shoulder 1.5 | shoulder 3.0 |
| `none` | — (today's output) | — | — |

Sources: AASHTO Green Book (10 ft / 3.0 m freeway shoulders, 1.5 m bike
lanes, curb height 150 mm), ADA (≥ 1.5 m sidewalks), German RASt 06 / EAE
(sidewalks ≥ 2.5 m desirable, 2.0–2.5 m parking bays, 12 cm curbs as in
esmini's `fabriksgatan`). Plus: `parking_clear` (parking ends this far
before a junction, default 10 m, tapering over 5 m as a curb extension),
`corner_radius` per class (3.0 / 4.9 / 7.6 m, StreetGen Fig. 5) with the
rule r = min of the two roads (StreetGen §2.4) and shrink-to-fit (§2.5).

OpenDRIVE layout for a two-way street, right side outward: `-1 … -n`
driving, then parking, curb, sidewalk; left side mirrored. The driving lanes
keep their ids, so the junction code and the sidecar movements are
unchanged. A one-way road has its reference line on its left edge: its
left-edge side lanes become positive lanes (curb, sidewalk) next to the
centre lane, which is valid OpenDRIVE.

### How it works with what stages A and C built

- **Lane sections and tapers (A):** side lanes run through every lane
  section. Where an outer driving lane tapers to 0, the side lanes follow its
  edge inward (the curb follows the taper, as on real roads), and their ids
  shift by one. So `make_lane_sections` must link lanes **by role** (type and
  index within the type), not by position, or a sidewalk would be linked to a
  driving lane. Parking tapers out before junctions with the same smoothstep.
- **Lane splits (C5):** junction-internal, unaffected; only driving lanes get
  connecting roads.
- **Merges / diverges, ramp nodes (C9):** shoulders end before ramp nodes
  (taper, like parking), since the gore between highway and ramp is not a
  shoulder; this also removes the 2 failures `highway_corridor` shows.
  Ramps get the highway profile without median.
- **Bridges (A, C8):** sidewalks and shoulders stay on bridges; parking does
  not (it ends before the span). The `<bridge>` record is unchanged; the
  bridge is as wide as the road with its side lanes. Railings as
  `<object type="railing">` (repeated) are optional.
- **Tunnels (C8):** highway shoulders narrow to 1.0 m inside (common tunnel
  practice); optionally a raised walkway (curb + sidewalk 1.0 m).
- **Roundabouts (C7):** ring edges are one-way: outside a curb and the
  arms' sidewalk profile, inside a curb (the central island is not a lane).
  Entries get splitter islands only if medians are on (later, see 7).
- **Cul-de-sac bulbs (C4):** street profile; the loop is a one-way ring, so
  the inner side gets a curb only.
- **Medians:** a `median` lane next to the centre line shifts the driving
  lane ids by one (-2 … -n-1). That breaks every place that assumes driving
  lanes start at -1 (`RoadEnd.in_lanes`, `lane_pose`, the junction
  movements, the scenario's lane slots). Options:
  - **(a) Generalise lane roles** once: `RoadEnd` carries explicit driving
    lane ids and their lateral offsets from the lane layout, instead of
    computing them from a count and one width. About 1 day, and turn pockets
    (section 7) need the same change.
  - **(b) No medians in stage B**; leave them to divided carriageways (a
    stage C option that was deferred).
  - Recommendation: (a), with medians only on two-way highways (and
    avenues if wanted) of 2+2 lanes or more; at junctions the median ends
    (taper) or becomes the left-turn pocket.

Effort: params and neutral module 1 d; lanes, role links, tapers, one-way
and special cases in the builder 2.5 d; lane-role generalisation 1 d.

## 2 · Knock-on effects of wider roads

| Consumer | Today | Change |
|---|---|---|
| `geometry/junction_space.py` trims | widest half width + edge crossings + 2 m | curb lines with the corner fillet (shrunk to fit) and outer edges, as in `side_room.py`; parking and shoulders ending before junctions do not count |
| `geometry/alignment.py` | `half_width` for road clearance and corner offsets | clearance uses the full half width (no sidewalk may overlap another road); the speed model keeps using driving lanes only |
| `generators/repair.py` | runs inside `generate` with today's trims | a cross-section-aware repair (same algorithm, wider trims) in the pipeline before the build; see below |
| `scene/buildings.py` `road_half_extent` | driving lanes + 3 m `setback` ("sidewalk") | full half width from the cross-section; `setback` becomes the front yard behind the sidewalk (default 1 m when sidewalks exist) |
| `scene/ground.py` levelling | `half_width` + `road_margin` | full half width, so the ground never covers a sidewalk; the 20 m mesh can't show a 2 m sidewalk, so see the streetscape mesh in section 9 |
| `maps/drape.py` | bridges where the centre line is over water | buffer by the half width, so a sidewalk over the shore is on the bridge too (small) |
| `geometry/grade_separation.py` | flat top from `half_width(low)` | full half width of the lower road (longer flat top) |
| `validate/clearance.py` | vertical gap at crossings | unchanged rule; reads the lane widths from the `.xodr`, so it picks up the wider decks by itself (to confirm) |
| `opendrive/junctions.py` turn paths | lane-centre clothoids between the trimmed lane ends | unchanged; a new check that every right-turn path stays inside the curb return |
| `scenario/xosc_builder.py`, `sim/drive_check.py`, `metrics` | filter `type="driving"` | unchanged, plus tests with side lanes and pockets |

**Too-short edges.** Options:

- **(a) Parking (and shoulders at ramp nodes) end before junctions +
  corner radii shrink to fit + a cross-section-aware repair pass in the
  pipeline (recommended).** The measurement says this leaves 0–5 edges per
  network to collapse or remove. The repair is the existing deterministic
  one, run with wider trims as part of the `annotate` step (and by
  `build-xodr` on hand-made graphs). The graph JSON a pipeline writes then
  differs from `generate`'s by those few edges, as it already does after
  `align`, `drape` and `separate`.
- **(b) The builder narrows sidewalks locally** where the room is short.
  No topology change, but sidewalk widths jump, every consumer must know
  the local width, and a bad junction stays bad.
- **(c) Generators learn the widths** (`min_segment_length` from the
  profile). Changes generators and fingerprints for a 1 % effect.

Effort: trims and repair 1 d, scene / drape / grade separation / buildings
1.5 d.

## 3 · Road marks

| Mark | Options | Recommendation |
|---|---|---|
| Centre line | (a) US / MUTCD: yellow, double solid on multi-lane two-way roads, solid on approaches; (b) DE / Vienna: white, broken on ordinary streets, solid (Z 295) where crossing is forbidden and on approaches | follow the country profile (question 1); both are a small table in `lanes.py` |
| No-lane-change zone | solid lane lines over the last `no_change_length` of each approach (20 m street, 30 m avenue, 50 m highway), with `laneChange="none"`, via a second `<roadMark sOffset>` in the last lane section(s) | as described (JunctionArt Stage 4) |
| Edge lines | solid between the outer driving lane and parking, shoulder or bike lane; a `curb` mark on curb lanes; no mark on sidewalks | as described |
| Bike lanes | solid line to the driving lane (MUTCD 9C, ERA) | as described |
| Stop lines | (a) `signal` DE 294 (Haltlinie) with `<validity>` over the approach lanes; (b) `<object type="roadMark">` with an outline, for rendering; (c) both | (c) in the DE profile, (b) in the US one; B0 checks what esmini draws |
| Yield lines | DE Z 341 (broken, "Wartelinie") / US yield line ("shark's teeth") as `roadMark` objects | at yield approaches and roundabout entries |
| Inside junctions | already none | keep, and add a validator check |
| Arrows | lane-use arrows as `roadMark` objects (and DE 297 signals if esmini's table has them) | with the lane-use solver (section 7) |

Effort: 1.5 d.

## 4 · Junction control and right of way

### Choosing the control type

Options:

- **(a) A class-pair matrix in the builder** (`ControlParams`, presets per
  country): fixed answer per (major class, minor class), corrected by leg
  count and speed. Cheap and transparent, but uniform across a city (see
  the measurement: almost everything is avenue × street).
- **(b) (a) plus density via the `annotate` step (recommended):** where a
  population map exists, junctions whose density exceeds `signal_density`
  get lights, below `stop_density` the minor road gets a stop sign instead
  of a yield sign. The threshold is calibrated in B0 on the measured
  densities (3,000–4,500 people/km² median at avenue × street), aiming at
  roughly a quarter of `city`'s avenue × street junctions with lights.
- **(c) Traffic-volume warrants (MUTCD 4C):** needs traffic volumes we don't
  have; a demand model is out of scope.

Proposed rules (first match wins; every value a parameter):

| Junction | Control | Signs and marks |
|---|---|---|
| Two-road node that stays a junction (sharp bend, lane-count change) | none | — |
| Ramp node (C9) | none (merge / diverge lane) | — |
| Roundabout entry (`metadata["roundabouts"]`) | arm yields, ring has priority | DE 205 + 215 (US R1-2 + R6-5P), yield line |
| Diamond ramp terminal (`metadata["interchanges"][*]["terminals"]`) | lights if the crossing road is an avenue or more, or density ≥ `signal_density`; otherwise stop on the ramp, crossing road has priority | lights, or 206 + stop line on the ramp |
| `Node.control` set (by `annotate` or by hand) | as set | as below |
| Major and minor road both avenue or higher (avenue × avenue, highway × avenue, highway × highway at grade), or ≥ 5 legs with an avenue | lights | 1000001 per approach, stop lines, crosswalks |
| Major × minor (class, lanes or speed differ) | priority road; minor yields, or stops when the major speed ≥ 60 km/h, the junction is skewed > 30° or has ≥ 5 legs, plus a seeded `stop_share` for variety | DE 306 / 301 on the major road, 205 / 206 + yield or stop line on the minor; US R1-2 / R1-1 |
| Street × street, ≤ 4 legs | DE: right before left (no signs, optional 102); US: all-way stop | US R1-1 + R1-3P and stop lines on every approach |

### Right of way in OpenDRIVE

- **Signs and lights** carry the rules for most tools (esmini's OSI, CARLA,
  RoadRunner). Placed at the stop line, on the right-hand roadside
  (`t` = outer edge of the carriageway + 0.5 m, i.e. on the sidewalk or
  shoulder), `orientation` "+" for traffic along +s and "-" against it,
  `<validity fromLane toLane>` over the approach's driving lanes.
- **`<junction><priority high low>`** (1.6: ids of connecting roads): one
  record per pair of conflicting connecting roads whose approaches have
  different priority (major vs minor, ring vs entry), plus left turns
  yielding to the oncoming straight and right turns. Conflicts come from
  the turn paths (they intersect or end in the same lane). Size: a 4-leg
  2+2 junction has about 20 movements and up to ~100 conflicting pairs;
  measured in B5, with a parameter to leave them out if files grow too much.
  esmini ignores them; they are for other tools and the training data.
- **Sidecar:** every movement gets `control`, `priority` (major / minor /
  signal phase) and `yields_to` (connecting road ids), the
  learning-friendly form of the same information.
- **Signal dependencies** (`<dependency>`): only to tie a supplementary
  plate (e.g. an all-way plaque, a speed value) to its main sign. Not
  needed for right of way.

### Light phase plans

Fixed-time plans built by the builder: approaches grouped into phases by
opposite pairs (heading difference within 180 ± 30°), leftover arms get
their own phase; T junction 2 phases, 4 legs 2 phases (permissive lefts) or
4 with protected lefts where a left pocket exists, 5+ legs one phase per
pair or arm. Timings: yellow 3 s at 50 km/h, by ITE y = t + v / 2a (3–5 s);
all-red 2 s; min green 10 s; cycle 60–90 s with green split by lanes;
offsets seeded per junction. Pedestrian lights (1000002) run with the
parallel vehicle phase where crosswalks exist (optional).

Where the plans live:

- **(a) OpenDRIVE `<controller>` + sidecar only.** OpenDRIVE has no timing,
  so lights stay dark in esmini.
- **(b) (a) plus OpenSCENARIO `TrafficSignalController` in the
  `RoadNetwork` of every `.xosc` (recommended),** written by
  `scenario/xosc_builder.py` from the sidecar's `signal_plans`, with
  `TrafficSignalState` strings `"on;off;off"` etc. If the B0 spike shows
  esmini 3.9 does not cycle RoadNetwork controllers, the fallback is a chain
  of storyboard events (`TrafficSignalStateAction` with simulation-time
  conditions, as in esmini's own `traffic_lights.xosc`), generated up to
  `sim_duration`.
- **(c) Only storyboard events.** Works in esmini for sure, but is
  non-standard for other OpenSCENARIO tools and grows with the duration.

Effort: control rules, priorities, sidecar 3 d; lights, controllers, phase
plans and the xosc part 3 d.

## 5 · Sign catalogue

OpenDRIVE 1.6 signals: `country` is an ISO 3166-1 alpha-2 code or
`"OpenDRIVE"` for the generic codes (lights), `type` / `subtype` from that
country's catalogue, `value` + `unit` for numbers.

| Option | Advantages | Disadvantages |
|---|---|---|
| **DE (StVO, Vienna Convention)** | esmini maps 397 DE codes to OSI sign types, so OSI ground truth is typed; CARLA's OpenDRIVE reader uses the same codes (checked in B0 against `LibCarla/source/carla/road/SignalType.cpp`: 205 yield, 206 stop, 274 speed, 301 / 306 / 307, 215, 209 / 211 / 214, lights 1000001 / 1000002; **not 294**, which this table first claimed); the de-facto default of OpenDRIVE samples; white marks | no yellow centre line; speed signs in km/h (our speeds are m/s anyway) |
| **US (MUTCD)** | yellow centre lines, all-way stops, the convention JunctionArt (and the review) uses | no esmini sign table, so OSI signs come out untyped; codes like R1-1 are not standardised in OpenDRIVE; speed signs in mph |
| **Profile with DE default, US optional (recommended)** | the review's yellow line and all-way stops are one flag away; the training data can cover both; OSI typed by default | two tables to maintain (marks, signs, control matrix); each about 30 entries |

Codes used (DE / US):

| Meaning | DE `type`-`subtype` | US |
|---|---|---|
| Stop | 206 | R1-1 |
| Yield | 205 | R1-2 |
| All-way plaque | — | R1-3P |
| Priority road / at next junction / end | 306 / 301 / 307 | — |
| Roundabout | 215 | R6-5P (plaque) |
| Speed limit / end | 274 (value, km/h) / 278 | R2-1 (value, mph) |
| Curve warning (advisory corners) | 103-10 / 103-20 (+ 274 plate for the advisory speed, or `value`) | W1-2 + W13-1P |
| Pedestrian crossing | 350-10 / 350-20 | R1-5 / W11-2 |
| Prescribed direction (banned turns) | 209-10/20/30, 214 | R3-1 / R3-2 / R3-5 |
| Stop line / yield line (markings) | 294 / 341 | object only |
| Vehicle / pedestrian light | `OpenDRIVE` 1000001 / 1000002 | same |

esmini draws only signs it has a model for; the others still reach OSI
(with the DE table). Simple posts with a coloured plate can be added to our
scene so every sign is visible (section 9).

Effort: 0.5 d per profile table.

## 6 · Placing signals, signs and objects

All placement is in road coordinates (StreetGen §2.9: objects positioned by
curvilinear abscissa, relative to the axis or the sidewalk):

- **Stop / yield line:** across the approach's driving lanes, 1 m before the
  road's end at the junction, moved back behind the crosswalk where there is
  one (MUTCD: 4 ft / 1.2 m before it).
- **Crosswalk:** `<object type="crosswalk">` with a `cornerLocal` outline from
  curb to curb and zebra `<markings>` (DE Z 293 at unsignalised junctions,
  two transverse lines at lights), 3 m (street) / 4 m (avenue) wide, at the
  junction border on every arm with sidewalks on both sides (StreetGen
  §2.9.2: a surface from sidewalk to sidewalk). Skipped where the approach
  is shorter than crosswalk + stop line + 2 m.
- **Signs:** on the right roadside at the stop line; priority-road signs
  after the junction on the major road; speed limit signs at every change of
  the `<type>` speed (including stage A's advisory corners: warning sign +
  advisory value before the corner, `ADVISORY_DECEL` lead) and after each
  junction on roads longer than 100 m.
- **Lights:** one 1000001 per approach on the right roadside, plus one over
  the lanes (`zOffset` 5.5 m) for approaches with 3+ lanes; all in one
  controller per junction.
- **Ids:** signals and objects get ids unique per file, numbered like the
  bridge / tunnel objects (`<road id><kind><k>`).

Effort: 2 d (crosswalks, signs, speed signs), counted with sections 4–5.

## 7 · Junction realism (review item 4)

### Corner radii by road class

As measured, the curb-return fillet only changes trims at acute junctions
and with highway radii. It matters for:

1. **Trims:** the fillet term, radius shrunk to fit (StreetGen §2.5), so it
   never makes a network unbuildable.
2. **The junction outline:** curb arcs between neighbouring arms, written to
   the sidecar (`corners`: junction, arms, centre, radius, tangent points)
   for the scene, the plots and validation.
3. **Sidewalk corners:** either (a) sidewalk-only connecting roads around
   each corner (lane type `sidewalk`, linked to the arms' sidewalks): a
   continuous sidewalk network in the map, useful for pedestrians in stage E,
   drawn by esmini itself; or (b) corner fills only in our scene mesh.
   Recommendation: (a) if B0 shows esmini never routes vehicles onto them
   (they link only to sidewalk lanes), else (b).
4. **Right turns:** today's lane-centre clothoids already run inside the
   curb return (with sidewalks the trims grow and so do the right-turn
   radii, about 5 m at a 90° street corner); a new check enforces it.

Effort: 1.5 d.

### Lane use, turn pockets and turn restrictions

`plan_movements` already is a rule-based lane-link solver (JunctionArt Stage
3: every incoming lane leads somewhere, no crossings within an approach,
split strategies). Missing:

- **Explicit lane use:** a per-approach assignment (lane → set of
  movements: L, L+S, S, S+R, R, U) that the movements are generated from,
  recorded in the sidecar (`lane_use`) and drawn as arrows. Small, and it
  gives the training data lane-level routing rules. 1 d.
- **Turn pockets** (left-turn bays; right-turn lanes optional): the
  builder adds an innermost lane over the last `pocket_length` of an
  approach (AASHTO storage + deceleration: 30–60 m urban), with a taper,
  where the junction has lights or priority control and the approach is long
  enough. The pocket is left-only; protected left phases use it. Needs the
  lane-role generalisation from section 1 (ids shift by one in the pocket's
  section) and widens the junction by one lane on that arm, which must be
  measured. With medians the pocket takes the median's width instead of
  widening the road. 2.5 d.
- **Turn restrictions** (banned turns): no left turn from a minor road
  onto a 2+2 or wider highway at grade (right-in / right-out), plus a small
  seeded share elsewhere, signed with prescribed-direction signs. Only where
  the lane-level graph stays strongly connected (the connectivity validator
  checks it). 1 d.

Options for the scope: (a) corner radii + explicit lane use + arrows;
(b) + turn pockets; (c) + medians; (d) + banned turns. Recommendation: all
four, ordered so that each is usable on its own (question 4).

## 8 · Default on or off, history and tests

Options:

- **(a) Everything off by default; opt in with `--hd` or a profile.**
  Outputs, tests and replays unchanged. But the stage's whole point (AV-ready
  maps) is hidden behind a flag, every example needs it, and users get
  driving-only maps by default.
- **(b) Off while it is built, flipped on in the last phase
  (recommended).** Each phase lands behind its own switch, tests run both
  ways, and the final phase turns the default to the `urban` profile with
  marks, signs and lights for the DE profile, in one commit with its own
  version stamp (`hdmap_version` in the sidecar, like `geometry_version`).
  `--no-hd` (or `--set hdmap.enabled=false`) gives today's output.
- **(c) Split:** marks, signs and lights on by default (they add elements but
  move nothing), cross-sections off (they change trims, curves and the
  repair). Smaller change, but crosswalks need sidewalks, so the default map
  would have signals without crosswalks.

What changes with (b):

- **History replays:** `run.yaml` stores only what differs from the
  defaults, and replay "runs the spec against the current defaults and code"
  (`history.py`). So after the flip, replaying an old run gives the HD
  output, and a new run of the same command lands in the same `out/<hash>/`
  folder (same spec, same hash) and replaces it. That is the current design
  (the hash identifies inputs, not code). To reproduce an old output exactly,
  replay with `--set hdmap.enabled=false`. The `hdmap_version` in the sidecar
  tells the two apart. Recording the git commit in `run.yaml` (review part
  F) would make this explicit; not part of stage B unless you want it.
- **Tests:** while off, nothing changes. At the flip, tests that assert lane
  counts, mark types or file contents of default builds
  (`test_opendrive.py::test_road_marks_2_2`, parts of `test_junctions.py`,
  `test_scenario.py`) move to an explicit `hdmap.enabled=false` or are
  updated; the identity test is unaffected (it checks generator output).
- **Benchmarks:** `.xodr` files grow (signals, objects, more lanes);
  `bench` and `simulate` timings must be re-measured.

## 9 · Validation, metrics and visual checks

New validator `roadgen/validate/hdmap.py`, run by `build_and_validate`:

- every signal and object: 0 ≤ s ≤ road length; |t| within the road's outer
  edge at s (+ 2 m roadside for signs); validity lanes exist at s; ids unique;
- every controlled approach has a stop or yield line and a sign or light
  facing it (orientation matches the travel direction) within 5 m of the
  road end; roundabout entries and diamond terminals included;
- every light belongs to a controller, every controller to a junction, every
  signal plan covers its lights, and no two conflicting movements are green
  in the same phase (permissive lefts excepted);
- `<priority>` records reference connecting roads of their own junction;
- no `<roadMark>` on connecting roads; no road mark on sidewalk lanes;
- lane types are consistent across every lane link (driving ↔ driving,
  sidewalk ↔ sidewalk, …), connecting roads of movements are driving only,
  no junction connection starts or ends in a non-driving lane;
- right-turn paths stay inside the curb returns.

`metrics` (new `roadgen/metrics/hdmap.py`, `metrics --hd`): length of each
lane type per km of road, marks by type and colour, signals and objects per
km and per junction by type, junction control mix (share of none / yield /
stop / all-way / lights / roundabout), lights and phases per signalised
junction, crosswalks per junction, share of approaches with turn pockets
and lane-use arrows.

Visual checks:

- **Plots:** `plot-graph` only sees the graph. Options: (a) a new
  `plot-xodr` command that draws the built lanes coloured by type, the marks,
  crosswalks, stop lines and a glyph per sign and light, from the `.xodr`
  and the sidecar (reuses the validators' road evaluator); (b) overlays on
  `plot-graph` from the sidecar (control glyphs only). Recommendation: (a),
  1.5 d; the pipeline writes `PREFIX_hd.png` next to `PREFIX.png`.
- **esmini (`view`):** esmini draws lane types, marks and the objects it
  understands; B0 records what it shows.
- **Scene:** a new `scene/streetscape.py` mesh: raised sidewalk slabs and
  curb faces (unless esmini already raises `<height>` lanes), corner fills
  where there are no sidewalk connecting roads, and simple posts for signs
  esmini has no model for. Written into the same `.osgt` with the ground
  and buildings, and also without maps (flat), with `--buildings` or a new
  `--streetscape` flag. Buildings move behind the sidewalks (section 2).
  2 d.

## Paper techniques used

- **StreetGen (Cura et al., file5)** §2.3: streets as constant sections and
  transition parts; corner radius from road type and speed (rule of thumb,
  SETRA); §2.4: arc centres at distance w + r from both axes, r = min(r1,
  r2); §2.5: shrink the radius when the axis is too short, fall back to a
  tiny radius when an arc is misplaced; border points farthest from the
  centre define the junction surface; variable-width transitions (we have
  them as tapers); lanes and markings from the section; crosswalks along
  the border lines; city blocks as the complement of road and junction
  surfaces (buildings behind sidewalks); §2.7: one-way lanes, interconnections
  only between opposite directions, no U-turns by default; §2.8: roundabouts
  need different traffic rules from junctions (our yield entries); §2.9:
  objects placed by curvilinear abscissa relative to the axis or the
  sidewalk; the pedestrian crossing as a specialised object with a width and
  a surface from sidewalk to sidewalk.
- **JunctionArt (Muktadir et al., file10)** Stage 3: incident points moved
  outward until lanes fit (our trims), every incoming lane leads to at least
  one outgoing lane, no merging or crossing inside the junction, split-first /
  split-last strategies (C5's `lane_split`), roads with turn lanes and a
  median type; Stage 4: lane-count mismatches repaired by splitting or
  merging lanes, marks removed inside junctions, solid yellow centre lines,
  solid lines near junctions.
- **Where the papers are silent:** ASAM OpenDRIVE 1.6 (lane types, `<height>`,
  `roadMark` with `laneChange`, signals with `country`/`type`/`subtype` and
  `<validity>`, `<controller>`, junction `<priority>`, objects with outlines
  and markings); OSI 3 (TrafficSign, TrafficLight, lane classification and
  boundaries, as esmini writes them); MUTCD 11th ed. (Part 2B regulatory
  signs, Part 3 markings: yellow centre lines, stop lines, Part 4 signal
  timing); Vienna Convention 1968 and the German StVO catalogue; AASHTO Green
  Book (curb return radii, shoulders, turn-lane lengths); ITE yellow-interval
  formula; RASt 06 / EAE (German cross-sections).

## Risks

| Risk | Handling |
|---|---|
| esmini renders some lane types, `<height>` or objects badly, or odrviewer traffic uses shoulders | B0 spike on small fixtures before anything is built on it; fall back to the scene mesh, and keep shoulders out of odrviewer traffic by type if needed |
| esmini ignores RoadNetwork signal controllers | storyboard fallback (section 4) |
| Role-based lane links break stage A tapers or the drive-check | the lane-role generalisation comes first and is tested with side lanes off (identical output) and on |
| Wider trims make more stub roads and more advisory corners | measured above; repair pass; metrics before / after in every phase |
| `<priority>` records blow up file size | measured in B5; parameter to leave them out |
| Too many lights or stops make every city alike | density thresholds calibrated in B0 on the measured densities; control mix in `metrics` |
| Banned turns trap traffic | applied only where the lane graph stays strongly connected |
| Large outputs slow `simulate` / `bench` | timings re-measured before the default flip |

## Tentative phases (detailed in the implementation plan)

| Phase | Content | Effort |
|---|---|---|
| B0 | HD metrics (zero baseline) in `plans/baselines/`; esmini 3.9 / OSI spike on small fixtures (lane types, heights, marks, objects, signals, controllers, odrviewer traffic); calibrate densities | 2 d |
| B1 | `geometry/cross_section.py`, `Edge.cross_section`, lane-role generalisation, trims with curb lines and corner fillets (shrink to fit), cross-section-aware repair | 3 d |
| B2 | Builder side lanes: profiles, role links, parking / shoulder ends, one-way, bridges, tunnels, roundabouts, medians; `plot-xodr` | 3.5 d |
| B3 | Knock-on: alignment clearance, ground, buildings, drape, grade separation; `scene/streetscape.py`; sidewalk corners | 2.5 d |
| B4 | Road marks per country profile, no-lane-change zones, edge / bike lines | 1.5 d |
| B5 | Junction control, priority road, signs, stop / yield lines, `<priority>`, sidecar right of way, roundabout entries, diamond terminals, `annotate` step | 3.5 d |
| B6 | Lights, controllers, phase plans, xosc `TrafficSignalController` (or fallback), pedestrian lights | 3 d |
| B7 | Crosswalks, speed and curve signs | 1.5 d |
| B8 | Junction realism: lane-use solver + arrows, turn pockets, banned turns (scope per question 4) | 2–4.5 d |
| B9 | `validate/hdmap.py` and `metrics --hd` completed (each phase adds its own checks as it goes) | 1.5 d |
| B10 | Default flip (if chosen), docs, examples, final measurement, supervisor summary | 2 d |

About 26–29 days (5–6 weeks), depending on the B8 scope.

## Questions for you

1. **Country profile:** DE (Vienna / StVO codes, white centre lines, typed
   OSI signs in esmini) as the default with US (MUTCD, yellow centre lines,
   all-way stops) as an option (recommended); US only; or DE only?
2. **Default at the end of stage B:** flip to on in the last phase
   (recommended); keep everything opt-in; or marks and signs on with
   cross-sections off?
3. **Light phases in OpenSCENARIO:** `TrafficSignalController` in the
   `.xosc` RoadNetwork with a storyboard fallback (recommended); OpenDRIVE
   controllers and the sidecar only; or also pedestrian lights?
4. **Scope of junction realism (B8):** corner radii and explicit lane use
   with arrows are always in; which of turn pockets, medians and banned
   turns should stage B include?

## Decisions

The user's answers to the four questions, verbatim (2026-10-29):

- Country profile: "DE only"
- Default at the end of stage B: "Flip on in last phase (Recommended)"
- Light phases: "xosc controller + fallback (Recommended)"
- B8 scope: "Turn pockets, Medians, Banned turns"

| # | Question | Decision | By |
|---|---|---|---|
| 1 | Country profile | **DE only** (StVO / Vienna Convention catalogue, `country="DE"`; lights `country="OpenDRIVE"` 1000001). Consequences: centre lines are **white** (Z 340 broken, Z 295 solid on approaches and multi-lane two-way roads), not the review's yellow; street × street junctions are right-before-left (no all-way stops, which German law does not have); no US tables. | user |
| 2 | Default output | **Off while built, flipped on in the last phase** (`urban` profile, marks, signs, lights), with `hdmap_version` in the sidecar and `--no-hd` for today's output. Replays of old runs then give HD output (section 8). | user |
| 3 | Light phases | **Phase plans in the sidecar and OpenDRIVE `<controller>`, plus an OpenSCENARIO `TrafficSignalController` per junction in every `.xosc`**; storyboard events if esmini 3.9 does not cycle controllers. No pedestrian lights. | user |
| 4 | B8 scope | **All of it:** corner radii, explicit lane use with arrows, **turn pockets, medians, banned turns** | user |
| 5 | Where decisions live | Builder derives; optional `Edge.cross_section` and `Node.control` overrides; map-dependent values written by a new `annotate` pipeline step, never by generators | Claude |
| 6 | Cross-section params | Neutral `geometry/cross_section.py`, profiles `urban` / `suburban` / `rural` / `none` per class, widths as in section 1 | Claude |
| 7 | Too-short edges | Parking and ramp-node shoulders end before junctions, corner radii shrink to fit, cross-section-aware repair in the pipeline | Claude |
| 8 | Control type | Class-pair rules + density thresholds from `annotate`; roundabout entries yield; diamond terminals lights or stop | Claude |
| 9 | Right of way in OpenDRIVE | Signs and lights with validity, `<priority>` records for conflicting connecting roads (size-checked), right of way per movement in the sidecar | Claude |
| 10 | Stop lines | DE 294 signal + `roadMark` object; final form after the B0 spike. **After B0 (2026-10-30): `roadMark` object with a corner-reference marking only**, since esmini has no OSI type for 294 and CARLA does not know it (see the B0 result in the plan) | Claude |
| 11 | Plots | New `plot-xodr` command | Claude |
| 12 | Randomness | Builder gets the pipeline seed; salted sub-streams per feature | Claude |
| 13 | Speed signs | DE 274 at every speed change and after junctions on roads > 100 m; curve warnings 103 / 105 before advisory corners, with a 274 rounded down to 10 km/h where the advisory is at least 10 km/h below the limit | Claude |
