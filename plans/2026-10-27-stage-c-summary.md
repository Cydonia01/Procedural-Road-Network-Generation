# Stage C (long-tail diversity): summary for presentation

A short, non-technical account of what stage C changed and how to see it.
Details, decisions and numbers: [discussion](2026-10-26-stage-c-discussion.md)
and [implementation plan](2026-10-26-stage-c-implementation.md).

## Prompts

**2026-10-27**

> You can commit this and continue with the rest of the phases. Also, add some view commands for generated examples

(This summary is the "supervisor summary" step of the plan, phase C10. The
other prompts of stage C are quoted in the discussion and the plan.)

## The problem

After stage A the road *shapes* were realistic, but the cities were all
alike: almost every city was a grid, almost every intersection had three or
four roads meeting at right angles, and the road types that make driving
hard for a self-driving car did not exist at all: no roundabouts, no
cul-de-sacs, no motorway ramps, merges or lanes that end, no bridges over
other roads, no tunnels. A model trained only on such maps never sees the
rare situations it most needs to learn.

## What was done

**1. A way to measure variety.** A new measurement counts how many roads
meet at each intersection, how skewed intersections are, how many dead
ends there are, how varied the street directions are, how large the blocks
are, and how many roundabouts, bridges, tunnels and interchanges a city
has. Every change below was checked against these numbers.

**2. One city generator instead of two.** The separate grid generator
("manhattan") was removed: the main generator now grows the same grids,
including Manhattan's alternating one-way avenues and a ring road, as the
papers do with a single growth method. Old runs that used it say so and
point to the replacement.

**3. Street patterns, mixed in one city.** Following Chen et al. and Parish &
Müller, streets now follow a blend of patterns: a regular grid, a radial
"Paris" star with ring roads, organic curving streets, streets that follow
hill contours (San Francisco), and suburbs. One setting moves a city from
grid to organic; another skews streets so they cross at other angles than
90°.

**4. Districts.** A new "style map" splits a city into districts, each with
its own pattern and its own grid direction: a grid downtown, a radial star
around the main centre, organic or suburban streets at the edge, streets
along the contours on steep slopes. Neighbouring districts blend into each
other.

**5. From grids to suburbs.** A connectivity setting closes gaps in the
network (grid-like) or leaves a tree of dead-end streets (suburbs). Dead
ends get small turn-around loops (cul-de-sac bulbs), so traffic never gets
stuck.

**6. More kinds of intersections.** Star intersections with five to seven
roads, Y and K shapes, staggered crossroads (two T junctions close
together), and lanes that split inside an intersection. A small extra
generator makes one intersection of any of these shapes per run, so rare
shapes can be produced on demand (after the JunctionArt paper).

**7. Roundabouts and U-turns.** Intersections can become roundabouts (one or
two lanes, sized after the US federal roundabout guide). U-turns are allowed
where a road is wide enough.

**8. Motorways.** Highways can be motorways: streets pass under them on
bridges instead of crossing at intersections, and the motorway is raised
over them with gentle ramps. Highways can go through hills in tunnels.

**9. Motorway interchanges.** Diamond interchanges connect motorways to the
city (exit ramps, entry ramps, deceleration and acceleration lanes), and a
cloverleaf template connects two motorways. Motorways also gain and lose
lanes along the way.

**10. Stronger checks.** A new check makes sure that roads crossing without
an intersection really are at different heights. It found an older geometry
fault (a curve could cut across a nearby road in about 1 in 5 large cities),
which is now fixed.

## Results

Example networks, before stage C → after (from `plans/baselines/`):

| | Before | After |
|---|---|---|
| Kinds of city layout | grids (straight, wobbly, curved) | + radial, organic, skewed, suburban, mixed districts |
| Intersections with 5 or more roads | almost none (0–5 per city) | 13 in the mixed city, 3–7-road junctions on demand |
| Skewed intersections (≥ 15° off a right angle) | 0–8 % in grid cities | 26 % organic town, 55 % mixed city, 100 % skewed grid |
| Variety of street directions (entropy) | 1.25 (grid) – 2.68 | up to 3.52 (mixed city) |
| Roundabouts, cul-de-sacs | none | in organic towns, suburbs and mixed cities |
| Bridges over roads, tunnels, interchanges | none | in motorway cities |
| Cities that cannot be built (200 tried) | 0 | SWEEP |

Two honest caveats:

- The simulator's drive test of the interchange networks takes many minutes
  and was postponed; the networks pass all the other checks.
- The main generator's motorways stop where they meet another motorway, so
  cloverleafs appear only in the single-interchange generator so far.

## How to look at it

Every example run is listed in [`runs/README.md`](../runs/README.md) with a
`view` command that opens it in esmini's viewer (with cars, and with
ground and buildings where the run made them), for example:

```bash
docker compose run --rm gui python -m cli view out/<hash>/paris.xodr
```

The stage C examples are phase 9 there: Paris, organic town, skewed grid,
suburb, mixed city, single junctions (K, random, roundabout), the diamond
and cloverleaf interchanges, the motorway city, the motorway corridor and
the city with a tunnel.
