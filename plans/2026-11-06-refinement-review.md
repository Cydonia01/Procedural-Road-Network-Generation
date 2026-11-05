# Refinement review: natural networks, esmini look, what to add or drop

Review of stages A, C and B before any new feature. It explains:

- the six esmini screenshots;
- what is missing;
- what can go;
- where the generation time goes.

Nothing under `roadgen/` changes in this step. The implementation plan for the
refinement stage (called stage R below) will be its own file.

The online version is the artifact "Roadgen Refinement Review",
<https://claude.ai/artifact/HpT8AA74KFdbvrLUUz4uh6>, built from this file. Its source
is `plans/artifacts/refinement-review.html`.

- Screenshots: [`images/2026-11-06-refinement/`](images/2026-11-06-refinement/).
- Numbers: [`scripts/refinement_probe.py`](scripts/refinement_probe.py).

## Prompts

**2026-11-06, prompt 1**

> Now, we need to stop and revise what is implement until here. you can see the last implementations in plans folder. Before going into the new implementations, we need to improve and refine what is implemented. Firstly, analyze the repo and implementations in plans and define what is absent and what should we do explicity (like getting textures for hd map) to improve it or what is unnecessary. Besides that, I have added some screenshots which shows some unwanted illustrations in esmini:
> - in 1.png, the road is below the ground and changin from two lines from 4 lines is unnatural. This is because at the very beginning of the generator implementation, we decided to make vertical roads (or horizontal roads, i dont remember) thicker. Discuss this decision and propose better options.
> - in 2.png, the problem is the same above.
> - in 3.png, you can see the texture of the junctions are different. Analyze why it is like this.
> - in 4.png, we again see that some of the roads are under gorund in texture. If it is because of the limitations of esmini, we can leave it as it is because we plan to use blender later.
> - in 5.png, there is roads that is not connected anywhere.
> - in 6.png, a road intersects with another road unnaturally.
>
> The main aim of this improvements is to make the roads (or city network) more natural. In this stage, just give me an explanation of absences and what is absent (even solutions maybe). Also, you can analyze generation performance and propose ways to optimize it. Create an artifact.

**2026-11-06, prompt 2** (after reading the first draft)

> for hierarchy option, A is ok. The manhattan-like grids are not the main aim of the project. It was only the starting point, so we should not shape the implementation around it.
> I said it will be exported to blender in future but I want also to see fixed in esmini. Do which option is better. If the functionality will break, then the aesthetics can be in second place.
> Save also thie review file and create a pdf file artifact online like Roadgen AV Readiness Review.pdf file. It should contain what is needed to be implemented.

## Decisions

| # | Question | Decision | By |
|---|---|---|---|
| 1 | How to pick lanes per road | **A: role-based hierarchy from strokes** (below). The axis rule (`major_*` along local y, `minor_*` along local x) goes. Manhattan-like grids are not shaped for; their grid lines become straight strokes anyway. | user |
| 2 | esmini or Blender | **Fix it in esmini too.** Blender comes later and reuses the same meshes. Where a fix could break driving, validation or the HD data, the look comes second. | user |
| 3 | lsystem identity fingerprints (`tests/test_lsystem_identity.py`) | The hierarchy changes lane counts, so the fingerprints are re-recorded once in R1 and the CLAUDE.md rule "without maps lsystem output must stay identical" is updated. Keeping the old rule would mean shaping the code around the grid presets, which decision 1 rules out. `--no-hd` byte identity (`test_hdmap_identity.py`) stays. | Claude |
| 4 | Junction colour (screenshot 3) | Draw the junction fill in our scene mesh with esmini's asphalt colour, and write the `patch` objects only when no scene is written. The other options change the OpenDRIVE lanes or junctions, which the drive-check and validators read. Decision 2 puts function first, so the fix lives in the purely visual scene. Nothing else in `roadgen/` reads the patches. | Claude |
| 5 | Ground under roads (screenshots 1, 2, 4) | Two steps. First the cheap levelling fixes. Then cut the road and junction footprints out of the ground and stitch the ground to the road edges. Both touch only the scene file, so driving is safe, and Blender gets the same mesh later. | Claude |
| 6 | Order | R1 hierarchy and lane continuity → R2 width-aware growth → R3 dead ends → R4 ground and structures → R5 junction look → R6 buildings → R7 materials and Blender export. Performance (P1–P6) runs alongside, with each step measured before and after. | Claude, open to change |

## Measurements

Measured with `plans/scripts/refinement_probe.py` on the saved example graphs in `out/`.

- A *straight continuation* is two roads meeting at a node with less than 30° deflection.
- An *overlap* is two straight road bodies (driving width) that intersect by more than 1 m² without sharing a node.

| Network | Nodes | Dead ends | Straight continuations | …with a lane-count jump | …with a class change | Body overlaps |
|---|---|---|---|---|---|---|
| city (seed 7, maps) | 605 | 88 (14.5 %) | 477 | 3 | 12 | 10 |
| mixed_city (seed 1, maps) | 707 | 79 (11.2 %) | 539 | 36 | 37 | 4 |
| hill_town (seed 1, maps) | 1035 | 117 (11.3 %) | 865 | 14 | 14 | 5 |
| paris | 312 | 26 (8.3 %) | 314 | 4 | 4 | 2 |
| suburb (seed 1) | 475 | 30 (6.3 %) | 280 | 0 | 0 | 0 |

Suburb dead ends include the intended cul-de-sacs.

Performance figures are from `plans/baselines/2026-10-31-stage-b-b9-perf.json`.

## Findings per screenshot

### 1, 2: lanes jump from 4 to 2, ground over the road

**Where the decision came from.** The Phase 3–4 prompts (`copilot_prompts.md:199-243`) set:

- avenues `(2, 2)` and streets `(1, 1)`;
- later, 4-lane one-way avenues and 2-lane one-way streets.

lsystem kept this as an axis rule: roads along local y are avenues (`major_lanes`) and roads along local x are streets (`minor_lanes`) (`roadgen/defaults.yaml:30-31`, `roadgen/generators/lsystem.py:1733-1749`). The class therefore says which way a road points, not what it does in the network.

**How the jumps happen.**

- A street proposal snaps onto an avenue end within `snap_radius` 15 m (`resolve`, `lsystem.py:974-1012`) and carries the line on almost straight.
- If the travel directions differ, `chains.is_mergeable` keeps that two-road node as a junction. `straight_pairs` and `continuation_pairs` (`roadgen/opendrive/junctions.py:234-253`) then leave the surplus lanes unconnected, or merge them into the outer lane, with no taper.
- Where the node does merge, the taper is capped at half the edge (`MAX_TAPER_SHARE`, `roadgen/geometry/profiles.py:78`). On short edges it is abrupt, and it often falls inside a curve.
- Streets spawned from highways and interchange crossings take their class from their heading (`lsystem.py:1108,1316`). The connector pass always adds streets (`:926`).

**Options.**

- **A, chosen: role-based hierarchy.**
  - After growth, group edges into *strokes*: the continuation through a node with the smallest deflection, if under ~35°.
  - Rank strokes by length × edge betweenness (networkx), optionally weighted by mean population.
  - Assign highway / arterial / collector / local by rank share, so lanes are constant along a stroke.
- B: keep the axes but give the class per grid line. Dropped by decision 1.
- C: lanes from demand on the population map. Can be added to A later as a weight.

**Ground over the road.** These are our ground mesh's errors, not esmini's (`roadgen/scene/ground.py`).

- **Corners left at terrain height.** The 20 m grid (`defaults.yaml:245`) is levelled only at vertices within `half width + road_margin + cell` of the centre line (`ground.py:82,118`). A triangle that covers the road edge can have a corner up to `cell·√2` away. That corner keeps its terrain height and lifts the triangle through the asphalt where the road runs in a cut.
- **Banking extrapolated.** Banking is added to vertices up to ~22 m beyond the edge (`ground.py:135`). A flat triangle misses the warped banked surface by about 28 m × 0.06 / 4 ≈ 0.4 m, which is more than `below_road` 0.3 m.
- **Sag curves.** On sag vertical curves the triangle's chord lies above the road.

### 3: junction surface a lighter grey

- With HD on, holes in a junction's lane union become `<object type="patch">` asphalt outlines (`roadgen/opendrive/builder.py:682-760`). They sit 0.02 m above the junction plane.
- esmini draws outline objects as plain coloured geometry, not with the lane surface material, so the patch reads lighter. The shape matches the patch in the screenshot.
- Sidewalk and curb lanes of the corner roads add the lighter bands at the corners.

### 4: road under the ground at the water

- Under bridge spans the ground is left at terrain height (`ground.py:126`). Land strips inside or next to a span, and coarse shore cells, can stand above the deck.
- esmini draws no deck, piers or tunnel portals. That part is an esmini limitation.
- The ground part is ours and cheap to fix.
- The stair-step shoreline comes from colouring each vertex by the nearest terrain class on a coarse grid.

### 5: roads that lead nowhere

**Sources.**

- Streets stop at water or vegetation (`legal_street`, `lsystem.py:1485-1500`).
- A continuation that fails leaves a free end.
- Suburb streets end on purpose.

**Why nothing removes them.**

- The connector pass is off (`detour_ratio: 0.0`).
- Cul-de-sacs are off in grid presets (`culdesac_prob: 0.0`).
- `repair` never removes a dead end (`repair.py:460-466`).
- `--allow-traps` turns the trap report into a warning (`cli.py:77-85`).

Result: 11–15 % of nodes in the map-based cities are dead ends.

### 6: one road runs through another

- Growth tests centre lines only:
  - zero-width crossing test (`_segment_hit`);
  - 15 m snap;
  - 30° minimum angle (`acceptable`, `lsystem.py:1014-1051`).
- Nothing checks the spacing between near-parallel roads, or road width. Two roads can run side by side or meet in a narrow Y with overlapping bodies.
- `_clearance_check` (`roadgen/geometry/alignment.py:297-335`) rejects only contacts that the curve fit adds.
- `validate/clearance.py` sees only centre lines that cross.

## What is missing

1. **Road hierarchy and lane continuity.** The largest gap for natural networks (R1).
2. **Width-aware growth.** Spacing, overlap and merge checks during growth, and a validator for body overlaps (R2).
3. **Dead-end policy.** Connect, trim or turn around (R3).
4. **Ground–road integration.**
   - A footprint cut.
   - Sidewalks and curbs as raised geometry; the 20 m mesh cannot show a 2 m sidewalk.
   - Embankment and cut slopes.
   - Bridge decks, piers and tunnel portals in the scene (R4).
5. **Buildings.** Today there is one rounded block-sized blob per block, 12–60 m tall (screenshots 1, 2, 6). Needed: parcels, frontage, setbacks, heights from population, and empty parks (R6).
6. **Materials and textures: none.**
   - Colours are per vertex only (`roadgen/scene/osgt.py`).
   - esmini's road look comes from its own road geometry. First check whether the esmini model and texture resources are installed; flat colours may mean they are missing.
   - For Blender: our own road mesh per lane type with UVs = (s, t), a material id per lane type, marks as decals from `<roadMark>`, and CC0 textures (ambientCG / Poly Haven) (R7).
7. **Quality gates.** Dead-end share, lane-count jumps, class changes, body overlaps and ground poke-through as metrics. Without them naturalness cannot be tracked over seeds.
8. **Already-logged open items.**
   - Divided carriageways.
   - Trumpet interchanges.
   - Roundabout splitter islands.
   - Roundabout spur failures (`mixed_city`, 10/200 seeds with HD off).
   - The interchange drive-check.
   - No cloverleafs from lsystem.
   - `city` seed 7 has no street × street junctions.
9. **Comparison with real-city statistics** (review PDF §D).

## What can go or be simplified

- **The axis rule for lanes** (decision 1).
- **Junction `patch` objects** when a scene is written (decision 4).
- **Corner roads for esmini runs.** They cost ~27 s of `city`'s esmini load. They become optional once the scene mesh draws the sidewalks round the corners. Keep them for HD output.
- **The HD lane plot on every pipeline run** (4.5 s). Make it opt-in.
- **Seven re-parses of the written `.xodr`** in validation (P2).

## Performance

`city` with HD takes 39 s of Python. The main steps:

| Step | Time |
|---|---|
| fit | 6.8 s |
| build | 7.0 s |
| write | 5.0 s |
| validate | 9.4 s |
| lane plot | 4.5 s |

`mixed_city` takes 61 s. esmini load takes 49–137 s.

| # | Change | Where | Expected |
|---|---|---|---|
| P1 | Adjacency index. `incident_edges` sorts all edges on every call, so node loops are O(N·E log E). | `roadgen/graph/model.py:89-105`, `chains.py`, `junction_space.py` | align and fit 30–50 % faster |
| P2 | Parse the written file once and pass the tree to every validator. | `cli.py:145-155`, `validate/*` | validate 9 → 3–5 s |
| P3 | Vectorize `RoadEval` and `lane_polygons`. | `validate/continuity.py`, `opendrive/plot.py:84` | 3–5 s (validate, lane plot, junction fill) |
| P4 | Compute `network_alignment` once per graph and pass it on. | align, drape, repair, plot, build, ground, buildings | 1–3 s per reuse |
| P5 | Repair checks local to the two end nodes. | `repair.py:161-176` | most of the remaining fit time |
| P6 | Sweeps: `--jobs` default = CPU count; no plots. Corner roads optional for esmini. Round coordinates. | `metrics/sweep.py`, builder | sweeps, esmini load |

These are estimates from reading the code. Each step is measured against the B9 baseline.
