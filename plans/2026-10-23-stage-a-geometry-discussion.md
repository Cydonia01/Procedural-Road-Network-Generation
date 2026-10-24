# Stage A (geometry accuracy): discussion and decisions

Source: `Roadgen AV Readiness Review.pdf` (repo root), part **A · Fix first:
Geometry accuracy**. Implementation plan:
[2026-10-23-stage-a-geometry-implementation.md](2026-10-23-stage-a-geometry-implementation.md).

## Prompts

**2026-10-23, prompt 1**

> Read the Roadgen AV Readiness Review.pdf file and create an implementation plan for stage A - Geometry accuracy. This pdf was also created by you in a previous chat. Brainstorm the part A let us discuss the advantages and disadvantages if we implement it.

**2026-10-23, prompt 2**

> I prefer Option 1. Also, couldn't we find a solution for the caveat of the option 1? Decide the values of e and f yourself. for 3, do which is better. Scope includes A5. Firstly, add claude.md that you should create a markdown for each plan and put them into the plans folder. This is for traceability. Also, save prompts if possible. The discussion above should also be in markdown. Then, generate a implementation plan phase by phase. You can use any stack needed (like scipy you mentioned above). Also, you can utilize papers in files (read md files, pdfs are costly). Besides papers, you can use contemporary knowledge you have to make the implementation better.

The prompts of the earlier chat that produced the review PDF were not saved.

## What part A of the review asks for

1. **Merge bends into curved roads.** Build each chain of two-road nodes as
   one road made of line, spiral and arc geometries. Use a minimum radius
   from design speed, `R ≥ v² / (g·(e + f))`, store a lower advisory speed
   where a bend can't be widened, and turn lane-count changes into lane
   sections with width tapers instead of junctions.
2. **Make lsystem aware of junction trims**, so it stops producing edges
   shorter than the trims at their ends.
3. **Grow smooth curved roads** in the generators instead of straight
   segments (tensor-field streamlines, curve smoothing, tortuosity).
4. **Banking and sloped junctions** (already listed as next steps in
   `docs/maps.md`).

Measured in the review: 24–28 % of junctions are bends; median bend radius
12 m; median lateral acceleration at the speed limit 11.6 m/s² (90th
percentile 21 m/s²); 1 in 20 lsystem map + seed pairs cannot be built;
curvature jumps (G1 only) at every junction entry.

## New measurement (2026-10-23)

Script: [`scripts/bend_room.py`](scripts/bend_room.py). For each two-road
node it takes the room a curve can use: half of each neighbouring edge, or
the whole edge minus about 10 m where the other end is a real junction.
Arcs only, no spirals, e + f = 0.22.

| Network | Two-road nodes | Lane-count changes | Real bends that fit R_min at the speed limit | Advisory ÷ limit (median / 10th %ile) |
|---|---|---|---|---|
| `out/examples/net/coast_city.json` | 119 | **70** | 58 of 104 (56 %) | 1.04 / 0.77 |
| `out/examples/pl/city_maps.json` | 167 | **102** | 82 of 151 (54 %) | 1.02 / 0.79 |
| `out/examples/net/organic.json` | 39 | 26 | 30 of 39 (77 %) | 1.19 / 0.83 |

What this means:

- About 60 % of the "bend junctions" are really lane-count changes, so the
  taper work matters as much as curve fitting.

> **Correction (2026-10-24, after A2):** the "about half the bends fit"
> estimate below was too optimistic. It used the slower edge's speed limit,
> e + f = 0.22 and arcs without spirals. Fitted with the faster edge's limit,
> flat roads and AASHTO spirals, 88 of 112 corners on `coast_city` need an
> advisory speed (median 70 % of the design speed); all of them are 60–135°
> bends. See the A2 result in the implementation plan.

- The 12 m median radius comes from the junction trims, not from the road
  layout. Without the junction, about half the bends get a full
  design-speed radius, and even the tightest 10 % only need about 20 %
  lower advisory speeds.

## Where to merge: the two options

- **Option 1: in the builder.** The RoadGraph is unchanged; a two-road node
  becomes a shape point at build time. Generators, drape, run-history
  hashes and the "lsystem output must stay identical without maps" rule are
  unaffected. `Node.kind` gives a per-node override (`"junction"` keeps a
  real junction, `"auto"` allows a merge). Generator curves later become
  "emit two-road nodes along the curve".
- **Option 2: in the graph.** Add `Edge.shape` points and a simplify pass.
  Changes the model, the JSON schema, drape, `scene/`, `connectivity.py`,
  the lsystem rules and every fixture, with no gain over Option 1.

**The caveat of Option 1:** `scene/buildings.py`, `scene/ground.py` and
`maps/drape.py` read roads as straight edges. A fitted curve cuts inside
the corner (about 20 m at a 90° bend with R = 50 m), so buildings, ground
levelling and bridge detection would disagree with the road in the .xodr,
and a curve could reach water that the straight edges avoided.

## Advantages of implementing stage A

- Fixes the largest measured error at its cause: about 25 % fewer
  junctions, design-speed radii on about half of the bends, honest advisory
  speeds on the rest.
- Realistic lane graph: no fake junctions, lane drops as real tapers.
- Gradual curvature through bends, so learned steering profiles are smooth.
- Side benefits: smoother height profiles through bends (no flat step at
  every bend node), smaller files, fewer roads and connecting roads for
  esmini (helps the scalability benchmarks).
- Partly fixes the lsystem build failures for free (bends stop trimming).
- Enables later stages: B's signs and marks need roads measured along their
  length, D's curvature metrics need real curvature, generator curves become
  "emit more nodes".
- Keeps the architecture; the neutral geometry module settles the
  "generators never touch OpenDRIVE code" question.

## Disadvantages and risks

- Outputs change for every network: road ids, counts, sidecar format; old
  `runs/history.json` replays give different `.xodr`; many assertions in
  `test_opendrive.py` and `test_junctions.py` need updating.
- The most complex code in the project: spiral fitting, room limits, chains
  that both curve and change lane count, closed rings, one-way chains whose
  stored direction flips, Fresnel evaluation in the validator.
- Corner cutting moves the road off the graph's straight edges (the caveat
  above).
- Only about half the bends reach the design radius; the rest get advisory
  speeds and still look sharp. The real fix is curves in the generators.
- Junction entries stay G1 until the junction turns are rebuilt with
  clothoids.
- Effort: roughly 1.5–2.5 weeks for review items 1–2; items 3–4 add about
  as much again.

## Decisions

| # | Question | Decision | By |
|---|---|---|---|
| 1 | Where to merge bends | **Option 1**, builder-level, RoadGraph unchanged | user |
| 2 | Values of e and f | **f from the AASHTO speed-dependent side-friction table; e_max 0.04 for streets and avenues, 0.06 for highways.** Gives R_min ≈ 84 m at 50 km/h and ≈ 252 m at 80 km/h (the review's ≈ 90 m and ≈ 250 m). Advisory speeds use the banking actually built (e = 0 until banking lands). | Claude, see below |
| 3 | Keep a flag for the old builder behaviour | **No flag; switch over completely.** Stamp a `geometry_version` in the sidecar and in run history so a replay of an older run warns that the geometry changed. `Node.kind = "junction"` remains the per-node escape hatch. | Claude, see below |
| 4 | Scope | **All of part A**, including junction clothoids, banking, sloped junctions and generator curves | user |
| 5 | Plans and prompts | One Markdown per plan or discussion in `plans/`, prompts saved verbatim (rule added to `CLAUDE.md`) | user |
| 6 | Extra dependencies | `scipy` (Fresnel integrals, root finding) and `pyclothoids` (G2 clothoid solver), both already installed in the dev container through scenariogeneration; declare them explicitly in `pyproject.toml` | user allowed, Claude chose |

### Why these e and f values (decision 2)

- The AASHTO Green Book side-friction factors `f_max(v)` are calibrated on
  driver comfort, not on tyre limits, and fall with speed (0.27 at 32 km/h,
  0.20 at 48 km/h, 0.14 at 80 km/h, 0.10 at 113 km/h). A single constant
  `f` would be too strict for slow streets and too loose for highways.
- `e_max` 0.04 is the usual urban limit (traffic stops on curves, ice);
  0.06 is a moderate value for highways. 0.08 and up is rural practice and
  would bank roads more than a city does.
- At 50 km/h that gives `a_lat = g·(e + f) ≈ 2.3 m/s²`, under the review's
  3 m/s² comfort line. At 30 km/h the table would allow about 3.2 m/s², so
  `f` is capped at `3.0/g − e` and every road stays within 3 m/s².
- Until banking exists (phase A8) the roads are flat, so advisory speeds are
  computed with e = 0. The design radius already targets `e_max`, so roads
  built before A8 need no refit when banking is switched on.

### Why no legacy flag (decision 3)

- The old behaviour has measured defects (fake junctions, > 1 g bends); no
  use case needs it except reproducing old numbers.
- Two geometry paths would double the test matrix of the builder, the
  scene, the drape and the validators for the whole project lifetime.
- Old runs remain readable in `runs/history.json`; the version stamp makes
  "same hash, different geometry" visible instead of silent.
- Anyone who needs a bend to stay a junction can set `Node.kind =
  "junction"` on it.

### Solution for the Option 1 caveat

Three parts, so that every stage sees exactly the same road shape and no
stage needs the maps it doesn't already have:

1. **One source of truth for the road centreline.** A new neutral package
   `roadgen/geometry/` (no OpenDRIVE or scenariogeneration imports)
   computes the *network alignment* from a RoadGraph: chains, fitted line /
   spiral / arc pieces, the station of every node and edge along its road,
   and densely sampled polylines. It is a pure, deterministic function of
   the graph and the design parameters. The builder, `scene/buildings.py`
   (block faces and footprints), `scene/ground.py` (levelling),
   `maps/drape.py` (heights and bridges), `graph/plot.py` and the metrics
   all read it, so buildings, ground and bridges follow the curved road.
2. **Conflicts with other roads are solved inside the alignment.** A corner
   curve may not come closer to another road than the two half-widths plus a
   clearance (shapely `STRtree` query); if it does, that corner's radius is
   reduced. This needs only the graph, so the builder gets the same answer.
3. **Map legality goes into the graph, like heights do.** A new `align`
   step (CLI subcommand, run automatically by `pipeline` when maps exist,
   before `drape`) checks each fitted curve against the terrain and
   elevation maps: no new water crossing, no grade or slope beyond the
   generator's limits. Where a curve fails, it shrinks the radius and
   stores the result as an optional `Node.radius_cap [m]`. The builder never
   sees a map; it just honours the caps. Without maps nothing is written,
   and graph JSON stays identical because unset optional fields are left
   out (`roadgen/graph/io.py::_OPTIONAL`).
