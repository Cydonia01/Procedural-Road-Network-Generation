# Stage A (geometry accuracy): summary for presentation

A short, non-technical account of what stage A changed, the pipeline as it
is now, and how to compare outputs before and after. Details and numbers:
[discussion](2026-10-23-stage-a-geometry-discussion.md) and
[implementation plan](2026-10-23-stage-a-geometry-implementation.md).

## Prompts

**2026-10-24**

> Good implementation overall. Now, I need to ask something more. Firstly, what is alignment_summary.pt, bend_room.py, and trim_failures.py? Why they aren't committed ? Aside from that, can you explain in a markdown file what is implement without giving not much technical details. Explain as if you are telling the supervisor what is done or presenting. Also, explain in short the whole pipeline again in short. Lastly, tell me which outputs should I compare before and after (same seed).

## The problem

Our generated road networks were valid OpenDRIVE files, but their road
shapes were not realistic enough to train self-driving models on. The
review found four main problems:

- **Every bend in a road was treated as an intersection.** About a quarter
  of all "junctions" were really just curves or places where a road gets
  wider or narrower.
- **Curves were far too sharp for their speed limits.** Driving them at the
  posted speed would have taken more than 1 g of sideways acceleration;
  a comfortable limit is about 0.3 g (3 m/s²).
- **Steering would jump.** Wherever a road met a junction, its curvature
  changed instantly, which real roads never do, so a model would learn
  jerky steering.
- **About 1 in 20 generated cities could not be built at all.**

## What was done

**1. A way to measure it.** A new `metrics` command scores any network: how
hard its curves are to drive, how many fake junctions it has, and where its
curvature jumps. It can also generate hundreds of cities and count how many
fail. Every change below was checked against these numbers.

**2. Bends became real curves.** A road that simply bends is now one road
instead of being cut by a junction. Each bend is built the way civil
engineers design roads (AASHTO rules): a straight, a gradual transition
curve, a circular arc, another transition, and a straight. The curve is as
wide as the speed limit needs. Where the city layout leaves no room for
that, the curve is made tighter and the road gets a lower **advisory speed**,
like the yellow curve-warning signs on real roads.

**3. Lanes that start or end now taper.** Where a road changes from two
lanes to one, the extra lane narrows smoothly over a realistic distance
instead of the road splitting at a fake junction.

**4. Junction turns are smooth.** The paths cars follow through
intersections are now curves whose curvature builds up and fades out
gradually, so steering is continuous everywhere. Each turn also gets a
sensible speed from how tight it is (about 20–25 km/h for a city corner).

**5. Curves are banked and intersections follow the slope.** On hilly maps,
curves tilt inwards like real roads, and an intersection on a hillside lies
on a gentle slope instead of being forced flat.

**6. Everything else follows the new roads.** The terrain, bridges, 3D
ground and buildings are now placed along the curved roads, so no building
sits on a road and the ground never covers one. If a curve would cut into a
lake or up a steep slope, it is made tighter so that it stays on legal
ground.

**7. Every city can be built.** One cause of the failures was a bug in how
much space an intersection needs; the rest are fixed by a small repair step
after generation that merges or removes the rare road too short to fit
between two intersections. Cities that worked before are unchanged.

**8. Curved streets can be generated.** The city generator can now grow
streets that drift and bend gently, or that follow the contour lines of
hills (like San Francisco), instead of only straight grids. The old
straight-grid behaviour is still the default.

**9. Stronger checks.** Besides the existing checks, every network is now
checked for smooth curvature and for curves too sharp for their speed, and
the esmini simulator drives every lane of every road end to end to confirm
it reads our roads exactly as we built them.

## Results

On the example cities, before → after:

| | Before | After |
|---|---|---|
| Junctions that were really just bends | 24–38 % | 0–2 % |
| Sideways acceleration needed on the worst curves | more than 3 g | at most 0.3 g |
| Sudden curvature changes at intersections | almost every connection | none |
| Cities that fail to build (200 tried) | 9 | 0 |
| Simulator drive test | intersections only | every intersection and every lane passes |

Two honest caveats:

- Most 90° corners in grid-like cities still need an advisory speed (about
  70 % of the limit, e.g. 35 km/h on a 50 km/h road), because city blocks
  are too short for a full-speed curve. That matches real roads; the new
  curved-street generator is the way to get faster curves.
- Building and checking a network takes a little longer (about 2 s instead
  of 1 s for a large city).

## The pipeline in short

1. **Input maps** (optional): water and land, terrain height and population
   density, generated or read from images.
2. **Generator**: grows a road network (a Manhattan grid, or an L-system
   city that follows the maps) and stores it as a graph of nodes and roads.
3. **Align and drape** (with maps): bends that would leave legal ground are
   tightened, and every road gets heights and bridges from the terrain.
4. **OpenDRIVE builder**: turns the graph into an `.xodr` road network:
   curved roads with lanes, speeds and banking, and intersections with one
   smooth path per lane movement.
5. **Validators**: check the file's structure, smoothness, drivability, and
   drive every road and turn in esmini.
6. **3D scene** (optional): ground and buildings for the simulator's view.
7. **Scenario builder and esmini**: hundreds to thousands of cars driven in
   the simulator, with timing and performance measured.

## Comparing outputs before and after (same seed)

The road *layout* a generator produces for a seed did not change (only
cities that used to fail are repaired); what changed is how that layout is
turned into roads. So compare the outputs from the OpenDRIVE stage on.

**Make the "before" outputs** from the last commit before stage A
(`38a01b9`) in a separate working copy, with the same command and seed:

```bash
git worktree add ../roadgen-before 38a01b9
cd ../roadgen-before
docker compose run --rm -T dev python -m cli --no-history pipeline lsystem --preset city \
    --maps --seed 1 --allow-traps --ground --buildings --drive-check -o out/cmp/city
cd -   # back to the current code
docker compose run --rm -T dev python -m cli --no-history pipeline lsystem --preset city \
    --maps --seed 1 --allow-traps --ground --buildings --drive-check -o out/cmp/city
```

(A smaller case: `pipeline manhattan --preset manhattan_like --seed 1`, or
`pipeline lsystem --preset grid_organic --seed 2 --allow-traps`.)

**What to compare:**

| Output | What to look for |
|---|---|
| `city.png` (the plot) | Bends drawn as smooth curves instead of sharp corners |
| The log line `wrote city.xodr (N roads, M junctions, K connecting roads)` | Fewer roads and junctions: bends are no longer junctions |
| `metrics` on both `.xodr` files (run it from the current code on both: `python -m cli metrics ../roadgen-before/out/cmp/city.xodr out/cmp/city.xodr --json out/cmp/metrics.json`) | Share of two-road junctions, sideways acceleration (bend / turn / road), curvature jumps |
| `odrviewer` view of both (`python -m cli view …xodr`, GUI service) | Curves, lane tapers, smooth turns, cars no longer jerking at junctions |
| `city.roadmap.json` | New `corners` list with each bend's radius and advisory speed |
| Drive-check line in the log | Before: junction movements only; after: movements and every lane |
| `city_scene.osgt` in the viewer (hilly maps) | Ground and buildings follow the curved, banked roads |
| `simulate` on a scenario with the same seed (`scenario … --seed 1`, then `simulate`) | Errors, real-time factor and load time on the smaller number of roads |
| `city.json` (the graph) | Same nodes and edges; only heights differ slightly near bends, and `radius_cap` appears where `align` tightened a bend |

The before/after metrics of the example networks are already saved in
[`baselines/`](baselines/) (`2026-10-23-stage-a-before-*`,
`2026-10-24-stage-a-after-*`).

## Helper scripts in `plans/scripts/`

These are small one-off analysis scripts that produced numbers quoted in
the plans; they are not part of the program:

- `bend_room.py`: the first estimate of how much room each bend has for a
  full-speed curve (used in the discussion).
- `trim_failures.py`: found which intersections caused the build failures,
  which led to the junction-space bug fix.
- `alignment_summary.py`: summarises the fitted curves of a network (how
  many corners, how many shrunk, how many advisory speeds).

They live in `plans/` because the project rules (CLAUDE.md) put the scripts
behind plan numbers next to the plans, and they were not committed because
you asked to leave `plans/` out of the commits.
