# Generators

A generator turns a frozen **params dataclass** and a **seed** into a
[`RoadGraph`](road-graph.md). Generators are deterministic and never touch
OpenDRIVE.

```bash
python -m cli generators          # list generators, presets and every parameter
```

## Common interface (`roadgen/generators/base.py`)

```python
class RoadNetworkGenerator(ABC, Generic[P]):
    name: ClassVar[str]           # set by @register
    params_type: ClassVar[type]   # the params dataclass
    description: ClassVar[str]
    def generate(self, params: P, seed: int) -> RoadGraph: ...
```

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
along local x become **streets** (`minor_*`). Spatial hashing keeps the
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
| `major_lanes`, `minor_lanes`, `lane_width`, `major_speed`, `minor_speed` | (2,2), (1,1), 3.5, 13.9, 11.1 | Road properties |

Presets:

- `grid`: reproduces the 10 × 10 Manhattan grid **exactly**. This is a
  consistency check between the two generators.
- `grid_organic`: the same goal with heading and length noise and 15% of
  branches skipped. Produces irregular blocks, and dead ends are likely
  (use `--allow-traps`).

```bash
python -m cli pipeline lsystem --preset grid
python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings --simulate
```

## Adding a generator

1. Create `roadgen/generators/<name>.py` with a frozen params dataclass,
   validated in `__post_init__`, and optional `PRESETS` + `preset()`.
2. Subclass `RoadNetworkGenerator`, set `params_type` and `description`,
   decorate with `@register("<name>")`, and implement `generate(params, seed)`
   using only `np.random.default_rng(seed)`.
3. Call `assign_node_kinds(graph)` and fill `graph.metadata` (`name`,
   `generator`, `seed`, `params`).
4. Import the module in `roadgen/generators/__init__.py`.
5. Add tests: determinism, `graph.validate() == []`, and building the graph
   with `OpenDriveBuilder` without errors.

The CLI picks the new generator up automatically: `generate <name>`,
`pipeline <name>`, `--preset`, `--params` and `--set`. Only Manhattan has
dedicated flags. Other generators use `--set`.

The benchmark currently sweeps Manhattan grids only (`BenchConfig.network`
holds Manhattan params).
