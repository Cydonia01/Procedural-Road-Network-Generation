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
```

- **Input maps.** A generator that reads maps sets `uses_maps = True`,
  accepts `generate(params, seed, maps: Optional[MapSet] = None)` and
  returns its world area from `map_bounds(params)`. The CLI builds or loads
  the maps for that area first ([architecture](architecture.md#input-maps-and-the-order-of-stages)).
  Generators without `uses_maps` are never passed maps.

- **Registry.** `@register("name")` makes a generator available to the CLI
  and to configs. `generator_names()` and `get_generator(name)` look it up.
  Built-ins are registered when `roadgen.generators` is imported.
- **Presets.** A params class may define `PRESETS: dict[str, dict]` and a
  `preset(name, **overrides)` classmethod.
- **Parameter resolution** (`load_params`). Later sources override earlier ones:
  1. a YAML file (`--params file.yaml`), containing either the fields
     directly or `preset: <name>` plus a `params:` mapping;
  2. `--preset <name>` (wins over a preset named in the file);
  3. overrides: dedicated flags (Manhattan only) and repeatable
     `--set key=value` (values parsed as YAML, so `--set origin=[10,20]` works).

  Unknown field names are rejected. YAML lists become tuples where the
  dataclass expects tuples.
- **Default output:** `out/<generator>[_<preset>]_s<seed>`.

```yaml
# example params file: my_grid.yaml
preset: manhattan_like
params:
  n_avenues: 4
  n_streets: 8
  jitter: 3.0
```

```bash
python -m cli pipeline manhattan --params my_grid.yaml --set rotation_deg=0 --seed 2
```

## `manhattan`: parametric grid (`manhattan.py`)

Avenues run along +y and streets along +x before rotation. Node *(i, j)* sits
at *(i · avenue_spacing, j · street_spacing)*. The grid is then rotated by
`rotation_deg` and moved to `origin`.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `n_avenues`, `n_streets` | required | Grid size (needs ≥ 2 nodes) |
| `avenue_spacing`, `street_spacing` | 250, 80 | Distance between avenues / streets [m] |
| `rotation_deg`, `origin` | 0, (0, 0) | Placement of the grid |
| `avenue_lanes`, `street_lanes` | (2, 2), (1, 1) | (forward, backward) lane counts |
| `lane_width` | 3.5 | [m] |
| `avenue_speed`, `street_speed` | 13.9, 11.1 | [m/s] |
| `jitter` | 0 | Uniform random node displacement per axis [m] |
| `edge_drop_prob` | 0 | Probability of removing each street segment |
| `avenue_oneway_pattern`, `street_oneway_pattern` | `none` | `none` or `alternating` |
| `boundary_mode` | `dead_end` | `dead_end` or `loop` (adds a ring road) |
| `ring_offset`, `ring_lanes` | 60, (2, 2) | Ring distance from the grid [m] and its lanes |

Behaviour:

- **One-way (`alternating`).** Consecutive avenues or streets flip direction,
  and a one-way road puts *all* its lanes forward (the sum of the lane tuple).
  A tuple with a zero entry, such as `(2, 0)`, is one-way even under `none`.
- **Boundary `loop`.** Every avenue and street is extended by one segment to
  a two-way ring road, so traffic never dead-ends at the edge of the grid.
- **Edge drop.** Street segments are dropped at random, but **only if every
  direction they carried can still be travelled another way**. Connectivity,
  and strong connectivity on one-way grids, is preserved.
- **Determinism.** Jitter is one array draw for the whole grid, and drop
  decisions are drawn for all candidates up front. The outcome depends only
  on the seed.

Node ids are `i · n_streets + j + 1`. Ring nodes follow.

**Preset `manhattan_like`:** Midtown-style. 10 × 30 grid rotated 29°,
avenues 250 m apart with 4 one-way lanes, streets 80 m apart with 2 one-way
lanes, both alternating, 11.2 m/s (25 mph), with a ring road.

```bash
python -m cli pipeline manhattan --preset manhattan_like --seed 1
python -m cli pipeline manhattan --avenues 10 --streets 10 --jitter 3 \
    --edge-drop 0.2 --rotation 15 --seed 1 -o out/grid_variant
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
The only goal implemented is `grid`: continue straight (time + 1) and, with
probability `branch_prob`, branch perpendicular both ways (time +
`branch_delay`). Headings are pulled back to the grid axes on every step, so
`angle_jitter_deg` adds noise without drift.

Roads along local y become **avenues** (`major_*` lanes and speed). Roads
along local x become **streets** (`minor_*`). With maps there are also
**highways** (`highway_*`, see [below](#input-maps)). Spatial hashing keeps the
neighbour queries fast. Generation stops after `max_segments`.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `global_goal` | `grid` | Only `grid` exists so far |
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

- `grid`: reproduces the 10 × 10 Manhattan grid **exactly**. This is a
  consistency check between the two generators.
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
  Full tensor-field street layouts (Chen et al.) are left for a later
  style-mixing stage.

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



### Input maps

Given a `MapSet` (`pipeline lsystem --maps`, or `--map-file`), growth follows
the paper's global goals and local constraints (Sections 3.2–3.3). The
pipeline builds the maps in order (terrain → elevation → population, see
[Input maps](maps.md#design)), so they agree with each other. Each map
is optional. **Without maps the output is exactly the grid growth above.**

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
   along the grid axis closest to its normal. Those streets wait for phase 2.

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

## Repair: edges too short for their junctions (`repair.py`)

A generator places nodes without knowing how much road each junction will
take: the builder trims a road back to every junction it ends at
([trimming](opendrive.md#trimming-where-roads-stop)). Both generators end
with `repair_short_edges`, which finds the edges the builder would reject
(an edge between two junctions whose trims add up to more than its length)
and fixes only those, so every network that could be built before is
returned unchanged:

1. **collapse** the edge: its two nodes merge at its midpoint (the lower id
   survives) if the merged node joins at most 5 roads (Teng & Bidarra's
   vertex-degree rule), no two roads would join the same pair of nodes, and
   its arms stay at least 10° apart;
2. otherwise **remove** it, if its two nodes stay connected;
3. otherwise leave it and log a warning (the builder reports it).

Over 200 lsystem `city` networks on generated maps (`metrics --sweep 200`),
9 failed to build before the junction-trim fix and the repair, 0 after.

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
`pipeline <name>`, `--preset`, `--params` and `--set`. Only Manhattan has
dedicated flags. Other generators use `--set`.

The benchmark currently sweeps Manhattan grids only (`BenchConfig.network`
holds Manhattan params).
