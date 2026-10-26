# Generators

A generator turns a frozen **params dataclass** and a **seed**, plus
optional [input maps](maps.md), into a [`RoadGraph`](road-graph.md).
Generators are deterministic and never touch OpenDRIVE.

```bash
python -m cli generators          # list generators, presets and every parameter
```

## Common interface (`roadgen/generators/base.py`)

```python
class RoadNetworkGenerator(ABC, Generic[P]):
    name: ClassVar[str]           # set by @register
    params_type: ClassVar[type]   # the params dataclass
    description: ClassVar[str]
    uses_maps: ClassVar[bool] = False
    def generate(self, params: P, seed: int) -> RoadGraph: ...
    def map_bounds(self, params: P) -> Optional[Bounds]: ...   # (x0, y0, x1, y1) or None
    def map_kinds(self, params: P) -> Optional[tuple[str, ...]]: ...  # kinds --maps builds; None = all
```

- **Input maps.** A generator that reads maps sets `uses_maps = True`,
  accepts `generate(params, seed, maps: Optional[MapSet] = None)` and
  returns its world area from `map_bounds(params)`. The CLI builds or loads
  the maps for that area first ([architecture](architecture.md#input-maps-and-the-order-of-stages)).
  Generators without `uses_maps` are never passed maps. `map_kinds(params)`
  tells `pipeline --maps` which kinds to build (lsystem asks for a
  [style map](maps.md#style-maps) only with its `field` goal).

- **Registry.** `@register("name")` makes a generator available to the CLI
  and to configs. `generator_names()` and `get_generator(name)` look it up.
  Built-ins are registered when `roadgen.generators` is imported.
- **Presets.** A params class may define `PRESETS: dict[str, dict]` and a
  `preset(name, **overrides)` classmethod.
- **Parameter resolution.** The CLI writes every source into the run's
  config section `generators.<name>` (stored in `out/<hash>/run.yaml`, only
  what differs from the defaults), and the params are built from it. Later
  sources override earlier ones:
  1. `defaults.yaml`, then the preset (`--preset <name>`, or `preset:` in
     the `--params` file or in the config section);
  2. a `--config` file's values for the section;
  3. a YAML file (`--params file.yaml`), containing either the fields
     directly or `preset: <name>` plus a `params:` mapping;
  4. repeatable `--set key=value` (values parsed as YAML, so
     `--set origin=[10,20]` works).

  Unknown field names are rejected. YAML lists become tuples where the
  dataclass expects tuples. For library use, `load_params` does the same
  for a file, a preset and overrides.
- **Default output name:** `<generator>[_<preset>]_s<seed>`, in the run's folder `out/<hash>/`.

```yaml
# example params file: my_grid.yaml
preset: manhattan_like
params:
  extent: [750, 560]       # 4 avenues x 8 streets (grid_extent)
  angle_jitter_deg: 1.5
```

```bash
python -m cli pipeline lsystem --params my_grid.yaml --set rotation_deg=0 --seed 2
```

## `lsystem`: growth after Parish & Müller (`lsystem.py`)

Implements the road-growth loop from *Procedural Modeling of Cities* (2001).
All work happens in a local frame: the lower-left corner of `extent` is the
origin. The result is rotated and translated to world coordinates at the end.

**Main loop.** Proposed segments wait in a priority queue ordered by time,
with a FIFO tie-break so results are deterministic. Each popped proposal goes
through **local constraints**, which may shorten, redirect or reject it:

1. **bounds:** endpoints outside the area are dropped;
2. **intersect:** a segment crossing an existing road stops at the first
   crossing, which becomes a junction (the crossed road is split);
3. **snap:** an end within `snap_radius` of a node ends on that node; an end
   within `snap_radius` of a road ends on that road (split);
4. **acceptability:** minimum length, no duplicate road, at least
   `min_angle_deg` to every road already at either end, no crossing of any
   other road, and split pieces must stay ≥ `min_segment_length`.

An accepted segment with a free end asks the **global goal** for follow-ups.
There are two:

- `grid` (the default): continue straight (time + 1) and, with probability
  `branch_prob`, branch perpendicular both ways (time + `branch_delay`).
  Headings are pulled back to the grid axes on every step, so
  `angle_jitter_deg` adds noise without drift.
- `field` (stage C2): follow a blend of street patterns; see
  [Pattern fields](#pattern-fields-global_goal-field).

lsystem is the only generator for cities (the [`junction`](#junction-one-junction-per-seed-junctionpy)
generator makes single junctions): the old `manhattan` generator was
removed in stage C, since lsystem grows the same grids
([Manhattan-style grids](#manhattan-style-grids)), as in the papers.

Roads along local y become **avenues** (`major_*` lanes and speed). Roads
along local x become **streets** (`minor_*`). With maps there are also
**highways** (`highway_*`, see [below](#input-maps)). Spatial hashing keeps the
neighbour queries fast. Generation stops after `max_segments`.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `global_goal` | `grid` | `grid` or `field` (pattern fields) |
| `extent`, `origin`, `rotation_deg` | (1000, 600), (0, 0), 0 | Area size and placement |
| `start` | (0.5, 0.5) | Seed point as a fraction of `extent` |
| `block_size` | (250, 80) | Segment length along local x / y [m] |
| `branch_prob`, `branch_delay` | 1.0, 3 | Branching chance and queue delay |
| `angle_jitter_deg`, `length_jitter` | 0, 0 | Heading noise (std, deg) and relative length noise |
| `snap_radius`, `min_segment_length`, `min_angle_deg` | 15, 30, 30 | Local constraints |
| `max_segments` | 20000 | Safety cap |
| `curve_mode` | `none` | Curved streets: `none` (the straight grid, unchanged), `drift` or `contour` |
| `curve_drift_deg`, `curve_reversion` | 6, 0.3 | Heading noise per segment (std, deg) and pull back towards the grid axis (0–1) |
| `curve_tortuosity` | 1.0 | Target length / chord of a street, reached with a bow node; 1 = straight |
| `major_lanes`, `minor_lanes`, `lane_width`, `major_speed`, `minor_speed` | (2,2), (1,1), 3.5, 13.9, 11.1 | Road properties |

Presets:

- `grid`: an exact 10 × 10 lattice, grown from a corner (checked against
  the lattice coordinates in the tests).
- `manhattan_like`: Midtown Manhattan, see [below](#manhattan-style-grids).
- `grid_organic`: the same goal with heading and length noise and 15% of
  branches skipped. Produces irregular blocks, and dead ends are likely
  (use `--allow-traps`).
- `city`: a 3000 × 2400 m area with a noisy grid and a `min_angle_deg` (40)
  that lets streets branch off curved highways. Meant for `--maps`.
- `organic_curved`: `drift` streets that wander smoothly away from the grid
  and bow between junctions (tortuosity 1.03).
- `hill_town`: `city` with `contour` streets that follow the terrain on
  slopes. Meant for `--maps`.

**Curved streets** (`curve_mode`, stage A9; `none` draws no extra random
numbers, so existing networks are unchanged):

- `drift`: a street's deviation from its grid axis follows a mean-reverting
  random walk (Ornstein–Uhlenbeck): each segment adds `curve_drift_deg` of
  noise and is pulled back by `curve_reversion`. The change per segment is
  limited so that the bend between two blocks can still get its design
  radius. A continuation keeps the deviation and branches leave
  perpendicular to the street, so blocks stay roughly rectangular.
- `contour`: the same, pulled towards the terrain's contour direction (from
  the elevation map's gradient), weighted by the slope: fully from 8 %,
  at most 60° off the axis (Parish & Müller's San Francisco rule, the height
  field of Chen et al.).
- With `curve_tortuosity` > 1, every street between two nodes then gets a
  **bow node** placed sideways at its middle so that its length / chord
  reaches the target (Aliaga et al.'s tortuosity), where the bow crosses no
  road, keeps `snap_radius` from other nodes and stays on legal ground.
- The OpenDRIVE builder turns the resulting bend nodes into smooth curves.
  Tensor-field layouts (Chen et al.) are the `field` goal below; `curve_mode`
  is only for the `grid` goal.

```bash
python -m cli pipeline lsystem --preset grid
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings --simulate
python -m cli pipeline lsystem --preset city --seed 1 --allow-traps --maps
python -m cli pipeline lsystem --preset organic_curved --allow-traps --drive-check
python -m cli pipeline lsystem --preset hill_town --maps --allow-traps
python -m cli pipeline lsystem --preset city --allow-traps --maps \
    --map-set terrain.coast_strength=0.8 --map-set terrain.water_fraction=0.3 \
    --map-set population.n_centers=4 --set max_grade=0.2
```


### Manhattan-style grids

Stage C1 moved the features of the removed `manhattan` generator into
lsystem. All are off by default, so existing networks are unchanged:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `oneway_pattern` | (none, none) | (avenues, streets): `none` or `alternating` |
| `boundary` | `none` | `none` or `ring`: a two-way ring road around the area |
| `ring_offset`, `ring_lanes` | 60, (2, 2) | Ring distance from the area's edge [m] and its lanes |
| `edge_drop_prob` | 0 | Chance to drop each street segment |

- **Grid lines.** Each proposal carries the index of its grid line:
  continuations keep it, a branch gets the index of the line through its
  node, counted from the seed point's line in block sizes. On the straight
  grid this is exact; the seed point's own line has index 0.
- **One-way (`alternating`).** Even lines run along +axis, odd ones against
  it, and a one-way road puts *all* its lanes forward (the sum of the lane
  tuple). A lane tuple with a zero entry, such as `(2, 0)`, is one-way even
  under `none`.
- **Ring (`boundary: ring`).** Every grid road ending within the largest
  block size of the area's edge continues straight out to a two-way ring
  road `ring_offset` outside, so traffic never dead-ends at the edge of the
  grid. An extension that would cross a road, or end closer than
  `min_segment_length` to a ring corner or to the previous one on its side,
  is left out. The ring ignores input maps; it is meant for grids without them.
- **Edge drop** (`roadgen/generators/transforms/drop.py`). Street segments
  are dropped at random, but **only if every direction they carried can
  still be travelled another way**. Connectivity, and strong connectivity on
  one-way grids, is preserved. The draws come from their own random stream,
  so the roads that stay are exactly the ones grown without dropping.
- **Sizes as counts.** `grid_extent(n_x, n_y, block_size)` gives the extent
  of a grid of `n_x` avenues and `n_y` streets grown from `start: [0, 0]`;
  `bench` uses it for its `grid_sizes`.

**Preset `manhattan_like`:** Midtown-style. 10 avenues 250 m apart with 4
one-way lanes and 30 streets 80 m apart with 2 one-way lanes, both
alternating, rotated 29°, 11.2 m/s (25 mph), with a ring road.

```bash
python -m cli pipeline lsystem --preset manhattan_like --seed 1
python -m cli pipeline lsystem --preset grid --set extent=[1500,560] \
    --set rotation_deg=15 --set angle_jitter_deg=2 --set edge_drop_prob=0.2 \
    --set boundary=ring --seed 1 -o grid_variant
```

### Pattern fields (`global_goal: field`)

Stage C2 (`roadgen/generators/patterns.py`, after Chen et al. 2008,
sec. 5, and Parish & Müller's pattern rules, sec. 3.2.2). Each pattern gives,
at a point, a direction field as a symmetric tensor whose major direction
the avenues follow; the streets follow the minor one, 90° to it. Tensors
don't care whether a road runs one way or the other, so patterns blend as a
weighted sum without wrap-around artefacts:

| Pattern | Directions |
|---------|------------|
| `grid` | one direction everywhere (`grid_angle_deg` from the local axes); New York |
| `radial` | avenues towards the nearest centre (spokes), streets around it (rings); Paris |
| `organic` | the grid turned by smooth Perlin noise, up to `organic_max_deg`, features of `organic_scale` |
| `contour` | avenues along the terrain contour, weighted by the slope (fully from 8 %), the grid on flat ground; San Francisco |
| `suburb` | like organic but curvier, with `suburb_block_size` blocks, `suburb_branch_prob` and `suburb_end_prob` |

Weights come from `pattern_weights` (grid, radial, organic, contour, suburb;
the radial one falling off as a Gaussian of `radial_radius` around
`radial_center`) or, on input maps, from a [style map](maps.md#style-maps).

- **Growth.** A segment steps along the field like a streamline: its
  heading is the field direction at its midpoint (midpoint rule, so rings
  stay round), closest to the proposal's heading. A continuation predicts
  the midpoint along its own heading and turns at most as far as the bend's
  design radius allows; a branch starts along the other direction. Lengths
  and the branch probability are the patterns' blended values.
- **Star.** Where the seed point is a radial centre (radial weight ≥ 0.5),
  growth starts with `radial_spokes` avenues: a star junction (étoile).
- **Skew** (Chen's rotation field). Streets turn away from 90° to the
  avenues by 0 to `skew_deg`, in patches of `skew_scale`: Y and K junctions.
- **Organic knob** `organic` ∈ [0, 1]: moves that share of the grid weight
  to the organic pattern, and headings within (1 − `organic`) · `snap_deg`
  of the plain field snap to it (TownSim's snap knob), so 0 keeps exact
  grids and 1 is fully organic.
- `curve_tortuosity` still bows streets; `curve_mode` must stay `none`.
- Without a style map, `start`, `extent` and the field are all the
  generator needs, so `field` works without maps too.

Presets: `paris` (a six-spoke star with rings, a grid outside), `organic_town`
(organic 0.8), `skewed_grid` (skew up to 30°), `suburb` (below) and
`mixed_city` (districts from a style map; meant for `--maps`).

```bash
python -m cli pipeline lsystem --preset paris --allow-traps
python -m cli pipeline lsystem --preset organic_town --set organic=0.4 --allow-traps
python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 --allow-traps
```

### Connectivity and cul-de-sacs

Stage C4. Two knobs between a well-connected grid and a tree of cul-de-sacs:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `detour_ratio` | 0 (off) | Connect nodes whose trip over the network is more than this × their distance; ≥ 1 |
| `connect_radius` | 300 | How far a connector may reach [m] |
| `culdesac_prob` | 0 | Chance of a turn-around bulb at a street's dead end |
| `suburb_culdesac_prob` | 0.7 | The same in suburbs (field goal), weighted by the suburb share |
| `culdesac_radius`, `culdesac_speed` | 15, 8.3 | Bulb loop radius [m] and speed [m/s] |
| `suburb_end_prob` | 0.25 | Chance a suburb street ends at a node instead of continuing |

- **Connectors** (Lechner et al. 2003, sec. 3). After growth, dead ends and
  then the other nodes in random order look for the nearest node within
  `connect_radius` that is more than `detour_ratio` times farther away over
  the network than in a straight line, and join it if the segment passes the
  local constraints (and the maps). A ratio close to 1 closes almost every
  gap; a large one only the worst detours. The
  [connected-node ratio](validation.md#network-metrics) falls as it rises.
- **Cul-de-sac bulbs** (`roadgen/generators/transforms/culdesac.py`). A
  street's dead end becomes a teardrop: the end node joins a one-lane,
  one-way loop of 6 nodes on a circle through it, driven counter-clockwise
  (right turn in, island on the left), so cars can turn around and the dead
  end is no longer a trap. The two loop arms leave the end node at 120°: a
  symmetric Y junction. Where something lies within the bulb (plus a lane
  and 2 m), the end node moves back along the street by one radius, up to
  three times, as long as 30 m of street remain; otherwise the dead end
  stays. Bulbs must lie inside the area and on legal ground, and are listed
  in `metadata["culdesacs"]`.

**Preset `suburb`:** suburb pattern only, minimum angle 50°, 30 km/h
streets, bulbs at 90 % of dead ends that have room.

### Junction variety

Stage C5. Junctions with more legs and other shapes come from the patterns
above and three more knobs:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `stagger_prob` | 0 | Chance a 4-way crossing becomes two offset T junctions |
| `stagger_offset` | 35 | Distance between the two T junctions [m] |
| `max_degree` | 5 | Most roads the repair may join when it merges two nodes |

- **Stars.** Every radial centre in the area (radial weight ≥ 0.5) gets a
  star of `radial_spokes` avenues, not only the seed point; with
  `max_degree` 7 the repair keeps 6- and 7-leg junctions.
- **Skew** from the skew field (`skew_deg`), and **5-leg junctions** where
  connectors (`detour_ratio`) or snaps join an existing junction.
- **Staggered crossings** (`roadgen/generators/transforms/stagger.py`): one
  arm of a crossing road moves `stagger_offset` along the through road (the
  pair with more lanes), which is split there. Undone where the moved arm
  would cross a road, meet another closer than `min_angle_deg`, or where
  less than 5 m of road would stay between the two T junctions after their
  trims.

`organic_town` uses stagger 0.3; `paris` and `mixed_city` allow 7 roads at
a node; `mixed_city` also uses stagger 0.15 and connectors (`detour_ratio`
2.5).

### Roundabouts

Stage C7 (`roadgen/generators/transforms/roundabout.py`).

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `roundabout_prob` | 0 | Chance a junction of 3–5 roads becomes a roundabout (where it fits) |
| `roundabout_radius` | 25 | Single-lane ring, centre line [m] |
| `roundabout_radius_avenue` | 32 | Two-lane ring, when an avenue or a 4-lane road meets the junction [m] |
| `roundabout_speed` | 8.3 | On the ring [m/s] |

The junction node is replaced by a one-way ring driven counter-clockwise,
each road cut back to an entry node on it, with a bend node at most every
60° between entries (radii after the FHWA roundabout guide). Entries are
3-way junctions, so traffic turns right onto the ring and right off it; a
U-turn is a lap. Candidates need their roads at least 50° apart and no
highway. A roundabout is only built where all of it fits:

- every road keeps 5 m after the entry's trim and its far end's;
- every ring arc keeps 5 m between its two entries' trims;
- the ring is smooth: fitted with the builder's own alignment, none of its
  bends would become a junction (inner lane tighter than `min_lane_radius`).
  Otherwise the radius is tried 25 % larger once;
- the ring (plus its lanes and 3 m) touches no other road, stays inside the
  area and on legal ground.

Roundabouts are listed in `metadata["roundabouts"]` (centre, radius,
lanes, entry and ring nodes). `organic_town` (0.1), `suburb` (0.15) and
`mixed_city` (0.08) use them.

### Input maps


### Input maps

Given a `MapSet` (`pipeline lsystem --maps`, or `--map-file`), growth follows
the paper's global goals and local constraints (Sections 3.2–3.3). The
pipeline builds the maps in order (terrain → elevation → population →
style, see [Input maps](maps.md#design)), so they agree with each other. Each map
is optional. **Without maps the output is exactly the grid growth above.**
The `grid` goal ignores a style map; the `field` goal blends its patterns by it.

**Phase 1: highways** (only with a population map and `highways: true`).
The seed shoots `SEED_RAYS` = 16 rays and starts two highways: one in the
most populated direction, one in the best direction at least 90° away. At
each free highway end:

1. **Global goal (population).** A fan of `highway_rays` directions within
   ±`highway_max_turn_deg` of the current heading is ranked by
   Σ population(sample) / distance over `highway_ray_samples` samples up to
   `highway_ray_length` (Figure 4 in the paper).
2. **Local constraints.** These are checked in rank order, and the first
   direction that passes wins:
   - **water:** a segment may cross one stretch of water up to
     `max_bridge_length`. It is stretched to the far shore if it would end in
     water, and its edge id goes into `metadata["bridges"]`;
   - **grade:** at most `max_grade` on land (bridges are level);
   - **density:** the end needs at least `highway_min_density`;
   - **detour:** if no direction in the fan passes, directions up to
     ±`highway_max_detour_deg` are tried, smallest turn first. The paper
     calls this "rotate the segment within a maximal angle"; the highway
     goes around a lake or along a hill's contour;
   - then the usual intersect / snap / angle checks. A highway that meets
     another road stops there.
3. **Follow-ups.** The highway continues (time + 1). On each side it branches
   into a new highway with `highway_branch_prob` (time +
   `highway_branch_delay`). With `branch_prob` it also spawns a grid street
   along the grid axis (field goal: the field direction) closest to its
   normal. Those streets wait for phase 2.

**Phase 2: streets.** This is the grid growth above, seeded at the start
point and at every street spawned by a highway. Additional checks:

- **terrain:** a street that would enter water (or vegetation, with
  `avoid_vegetation`) is pruned to the last legal sample. This happens only
  if at least `prune_factor` of its length (and `min_segment_length`) is
  left; otherwise the street is dropped. A pruned street does not continue
  straight but may still branch, so streets line the shore;
- **population:** streets stop where the density at their end is below
  `street_min_density` ("when an area with no population is reached, the
  streets stop growing");
- **grade:** no `GRADE_BASELINE` = 25 m stretch steeper than `max_grade`.
  Bumps shorter than that are treated as smoothed out by grading.

If the seed point lies in water or vegetation, it moves to the nearest legal
cell inside the area. A map that doesn't cover the whole area logs a warning,
and its edge values are used outside it. Map sampling uses half the finest
cell size as its step.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `highways` | true | Grow highways first (needs a population map) |
| `highway_length` | 150 | Segment length [m] |
| `highway_rays`, `highway_max_turn_deg`, `highway_max_detour_deg` | 7, 25, 60 | Direction fan and detour [deg] |
| `highway_ray_length`, `highway_ray_samples` | 800, 8 | Population rays |
| `highway_branch_prob`, `highway_branch_delay` | 0.1, 6 | New highways per side and segment |
| `highway_min_density` | 1500 | Highways stop below this [people/km²] |
| `highway_lanes`, `highway_speed` | (2, 2), 22.2 | Road properties (road class `highway`) |
| `max_bridge_length` | 250 | Longest water crossing [m] |
| `street_min_density` | 500 | Streets stop below this [people/km²] |
| `avoid_vegetation` | true | Streets stay out of parks / forest |
| `prune_factor` | 0.4 | Share of a street that must remain after pruning |
| `max_grade` | 0.15 | Max rise / run along any road; 0 disables |

## `junction`: one junction per seed (`junction.py`)

Stage C6, after Muktadir et al., *JunctionArt* (2023). Cities have almost
only 3- and 4-leg junctions near 90°; this generator makes any shape on
demand, for long-tail datasets, builder stress tests and short ego
scenarios. It is not a city generator: one junction (or an offset pair) and
its legs.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `legs` | 0 | Number of legs (3–7); 0 = drawn by `leg_weights` |
| `leg_weights` | (0.3, 0.3, 0.2, 0.1, 0.1) | P(3, 4, 5, 6, 7 legs) |
| `angles_deg` | () | Leg headings [deg]; empty = evenly spread from a random start |
| `skew_deg` | 20 | Std dev of each leg's turn from its heading [deg] |
| `min_angle_deg` | 30 | Least angle between neighbouring legs [deg] |
| `leg_length` | 150 | [m] |
| `lane_probs` | (0.15, 0.6, 0.2, 0.05) | P(0, 1, 2, 3 lanes) on each side of a leg (JunctionArt), never 0 on both |
| `lane_width`, `street_speed`, `avenue_speed` | 3.5, 11.1, 13.9 | Legs with 4 or more lanes are avenues |
| `pair`, `pair_offset` | false, 35 | Two T junctions on a through road, stems on opposite sides [m] |
| `culdesacs`, `culdesac_radius`, `culdesac_speed` | true, 15, 8.3 | Turn-around bulbs at two-way leg ends |
| `roundabout`, `roundabout_radius`, `roundabout_radius_avenue`, `roundabout_speed` | false, 25, 32, 8.3 | Build the junction as a roundabout, where it fits |
| `max_tries` | 50 | Draws until a buildable junction |

A drawn junction must be buildable: arms at least `min_angle_deg` apart and
every road longer than its junction trims plus 5 m (checked with the
neutral junction-space code, never OpenDRIVE). Otherwise it is drawn again
on the next random sub-stream, so the result depends only on the seed
(`metadata.attempts` says how many draws it took; at most 4 over 200 seeds).
Bulbs at the leg ends remove the dead ends, so scenarios have no traps
(except behind one-way legs).

Presets: `t`, `y` (three legs at 120°), `skewed_cross` (60° / 120°), `k` (a
through road with two diagonals on one side), `five_way`, `six_way`,
`offset_t` (`pair`), `roundabout`, `roundabout_5`, `random`. Over seeds 0–199, `random` gives every leg
count from 3 to 7.

```bash
python -m cli pipeline junction --preset k --seed 3 --drive-check
python -m cli pipeline junction --preset random --seed 17 --set skew_deg=30
for s in $(seq 0 49); do python -m cli pipeline junction --preset random --seed $s; done
```

## Repair: edges too short for their junctions (`repair.py`)

A generator places nodes without knowing how much road each junction will
take: the builder trims a road back to every junction it ends at
([trimming](opendrive.md#trimming-where-roads-stop)). Generators end
with `repair_short_edges`, which finds the edges the builder would reject
(an edge between two junctions whose trims add up to more than its length)
and fixes only those, so every network that could be built before is
returned unchanged. Once every edge fits, it also fixes **stub roads**: a
single-edge road between two junctions that keeps less than
`MIN_ROAD_LENGTH` (5 m) after both trims, measured on the builder's own
alignment (which also keeps bends too tight for their inner lane as
junctions). Such a stub cannot change from one sloped junction's grade and
banking to the other's (found on stage C's `mixed_city`). Each is fixed by:

1. **collapse** the edge: its two nodes merge at its midpoint (the lower id
   survives) if the merged node joins at most 5 roads (Teng & Bidarra's
   vertex-degree rule), no two roads would join the same pair of nodes, and
   its arms stay at least 10° apart;
2. otherwise **remove** it, if its two nodes stay connected;
3. otherwise leave it and log a warning (the builder reports it).

Over 200 lsystem `city` networks on generated maps (`metrics --sweep 200`),
9 failed to build before the junction-trim fix and the repair, 0 after.

## Graph transforms (`roadgen/generators/transforms/`)

Passes from RoadGraph to RoadGraph that a generator runs at its end, like
the repair. They never touch OpenDRIVE, so the builder sees ordinary roads,
and any generator can use them. Each is deterministic for the random
generator it is given (lsystem salts a sub-stream per transform, so turning
one on leaves the rest of the network as it was).

| Module | Function | Stage |
|--------|----------|-------|
| `drop.py` | `drop_edges`: drop segments of given classes, keeping every travel direction possible | C1 |
| `culdesac.py` | `add_culdesacs`: turn-around bulbs at street dead ends | C4 |
| `stagger.py` | `stagger_crossings`: 4-way crossings into offset T pairs | C5 |
| `roundabout.py` | `add_roundabouts`: junctions into one-way rings | C7 |

## Adding a generator

1. Create `roadgen/generators/<name>.py` with a frozen params dataclass,
   validated in `__post_init__`, and optional `PRESETS` + `preset()`.
2. Subclass `RoadNetworkGenerator`, set `params_type` and `description`,
   decorate with `@register("<name>")`, and implement `generate(params, seed)`
   using only `np.random.default_rng(seed)`.
3. Call `assign_node_kinds(graph)` and fill `graph.metadata` (`name`,
   `generator`, `seed`, `params`).
4. Import the module in `roadgen/generators/__init__.py`.
   To read input maps, set `uses_maps = True`, take `maps: Optional[MapSet]`
   in `generate`, implement `map_bounds`, and sample with
   `MapLayer.sample(x, y)` (world coordinates).
5. Add tests: determinism, `graph.validate() == []`, and building the graph
   with `OpenDriveBuilder` without errors.

The CLI picks the new generator up automatically: `generate <name>`,
`pipeline <name>`, `--preset`, `--params` and `--set`.

The benchmark sweeps lsystem grids sized by counts (`BenchConfig.network`
holds lsystem params or a preset, see [benchmarking](benchmarking.md)).
