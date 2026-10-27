# lsystem: city generator

`roadgen/generators/lsystem.py` grows a city's roads with the loop from
Parish & Müller, *Procedural Modeling of Cities* (2001). It is the only city
generator. It covers plain and Manhattan-style grids, curved and organic
streets, blended street patterns, cul-de-sacs, roundabouts, and freeways on
input maps. Every feature beyond the plain grid is off by default and draws
from its own random sub-stream, so enabling one leaves the rest of a network
unchanged.

The generator interface and how parameters are set are described in
[Generators](generators.md). The resulting graph is described in
[RoadGraph](road-graph.md).

```bash
python -m cli pipeline lsystem --preset grid
python -m cli pipeline lsystem --preset manhattan_like --seed 1
python -m cli pipeline lsystem --preset paris --allow-traps
python -m cli pipeline lsystem --preset mixed_city --maps --allow-traps
python -m cli pipeline lsystem --preset highway_corridor --maps --allow-traps
```

## Presets

| Preset | Goal | What it grows |
|--------|------|---------------|
| `grid` | grid | An exact 10 × 10 lattice from a corner (tests check the lattice coordinates) |
| `manhattan_like` | grid | Midtown: 10 avenues 250 m apart (4 lanes, one-way) and 30 streets 80 m apart (2 lanes, one-way), both alternating, rotated 29°, 11.2 m/s (25 mph), a ring road |
| `grid_organic` | grid | Noisy headings and lengths, 15 % of branches skipped: irregular blocks, likely dead ends |
| `organic_curved` | grid | `drift` streets that wander from the grid and bow between junctions |
| `city` | grid | 3000 × 2400 m noisy grid, `min_angle_deg` 40 so streets can leave curved highways. For `--maps` |
| `hill_town` | grid | `city` with `contour` streets that follow slopes. For `--maps` |
| `highway_corridor` | grid | `city` with straight freeways, diamond interchanges every 1000 m, lane adds and drops. For `--maps` |
| `paris` | field | A six-spoke star with rings, a grid outside, up to 7 roads at a node |
| `organic_town` | field | Organic 0.8, staggered crossings, some roundabouts |
| `skewed_grid` | field | Streets meet avenues at 60–90°, in patches |
| `suburb` | field | Suburb pattern only, 30 km/h streets, bulbs at 90 % of dead ends, some roundabouts |
| `mixed_city` | field | Districts of every pattern from a style map, connectors, staggers, roundabouts. For `--maps` |

Networks with dead ends report traffic traps. Pass `--allow-traps`, or
remove the traps ([Validation](validation.md#traffic-connectivity)).

## How growth works

Everything happens in a local frame with the lower-left corner of `extent` at
the origin. The result is rotated by `rotation_deg` and moved to `origin` at
the end.

**Main loop.** Proposed segments wait in a priority queue ordered by time,
with a FIFO tie-break for determinism. Each popped proposal passes through
the **local constraints**, which may shorten, redirect or reject it:

1. **Bounds:** endpoints outside the area are dropped.
2. **Intersect:** a segment crossing a road stops at the first crossing,
   which becomes a junction (the crossed road is split).
3. **Snap:** an end within `snap_radius` of a node ends on that node. An end
   within `snap_radius` of a road ends on the road (split).
4. **Acceptability:** a segment is rejected if any of these fail:
   - it is at least the minimum length and doesn't duplicate a road;
   - it is at least `min_angle_deg` from every road at either end;
   - it crosses no other road;
   - the split pieces stay at least `min_segment_length`.

An accepted segment with a free end asks the **global goal** for follow-ups:

- **`grid`** (default): continue straight (time + 1) and, with probability
  `branch_prob`, branch perpendicular both ways (time + `branch_delay`).
  Headings are pulled back to the grid axes on every step, so
  `angle_jitter_deg` adds noise without drift.
- **`field`**: follow a blend of street patterns
  ([below](#pattern-fields-global_goal-field)).

Roads along local y are **avenues** (`major_*`), and roads along local x are
**streets** (`minor_*`). On a population map there are also **highways**
(`highway_*`). Spatial hashing keeps neighbour queries fast. Growth stops
after `max_segments`.

At the end, the graph passes through the enabled
[transforms](generators.md#graph-transforms) and the
[repair](generators.md#repair-edges-too-short-for-their-junctions).

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `global_goal` | `grid` | `grid` or `field` |
| `extent`, `origin`, `rotation_deg` | (1000, 600), (0, 0), 0 | Area size [m] and placement |
| `start` | (0.5, 0.5) | Seed point, as a fraction of `extent` |
| `block_size` | (250, 80) | Segment length along local x / y [m] |
| `branch_prob`, `branch_delay` | 1.0, 3 | Branching chance and queue delay |
| `angle_jitter_deg`, `length_jitter` | 0, 0 | Heading noise (std, deg) and relative length noise |
| `snap_radius`, `min_segment_length`, `min_angle_deg` | 15, 30, 30 | Local constraints [m, m, deg] |
| `max_segments` | 20000 | Safety cap |
| `major_lanes`, `minor_lanes` | (2, 2), (1, 1) | (forward, backward) lanes of avenues and streets |
| `lane_width`, `major_speed`, `minor_speed` | 3.5, 13.9, 11.1 | [m], [m/s] |

## Grids

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `oneway_pattern` | (none, none) | (avenues, streets): `none` or `alternating` |
| `boundary` | `none` | `ring`: a two-way ring road around the area |
| `ring_offset`, `ring_lanes` | 60, (2, 2) | Ring distance outside the area [m], and its lanes |
| `edge_drop_prob` | 0 | Chance to drop each street segment |

- **Grid lines.** Each proposal carries the index of its grid line.
  Continuations keep it, and a branch takes the index of the line through its
  node, counted in block sizes from the seed point's line (index 0).
- **One-way (`alternating`).** Even lines run along the axis and odd lines
  against it. A one-way road puts *all* its lanes forward (the sum of the lane
  tuple). A lane tuple with a 0, such as `(2, 0)`, is one-way even under
  `none`.
- **Ring.** Every road ending within the largest block size of the area's
  edge continues straight out to the ring, so traffic never dead-ends at the
  grid's edge. An extension is left out if it would:
  - cross a road; or
  - end closer than `min_segment_length` to a ring corner or to the previous
    extension.

  The ring ignores input maps.
- **Edge drop** (`transforms/drop.py`). Street segments are dropped at
  random, but only if every travel direction they carried remains possible
  another way. Connectivity, and strong connectivity on one-way grids, is
  preserved.
- **Sizes as counts.** `grid_extent(n_x, n_y, block_size)` gives the extent
  of a grid of `n_x` avenues and `n_y` streets grown from `start: [0, 0]`.

```bash
python -m cli pipeline lsystem --preset grid --set extent=[1500,560] \
    --set rotation_deg=15 --set angle_jitter_deg=2 --set edge_drop_prob=0.2 --set boundary=ring
```

## Curved streets (grid goal)

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `curve_mode` | `none` | `drift` or `contour` (needs an elevation map) |
| `curve_drift_deg`, `curve_reversion` | 6, 0.3 | Heading noise per segment (std, deg) and pull back to the grid axis (0–1) |
| `curve_tortuosity` | 1.0 | Target length / chord of a street; 1 = straight |

- **`drift`.** A street's deviation from its grid axis follows a
  mean-reverting random walk (Ornstein–Uhlenbeck). Each segment adds
  `curve_drift_deg` of noise and is pulled back by `curve_reversion`.
  - The change per segment is limited so the bend between two blocks can
    still get its design radius.
  - A continuation keeps the deviation. Branches leave perpendicular, so
    blocks stay roughly rectangular.
- **`contour`.** The same walk, pulled towards the terrain's contour
  direction (the elevation gradient). The pull is weighted by slope: full
  from 8 %, and at most 60° off the axis. This is Parish & Müller's San
  Francisco rule.
- **Tortuosity** (Aliaga et al.). With `curve_tortuosity` > 1, each street
  between two nodes gets a **bow node** placed sideways at its middle, so that
  length / chord reaches the target. The bow must cross no road, keep
  `snap_radius` from other nodes and stay on legal ground.

The builder turns these bend nodes into smooth curves
([Road geometry](geometry.md#fitted-roads)).

## Pattern fields (`global_goal: field`)

`roadgen/generators/patterns.py`, after Chen et al. (2008, sec. 5) and
Parish & Müller's pattern rules (sec. 3.2.2).

Each pattern gives, at every point, a direction field as a symmetric tensor.
Avenues follow its major direction, and streets the minor one, 90° to it.
Tensors don't care about a road's direction, so patterns blend as a weighted
sum without wrap-around artefacts.

| Pattern | Directions |
|---------|------------|
| `grid` | One direction everywhere (`grid_angle_deg` from the local axes); New York |
| `radial` | Avenues towards the nearest centre (spokes), streets around it (rings); Paris |
| `organic` | The grid turned by smooth Perlin noise, up to `organic_max_deg`, bends of `organic_scale` |
| `contour` | Avenues along the terrain contour, weighted by slope (full from 8 %), grid on flat ground; San Francisco |
| `suburb` | Like organic but curvier, with `suburb_block_size`, `suburb_branch_prob` and `suburb_end_prob` |

**Weights** come from one of two sources:

- `pattern_weights`, where the radial weight falls off as a Gaussian of
  `radial_radius` around `radial_center`; or
- on input maps, a [style map](maps.md#style-zones-street-patterns-per-district),
  where neighbouring districts blend over `style_blend_width`, each grid at
  its district's angle.

**How it grows:**

- **Streamlines.** A segment's heading is the field direction at its
  midpoint, the one closest to the proposal's heading. Using the midpoint
  keeps rings round.
  - A continuation turns at most as far as its bend's design radius allows.
  - A branch starts along the other direction.
  - Lengths and the branch probability are the patterns' blended values.
- **Stars.** Every radial centre in the area (radial weight ≥ 0.5) gets
  `radial_spokes` avenues: a star junction (étoile).
- **Skew** (Chen's rotation field). Streets turn away from 90° to the
  avenues by 0 to `skew_deg`, in patches of `skew_scale`, which gives Y and K
  junctions.
- **Organic knob** `organic` ∈ [0, 1]. It moves that share of the grid weight
  to the organic pattern. Headings within (1 − `organic`) · `snap_deg` of the
  plain field snap to it (TownSim's snap knob). So 0 keeps exact grids, and 1
  is fully organic.
- `curve_tortuosity` still bows streets, but `curve_mode` must stay `none`.
  Without a style map, the field needs only `start` and `extent`, so it works
  without maps.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `pattern_weights` | (1, 0, 0, 0, 0) | grid, radial, organic, contour, suburb (without a style map) |
| `grid_angle_deg` | 0 | Grid turn from the local axes [deg] |
| `radial_center`, `radial_radius`, `radial_spokes` | (0.5, 0.5), 0, 6 | Centre (fraction of extent), Gaussian radius [m] (0 = none), star avenues |
| `organic`, `organic_max_deg`, `organic_scale` | 0, 45, 600 | Knob, largest turn [deg], bend size [m] |
| `skew_deg`, `skew_scale` | 0, 1500 | Largest street skew [deg], patch size [m] |
| `snap_deg` | 15 | Snap window at `organic` 0 [deg] |
| `suburb_block_size`, `suburb_branch_prob`, `suburb_end_prob` | (120, 60), 0.6, 0.25 | Suburb pattern's block size, branching, and chance a street ends at a node |
| `style_blend_width` | 300 | District blend width [m] |

## Connectivity and cul-de-sacs

Two knobs between a well-connected grid and a tree of cul-de-sacs:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `detour_ratio` | 0 (off) | Connect nodes whose trip over the network is more than this × their distance; ≥ 1 |
| `connect_radius` | 300 | How far a connector may reach [m] |
| `culdesac_prob` | 0 | Chance of a turn-around bulb at a street's dead end |
| `suburb_culdesac_prob` | 0.7 | The same in suburbs, weighted by the suburb share |
| `culdesac_radius`, `culdesac_speed` | 15, 8.3 | Bulb radius [m] and speed [m/s] |

- **Connectors** (Lechner et al. 2003). After growth, nodes look for a
  partner to join, dead ends first and then the rest in random order.
  - The partner is the nearest node within `connect_radius` that is more than
    `detour_ratio` times farther over the network than in a straight line.
  - The new segment must pass the local constraints and the maps.
  - A ratio near 1 closes almost every gap, and a large one closes only the
    worst detours.
- **Cul-de-sac bulbs** (`transforms/culdesac.py`). A street's dead end
  becomes a teardrop.
  - **Shape.** The end node joins a one-lane, one-way loop of 6 nodes, driven
    counter-clockwise (right turn in, island on the left). The two loop arms
    leave at 120°, a symmetric Y junction. Cars can turn around, so the dead
    end is no longer a trap.
  - **Room.** If something lies within the bulb (plus a lane and 2 m), the end
    node moves back along the street by one radius. This repeats up to three
    times while 30 m of street remain; otherwise the dead end stays.
  - Bulbs must lie inside the area and on legal ground.

## Junction variety

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `stagger_prob` | 0 | Chance a 4-way crossing becomes two offset T junctions |
| `stagger_offset` | 35 | Distance between the two T junctions [m] |
| `max_degree` | 5 | Most roads the repair may join when it merges two nodes |

- **Many legs.**
  - Stars at radial centres; with `max_degree` 7 the repair keeps 6- and 7-leg
    junctions.
  - 5-leg junctions where connectors or snaps join an existing junction.
  - Skew from `skew_deg`.
- **Staggered crossings** (`transforms/stagger.py`). One arm of a crossing
  road moves `stagger_offset` along the through road (the pair with more
  lanes), which is split there. This is undone where the moved arm would:
  - cross a road;
  - meet another road closer than `min_angle_deg`; or
  - leave less than 5 m of road between the two T junctions after their trims.

## Roundabouts

`transforms/roundabout.py`, radii after the FHWA roundabout guide.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `roundabout_prob` | 0 | Chance a 3–5-way junction becomes a roundabout, where it fits |
| `roundabout_radius` | 25 | Single-lane ring, centre line [m] |
| `roundabout_radius_avenue` | 32 | Two-lane ring, when an avenue or a 4-lane road meets the junction [m] |
| `roundabout_speed` | 8.3 | On the ring [m/s] |

**Shape.** The junction node is replaced by a one-way ring driven
counter-clockwise:

- each road is cut back to an entry node on the ring;
- there is a bend node at least every 60° between entries;
- entries are 3-way junctions, so traffic turns right onto the ring and right
  off it, and a U-turn is a lap.

**Candidates** need their roads at least 50° apart and no highway.

**Fit.** A roundabout is only built where all of it fits:

- every road keeps 5 m after both its trims;
- every ring arc keeps 5 m between its entries' trims;
- no ring bend becomes a junction when fitted with the builder's alignment
  (inner lane radius under `min_lane_radius`). Otherwise the radius is tried
  25 % larger once;
- the ring, plus its lanes and 3 m, touches no other road and stays inside the
  area and on legal ground.

## Growing on input maps

With a `MapSet` (`pipeline --maps` or `--map-file`), growth follows the
paper's global goals and local constraints (sec. 3.2–3.3). Each map is
optional, and without maps the output is exactly the growth above. The
`grid` goal ignores a style map.

**Phase 1: highways.** This phase runs only with a population map and
`highways: true`. The seed shoots 16 rays and starts two highways: one in the
most populated direction, and one in the best direction at least 90° away.
At each free highway end:

1. **Global goal (population).** A fan of `highway_rays` directions within
   ±`highway_max_turn_deg` of the heading is ranked by Σ population /
   distance over `highway_ray_samples` samples up to `highway_ray_length`
   (the paper's Figure 4).
2. **Local constraints**, checked in rank order. The first direction that
   passes wins:
   - **water:** a segment may cross one stretch of water up to
     `max_bridge_length`. It is stretched to the far shore, and its edge is
     listed in `metadata["bridges"]`;
   - **grade:** at most `highway_max_grade` (or `max_grade`) on land;
     bridges are level;
   - **density:** the end needs `highway_min_density`;
   - **detour:** if nothing in the fan passes, directions up to
     ±`highway_max_detour_deg` are tried, smallest turn first. The highway
     goes around a lake or along a hill (the paper's "rotate the segment
     within a maximal angle");
   - then the usual intersect, snap and angle checks. A highway that meets
     another road stops there.
3. **Follow-ups.**
   - The highway continues (time + 1).
   - On each side it branches into a new highway with `highway_branch_prob`
     (time + `highway_branch_delay`).
   - With `branch_prob` it spawns a street along the grid axis (or field
     direction) closest to its normal. That street waits for phase 2.

**Phase 2: streets.** Normal growth, seeded at the start point and at every
street a highway spawned, with extra checks:

- **terrain:** a street entering water (or vegetation, with
  `avoid_vegetation`) is cut back to the last legal sample, if at least
  `prune_factor` of its length (and `min_segment_length`) remains. Otherwise
  it is dropped. A pruned street doesn't continue but may branch, so streets
  line the shore. A segment that snaps across water is rejected too;
- **population:** streets stop where the density is below
  `street_min_density`;
- **grade:** no 25 m stretch steeper than `max_grade`. Shorter bumps count as
  graded away.

If the seed point lies in water or vegetation, it moves to the nearest legal
cell. Maps are sampled every half of the finest cell size.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `highways` | true | Grow highways first (needs a population map) |
| `highway_length` | 150 | Segment length [m] |
| `highway_rays`, `highway_max_turn_deg`, `highway_max_detour_deg` | 7, 25, 60 | Direction fan and detour [deg]; turn ≤ detour < 90 |
| `highway_ray_length`, `highway_ray_samples` | 800, 8 | Population rays [m] |
| `highway_branch_prob`, `highway_branch_delay` | 0.1, 6 | New highways per side and segment |
| `highway_min_density` | 1500 | Highways stop below this [people/km²] |
| `highway_lanes`, `highway_speed` | (2, 2), 22.2 | Lanes and speed [m/s] |
| `max_bridge_length` | 250 | Longest water crossing [m] |
| `street_min_density` | 500 | Streets stop below this [people/km²] |
| `avoid_vegetation` | true | Streets stay out of parks and forest |
| `prune_factor` | 0.4 | Share of a street that must remain after pruning |
| `max_grade` | 0.15 | Max rise / run over 25 m; 0 = no limit |

```bash
python -m cli pipeline lsystem --preset city --maps --allow-traps \
    --map-set terrain.coast_strength=0.8 --map-set population.n_centers=4 --set max_grade=0.2
```

### Freeways, tunnels and interchanges

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `highway_crossings` | `at_grade` | `at_grade`: streets meet highways at junctions. `separated`: highways are freeways |
| `highway_max_grade` | 0 | Steepest highway over 25 m; 0 = `max_grade` |
| `tunnels` | false | Highways may tunnel through hills too steep to climb |
| `tunnel_min_cover`, `max_tunnel_length` | 8, 800 | Ground over a tunnel somewhere [m]; longest stretch under ground [m] |
| `interchanges` | false | Diamond interchanges on freeways (needs `separated`) |
| `interchange_spacing` | 1200 | Least distance between interchanges [m] |
| `highway_lane_change_prob` | 0 | Chance a highway stretch gets one more lane each way: a lane add and a lane drop |

- **Freeways** (`separated`). Streets ignore highways in the intersect,
  snap and crossing checks, so they pass under them without a junction.
  - Streets cross at least 30° from parallel and never end within a highway's
    corridor (half width plus `snap_radius`).
  - Highways spawn no streets, so streets reach them only at the seed point
    and at interchanges.
  - The crossings become overpasses after the drape
    ([grade separation](heights.md#3-grade-separation-overpasses)).
- **Tunnels** (Parish & Müller sec. 3.3). A highway segment too steep on the
  terrain is still allowed if a straight tunnel through the hill meets three
  conditions:
  - it keeps the highway grade end to end;
  - it has `tunnel_min_cover` of ground over it somewhere;
  - at most `max_tunnel_length` of it is under ground.

  Like a bridge, the segment is stretched to the far side, and it is listed in
  `metadata["tunnels"]`. The drape turns it into `Edge.tunnels`
  ([Heights](heights.md#tunnels)).
- **Interchanges.** Slots are chosen after the highways grow and before the
  streets.
  - **Where:** along straight highway stretches (within 20 m of a straight
    line), `interchange_spacing` apart, inside the area, on legal ground, away
    from other highways and the seed point.
  - **Reserved space:** each slot gets a crossing avenue under the highway and
    a zone streets may not enter. Streets start from the avenue's ends, so the
    city reaches the freeway through the interchanges.
  - **Building:** after growth, a [diamond](generators.md#interchanges) is
    built at each slot. lsystem never builds cloverleafs, because its
    highways stop where they meet, so they never cross.

```bash
python -m cli pipeline lsystem --preset city --maps --set highway_crossings=separated --allow-traps
python -m cli pipeline lsystem --preset city --maps --seed 3 --set tunnels=true --set highway_max_grade=0.05 \
    --map-set elevation.max_elevation=150 --map-set elevation.ridge=0.5 --allow-traps --ground
```
