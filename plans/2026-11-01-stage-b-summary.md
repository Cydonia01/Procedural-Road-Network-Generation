# Stage B (HD-map completeness): summary for presentation

A short, non-technical account of what stage B changed and how to see it.
Details, decisions and numbers: [discussion](2026-10-29-stage-b-discussion.md)
and [implementation plan](2026-10-29-stage-b-implementation.md).

## Prompts

**2026-11-01, prompt 14**

> Commit that, then implement B10. Stop after B10 and report, including the final exit-criteria table.

(This summary is the "supervisor summary" step of the plan, phase B10. The
other prompts of stage B are quoted in the discussion and the plan.)

## The problem

After stages A and C the roads had realistic shapes and the cities were
varied, but the maps were not yet *HD maps*, the detailed maps a
self-driving car reads. Every lane was a driving lane: no sidewalks, bike
lanes, parking or shoulders. There were no road markings beyond plain lines,
no signs, no traffic lights and no rules about who goes first at an
intersection. A model trained on such maps never learns to stop at a red
light, give way at a yield sign, or keep off the sidewalk.

## What was done

All of it follows the German rules (StVO and the Vienna Convention: white
markings, German sign numbers).

**1. Streets with everything beside the driving lanes.** Each road now has
the lanes a real street of its kind has: sidewalks with raised curbs, bike
lanes, parking lanes, shoulders, and medians on wide roads. A city street,
a suburban road and a country road each get their own layout, chosen from
how dense the population is where the road runs. Sidewalks continue around
the street corners, and corners are rounded as real kerbs are.

**2. Road markings.** Centre lines, lane lines, edge lines, double lines
where overtaking is forbidden, stop lines, give-way lines, zebra crossings
and arrows on the road that show which lane goes where.

**3. Who goes first.** Every intersection gets a rule. Large crossings get
traffic lights, main roads get priority with give-way or stop signs for the
side streets, quiet residential streets use "right before left", and
roundabouts give way to the traffic already in them. Each rule comes with
its signs and lines, and the map records for every turn which other turns
it must give way to.

**4. Traffic lights that work.** Lights are grouped into phases with a fixed
cycle (green, yellow, all red, and pedestrian phases at crossings). The
cycles are also written into the traffic scenarios, so the lights really
switch in the simulator.

**5. Signs along the road.** Speed limits, curve warnings before sharp
bends, priority-road signs, and signs for banned turns.

**6. Realistic intersections.** Lanes are assigned to directions (left only,
straight or right, ...). Extra left-turn lanes open before busy
intersections, some turns are banned where real designs ban them, and wide
roads keep their median up to the intersection.

**7. Checks for all of it.** New checks make sure each sign stands beside
the road, every controlled approach has its line and its sign or light,
light phases never give green to crossing traffic, and no driving lane ever
leads into a sidewalk. The simulator drives every turn and every lane of
each example to prove the map is drivable.

**8. Roads that no longer fit.** Wider streets need more room at
intersections, so some short blocks became too short. In those cases the
sidewalks and parking end just before the intersection, as on real short
blocks, and turn lanes are left out. Roads are never removed for this.

**9. On by default.** All of this is now switched on for every run. One
switch (`--no-hd`) gives exactly the maps of before, byte for byte, for
comparisons.

## Results

Example networks, before stage B → after (from `plans/baselines/`;
`city` and `mixed_city` are the two large cities on generated maps):

| | Before | After |
|---|---|---|
| Lane types | driving only | driving, sidewalk, curb, bike, parking, shoulder, median |
| Side lanes per km of road (city) | 0 | 3.2 km |
| Signs and lights (city / mixed city) | 0 / 0 | 2,229 / 2,444 (665 / 492 lights) |
| Markings and crossings as objects (city / mixed city) | 0 / 0 | 3,120 / 3,126 |
| Intersections with a right-of-way rule (city) | none | all 350 where three or more roads meet: 163 lights, 187 priority roads (the other 3 junctions join only two roads) |
| Large crossings with lights (avenues and highways) | none | all of them (94 in city, 37 in mixed city) |
| Rules in the mixed city | — | lights, priority with give-way or stop, right before left, roundabouts |
| Vehicles in the simulator off the driving lanes | — | none (30,100 positions checked in each city) |
| Cities that cannot be built (200 tried each) | city 0, mixed city 10 | city 0, mixed city 0 |
| Roads removed to make wider streets fit | — | none (0 on every example) |

What it costs:

| | Before | After |
|---|---|---|
| File size (city) | 18.3 MB | 25.6 MB |
| Building the map (city) | 3.3 s | 6.9 s (about 2×, the target) |
| Whole pipeline without simulator checks (city) | 16 s | 39 s |
| Simulator start-up (city) | 17 s | 53 s |
| Simulation speed (city) | 87× real time | 59× real time |

Honest caveats:

- **The simulator starts up slowly on these maps.** About a minute for
  `city`, mostly the sidewalks around the corners. We found why it is slow
  only from the outside, so there is a draft bug report for the simulator's
  authors (not sent): [issue draft](2026-10-31-esmini-issue-draft.md). The
  same draft reports that loading one and the same file sometimes takes
  three times as long. For speed tests there is a switch that leaves the
  corner sidewalks out.
- **The simulator's own cars ignore red lights and signs.** esmini's
  default driver does not obey them. The lights switch and the rules are in
  the map, ready for the self-driving model.
- **10 of 200 mixed cities fail to build without HD maps** (with HD maps
  they build). It is a gap in how roundabouts are joined to the streets,
  noted for a later stage.
- **The motorway interchanges' drive test** is still postponed, as in
  stage C.

## How to look at it

Every example run is listed in [`runs/README.md`](../runs/README.md) with a
`view` command that opens it in esmini's viewer. The stage B examples are
phase 10 there: the city with its ground and buildings, the same city
without HD maps, Manhattan with lights and arrows, a suburb, and a single
junction. For example:

```bash
docker compose run --rm gui python -m cli view out/<hash>/city_hd.xodr --scenegraph out/<hash>/city_hd_scene.osgt
```

A top view with every lane coloured by type is written next to each map as
`<name>_hd.png`.
