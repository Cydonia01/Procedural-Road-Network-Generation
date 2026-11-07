# What remains after stage R, and open issues from earlier stages

Status review on branch `dev/stage-r` (HEAD 9369fdc). Nothing under
`roadgen/` changes in this step. Sources: every file in `plans/`,
`docs/development.md#known-issues-and-open-ideas`, `Roadgen AV Readiness
Review.pdf`, `runs/examples.sh`, and the user's note
`proje yapılacaklar.txt`.

## Prompts

**2026-11-07, prompt 1**

> after stage-r implementation, tell me what is remain for implementation? look at plans folder. besides that, go into details of open issues from earlier stages.

## Decisions

None taken; this is a status list. Open questions for the user are at the end.

## 1. Remaining in stage R

| Item | State | What is left |
|---|---|---|
| R1 hierarchy | done | 3+-road class changes along straight lines remain (city 11, mixed_city 5, hill_town 10), mostly highway ↔ street. Accepted by R-4, but visible. |
| R2 width-aware growth | done | highway_corridor shows 12 probe overlaps, all grade-separated crossings; the probe does not know heights. |
| R3 dead ends | done, one deviation | The plan says "examples without `--allow-traps`", but `runs/examples.sh:46` still passes it to every example. Either drop it or record why it stays. |
| R4 ground | partly | Press-down and level-out done (0 road samples under the ground). Open: footprint cut with constrained triangulation, ground stitched to the road edges, sidewalks/curbs as raised geometry, embankment/cut slopes, bridge decks, piers, tunnel portals, the stair-step shoreline. |
| R5 junction colour | done | — |
| R6 buildings | done | — |
| R7 materials, Blender export | not started | glTF road mesh per lane type with UVs (s, t), material id per lane type, marks as decals from `<roadMark>`, CC0 texture fetch script (ambientCG / Poly Haven). |
| Q quality gates | done | Ground poke-through is logged by the pipeline but is not in `naturalness` or a test gate. |
| P1 adjacency | done (no index, R-13) | — |
| P2 parse once in validation | open | validate 9.4 s → 3–5 s expected on city. |
| P3 vectorize `RoadEval`, `lane_polygons` | open | 3–5 s (validate, lane plot, junction fill). |
| P4 `network_alignment` once per graph | open | 1–3 s per reuse. |
| P5 repair checks local to the end nodes | open | most of the remaining fit time. |
| P6 rest | open | sweeps `--jobs` = CPU count and no plots; corner roads optional for esmini runs (27 s of city's load); rounded coordinates. Lane plot opt-in is done. |

Not measured yet: the stage R timing against the B9 baseline
(`baselines/2026-10-31-stage-b-b9-perf.json`). The review asked that each P
step is measured before and after.

## 2. Open issues from earlier stages (bugs and unresolved behaviour)

Only things that are wrong or unexplained today, not features never planned.

### Failing or unfinished checks

1. **`tests/test_hdmap_identity.py` fails for manhattan_like, organic_town and
   city_maps** already on 3ae2780 (before stage R). Only `grid` matches the B0
   hashes. The guard that `--no-hd` output is byte-identical to pre-stage-B is
   therefore not working for 3 of 4 cases, and nobody knows which commit broke
   it. Needs a bisect between B0 and 3ae2780, then either a fix or a
   deliberate re-record.
2. **`test_monitor_memory_cap_and_peak` fails on Linux.** Not environmental
   noise: `run_monitored` sets `RLIMIT_AS`, the 300 MB allocation raises
   `MemoryError`, the child exits 1 and is never reported as
   `killed == "memory"`. One-line fix in either the runner or the test
   (`docs/development.md`).
3. **`mixed_city` 200-seed sweep never finished** (stage C exit table: "stopped,
   it takes long; open"). B9 ran it with HD, so build failures are known, but
   the stage C criterion itself was never closed.
4. **Interchange drive-check postponed since C9.** Diamond, cloverleaf and the
   highway_corridor interchange are not driven through esmini's check; it ran
   for minutes and was killed (stage C prompt 4). The examples run
   `--drive-check`, so either it now passes or it is silently slow. Unknown.

### Generator bugs

5. **Roundabout spurs: 10 of 200 `mixed_city` seeds fail with `--no-hd`.** A
   13–24 m avenue spur from a roundabout entry to a tight bend becomes a
   junction at both ends, and the trims (9 + 9 m) exceed it. Repair can
   neither collapse nor remove it. With HD on they build only because the HD
   fit straightens the bend. Stage R changed the roundabout fit (R-12); the
   sweep was not rerun, so the count may have changed.
6. **`city` seed 7 had no street × street junctions,** so right-before-left
   never occurs there. With the stroke hierarchy many former avenues are now
   locals, so this may be fixed; not re-measured.
7. **HD build failures absorbed by "bare road ends"** (B9): city 3 and
   mixed_city 21 of 200 needed side lanes dropped at a junction end. It
   builds, but a sidewalk ends abruptly at those ends.

### Behaviour in esmini

8. **Traffic lights have no effect on traffic.** The lights switch (storyboard
   events, checked by screenshots in B6), but esmini's default controller
   ignores lights, stop and yield signs, so cars drive through red. The
   `TrafficSignalController` in the RoadNetwork is ignored by esmini 3.9
   entirely. For the user the result is "lights do not work"; fixing it needs
   a controller that obeys signals (esmini ACC/ALKS controller, or SUMO
   co-simulation), not a map change.
9. **Cars in a dropped lane are merged by force.** esmini warns and merges the
   car when its lane ends (stage A open point), since the default controller
   never changes lanes. Same root cause as 8.
10. **"No connecting lane" warnings** at run time (3–4 per run in city /
    mixed_city since B4). Never traced to specific junctions.
11. **esmini load time** 49–137 s on city / mixed_city: corner roads are 5 % of
    lane length but 27 s of the load, and the same file sometimes loads three
    times slower. Unexplained; issue draft not filed
    (`2026-10-31-esmini-issue-draft.md`).
12. **Hierarchy roads look untextured** (user's note, not yet reproduced).
    Possible causes to check: esmini not finding `resources/models` from the
    `view` command (the runner counts "Texture file not located" as ignored
    `asset_warnings`), or a lane type the stroke classes produce that esmini
    draws without material.

### Geometry gaps logged in stage A

13. **Advisory speeds on grid corners**: corners too tight for the posted speed
    still get an advisory instead of a wider radius; only curved presets
    avoid it.
14. **`align` does not check cross slope under a curve** (banking on a grade).
15. **Drape has no grade limit**: networks grown without maps, or with
    `max_grade: 0`, can get steep roads when draped on hilly terrain.

### Housekeeping

16. `project-explanation.md` still says the project uses OpenStreetMap, which
    contradicts the project rule.
17. Run history records no git commit, dirty flag or package versions
    (review PDF §F), so `out/<hash>/run.yaml` cannot reproduce a run across
    code changes.
18. Schema check is always skipped (ASAM XSD not redistributable); the ASAM
    Quality Checker was suggested instead.

## 3. After stage R (from the AV readiness review, §D–F)

Stages A, B, C and R cover review sections A–C. Not started:

- **D, measure realism**: per-junction difficulty (field of view, max
  curvature, conflict area, complexity score); comparison with real-city
  statistics (no geometry, only numbers); generate until junction-type and
  curvature bins are filled. Network statistics exist (`metrics/network.py`).
- **E, training data**: OSI ground truth (`--osi_file`), an ego vehicle with
  manoeuvre routes, reactive traffic (fixes issue 8), pedestrians at
  crosswalks, export to Argoverse 2 / Waymo / ScenarioNet, or CARLA for
  perception.
- **F, engineering**: issues 16–18.
- **Generator features logged as "later"**: divided carriageways, trumpet
  interchanges, roundabout splitter islands, cloverleafs from lsystem, right
  turn pockets.

The user's note also lists: per-stage timing in the report, an end-to-end
review, Blender export (R7), free-flight camera, openx-assets, and writing the
report.

## Questions for the user

1. Issue 1 (identity guard): bisect and fix, or re-record the hashes?
2. R3: drop `--allow-traps` from `runs/examples.sh`?
3. Issue 8: is reactive traffic (lights obeyed) part of the next stage, or
   is it enough that the map carries the rules?
4. Next after R: finish R4/R7/P, or start D/E?
