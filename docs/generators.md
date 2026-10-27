# Generators

A generator turns a frozen **params dataclass** and a **seed**, plus optional
[input maps](maps.md), into a [RoadGraph](road-graph.md). Generators are
deterministic and never touch OpenDRIVE.

| Generator | Makes | Docs |
|-----------|-------|------|
| `lsystem` | Cities: grids, street patterns, highways on maps | [lsystem](lsystem.md) |
| `junction` | One junction or interchange per seed | [below](#junction-one-junction-per-seed) |

```bash
python -m cli generators          # generators, presets and every parameter
```

## Interface (`roadgen/generators/base.py`)

```python
class RoadNetworkGenerator(ABC, Generic[P]):
    name: ClassVar[str]           # set by @register
    params_type: ClassVar[type]   # the frozen params dataclass
    description: ClassVar[str]
    uses_maps: ClassVar[bool] = False
    def generate(self, params: P, seed: int) -> RoadGraph: ...
    def map_bounds(self, params: P) -> Optional[Bounds]: ...          # (x0, y0, x1, y1): area to build maps for
    def map_kinds(self, params: P) -> Optional[tuple[str, ...]]: ...  # kinds --maps builds; None = all
```

- **Registry.** `@register("name")` makes a generator available to the CLI
  and configs. `generator_names()` and `get_generator(name)` look it up.
  Built-ins register when `roadgen.generators` is imported.
- **Presets.** A params class may define `PRESETS: dict[str, dict]` and a
  `preset(name, **overrides)` classmethod.
- **Parameters.** Field defaults come from `generators.<name>` in
  `defaults.yaml`, and the CLI layers presets, `--config`, `--params` and
  `--set` on top ([how](cli-reference.md#parameters-and---config)).
  `load_params` does the same for library use.
- **Maps.** A generator that reads maps sets `uses_maps = True`, takes
  `generate(params, seed, maps: Optional[MapSet] = None)` and implements
  `map_bounds`. `map_kinds` limits the kinds `pipeline --maps` builds; lsystem
  asks for a style map only with its field goal.
- **Default output name:** `<generator>[_<preset>]_s<seed>`.

## `junction`: one junction per seed

`roadgen/generators/junction.py`, after Muktadir et al., *JunctionArt*
(2023). Cities have almost only 3- and 4-leg junctions near 90°. This
generator makes any shape on demand, for long-tail datasets, builder stress
tests and short ego scenarios. It makes one junction (or an offset pair) and
its legs, not a city.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `legs` | 0 | Number of legs (3–7); 0 = drawn by `leg_weights` |
| `leg_weights` | (0.3, 0.3, 0.2, 0.1, 0.1) | P(3, 4, 5, 6, 7 legs) |
| `angles_deg` | () | Leg headings [deg]; empty = evenly spread from a random start |
| `skew_deg` | 20 | Std dev of each leg's turn from its heading [deg] |
| `min_angle_deg` | 30 | Least angle between neighbouring legs [deg] |
| `leg_length` | 150 | [m] |
| `lane_probs` | (0.15, 0.6, 0.2, 0.05) | P(0, 1, 2, 3 lanes) on each side of a leg, never 0 on both |
| `lane_width`, `street_speed`, `avenue_speed` | 3.5, 11.1, 13.9 | Legs with 4+ lanes are avenues |
| `pair`, `pair_offset` | false, 35 | Two T junctions on a through road, stems on opposite sides [m] |
| `culdesacs`, `culdesac_radius`, `culdesac_speed` | true, 15, 8.3 | Turn-around bulbs at two-way leg ends |
| `roundabout`, `roundabout_radius`, `roundabout_radius_avenue`, `roundabout_speed` | false, 25, 32, 8.3 | Build it as a roundabout, where it fits |
| `interchange`, `interchange_length` | none, 1500 | `diamond` or `cloverleaf`: an [interchange](#interchanges) instead, each road this long [m] |
| `max_tries` | 50 | Draws until a buildable junction |

**Buildable.** A drawn junction must have its arms at least `min_angle_deg`
apart, and every road must be longer than its trims plus 5 m. This is
checked with the neutral junction-space code, not OpenDRIVE.

- **Redraws.** Otherwise it is drawn again on the next sub-stream, so the
  result still depends only on the seed. `metadata.attempts` counts the
  draws, at most 4 over 200 seeds.
- **No traps.** Bulbs at the leg ends remove the dead ends, so scenarios have
  no traps, except behind one-way legs.

Presets:

- shapes: `t`, `y` (120°), `skewed_cross` (60° / 120°), `k` (a through road
  with two diagonals on one side), `five_way`, `six_way`, `offset_t`;
- roundabouts and interchanges: `roundabout`, `roundabout_5`, `diamond`,
  `cloverleaf`;
- `random`: every leg count from 3 to 7 appears over seeds 0–199.

```bash
python -m cli pipeline junction --preset k --seed 3 --drive-check
python -m cli pipeline junction --preset random --seed 17 --set skew_deg=30 --allow-traps
for s in $(seq 0 49); do python -m cli pipeline junction --preset random --seed $s --allow-traps; done
```

## Repair: edges too short for their junctions

A generator places nodes without knowing how much road each junction will
take: the builder [trims](geometry.md#junction-trims) a road back from every
junction it ends at. Generators therefore end with `repair_short_edges`
(`repair.py`). It changes only the edges the builder would reject, so a
network that already builds is returned unchanged:

- **too short:** edges between two junctions whose trims add up to more than
  the edge's length;
- **stubs:** once every edge fits, a single-edge road between two junctions
  that keeps less than 5 m after both trims, measured on the builder's own
  alignment. A stub can't change from one sloped junction's grade and
  banking to the other's.

Each is fixed by the first option that works:

1. **Collapse** the edge, merging its two nodes at the midpoint (the lower id
   survives). This requires that:
   - the merged node joins at most `max_degree` (default 5) roads (Teng &
     Bidarra's vertex-degree rule);
   - no two roads would join the same pair of nodes;
   - its arms stay at least 10° apart.
2. **Remove** the edge, if its two nodes stay connected.
3. **Leave** it and log a warning (the builder reports it).

Over 200 lsystem `city` networks on maps (`metrics --sweep 200`), 9 failed to
build before the repair and 0 after.

## Graph transforms

`roadgen/generators/transforms/` holds passes from RoadGraph to RoadGraph
that a generator runs at its end. They never touch OpenDRIVE, so the builder
sees ordinary roads, and any generator can use them. Each is deterministic
for the random generator it gets. lsystem gives each its own sub-stream.

| Module | Function | Used by |
|--------|----------|---------|
| `drop.py` | `drop_edges`: drop segments, keeping every travel direction possible | [lsystem grids](lsystem.md#grids) |
| `culdesac.py` | `add_culdesacs`: turn-around bulbs at dead ends | [lsystem](lsystem.md#connectivity-and-cul-de-sacs), `junction` |
| `stagger.py` | `stagger_crossings`: 4-way crossings into offset T pairs | [lsystem](lsystem.md#junction-variety) |
| `roundabout.py` | `add_roundabouts`: junctions into one-way rings | [lsystem](lsystem.md#roundabouts), `junction` |
| `interchange.py` | `add_diamond`, `add_cloverleaf`: highway interchanges | [below](#interchanges) |

Each list of built features goes into `metadata` (`culdesacs`,
`roundabouts`, `interchanges`).

### Interchanges

`transforms/interchange.py` builds templates at a grade-separated crossing
of a highway with another road. Their parameters are in the `interchange:`
section of the config.

- **Common base.**
  - **Rebuilt roads.** Around the crossing, the mainline and the crossing road
    are rebuilt as straight chains with nodes where the template needs them.
  - **Ramps** are one-lane, one-way, highway-class roads. Mainline nodes
    where they leave or join get `Node.kind = "ramp"`. There the builder
    allows only straight-through, right exits and right merges
    ([ramp nodes](opendrive.md#ramp-nodes)).
  - **Auxiliary lanes** are lane-count changes on the mainline, built as
    [tapers](geometry.md#along-road-profiles).
- **Diamond** (highway × avenue or street).
  - **Exit:** each direction exits `ramp_off` (250 m) before the crossing,
    after an `aux_length` (100 m) deceleration lane.
  - **Terminal:** the ramp meets the crossing road at an at-grade terminal
    `terminal_offset` (90 m) off the mainline.
  - **Entry:** it rejoins `ramp_on` (200 m) after the crossing, beyond the
    overpass ramps, with an acceleration lane.
  - The crossing road gets at least `cross_lanes` (2 + 2) lanes.
- **Cloverleaf** (highway × highway at 70–110°).
  - **Loop ramps:** in each quadrant, a loop ramp (a 240° right turn of
    `loop_radius`, 60 m, leaving and joining at `ramp_angle_deg`, 25°)
    carries the left turn.
  - **Outer ramps:** an outer ramp (`outer_radius`, 150 m) carries the right
    turn. Outer ramps move out in 25 m steps until they clear the loops.
  - **Weaving:** between one loop's entry and the next loop's exit, both
    directions weave on an auxiliary lane.
- **All or nothing.** Nothing changes unless the whole interchange fits:
  - no other road may join the rebuilt roads inside it;
  - no ramp may come within 8 m (plus lane widths) of another road.

lsystem builds diamonds on its freeways
([lsystem](lsystem.md#freeways-tunnels-and-interchanges)). Cloverleafs come
from `junction --preset cloverleaf`.

## Adding a generator

1. Create `roadgen/generators/<name>.py` with a frozen params dataclass,
   validated in `__post_init__`, with defaults in `defaults.yaml` under
   `generators.<name>`, and optional `PRESETS` plus `preset()`.
2. Subclass `RoadNetworkGenerator`, set `params_type` and `description`,
   decorate it with `@register("<name>")`, and implement
   `generate(params, seed)`. Draw all randomness from
   `np.random.default_rng(seed)`.
3. End with `repair_short_edges` and `assign_node_kinds(graph)`, and fill
   `graph.metadata` (`name`, `generator`, `seed`, `params`).
4. Import the module in `roadgen/generators/__init__.py`.
5. To read maps, set `uses_maps = True`, take `maps: Optional[MapSet]`,
   implement `map_bounds`, and sample with `MapLayer.sample(x, y)` in world
   coordinates.
6. Add tests: determinism, `graph.validate() == []`, and a build with
   `OpenDriveBuilder` without errors.

The CLI picks the generator up automatically: `generate <name>`, `pipeline
<name>`, `--preset`, `--params` and `--set`.
