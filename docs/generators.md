# Generators

A generator turns a frozen **params dataclass** and a **seed**, plus optional
[input maps](maps.md), into a [RoadGraph](road-graph.md). Generators are
deterministic and never touch OpenDRIVE.

| Generator | Makes | Docs |
|-----------|-------|------|
| `lsystem` | Cities: grids, street patterns, highways on maps | [lsystem](lsystem.md) |

```bash
python -m cli generators          # generators, presets and every parameter
```

## Interface (`kadmos/generators/base.py`)

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
  Built-ins register when `kadmos.generators` is imported.
- **Presets.** A params class may define `PRESETS: dict[str, dict]`, read from
  `kadmos/presets.yaml` with `config.presets("generators", "<name>")`, and a
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
   - its arms stay at least 10° apart, and so do those of the nodes next to
     it, whose roads turn with the moved node (two-road bends included: a
     bend that tight would become an unbuildable junction).
2. **Remove** the edge, if its two nodes stay connected.
3. **Leave** it and log a warning (the builder reports it).

Over 200 lsystem `city` networks on maps (`metrics --sweep 200`), 9 failed to
build before the repair and 0 after.

With HD maps on, the pipeline runs the same repair once more before the
build, with the roads' [cross-sections](geometry.md#cross-sections) (turn pockets) in the trims.
That pass leaves the stubs the network already had alone. An edge the
builder's alignment finds too short for a bend it keeps as a junction counts
as too short too. A [left-turn pocket](opendrive.md#lane-use-pockets-and-banned-turns-hd-maps)
widens its arm and so the trims of the arms beside it. Before such an edge
is collapsed or removed, the pockets at its two junctions are left out one
at a time until it fits; those it fits without are put back. The pockets
left out are kept in the graph metadata (`no_pockets`, `[node, edge]`
pairs), and their count in `fit.dropped_pockets`.

An edge too short only because of the cross-section (it fits with the
driving lanes alone; every stub in that pass is one, since the old stubs are
left alone, but an old stub the alignment now finds too short for a bend it
keeps as a junction is checked like a too-short edge) is **never collapsed or
removed**. If leaving out pockets is not enough and one end is a two-road
bend, which the wider road turned into a junction (its curve no longer
clears the roads around), the bend is **straightened**: its two edges, the
same road, become one edge between the junctions on either side, and the
bend node goes (`straightened_bends`, `fit.straightened_bends`). The road
stays. An edge still too short is left for the builder to report. Only
edges too short with the driving lanes alone are collapsed or removed, as
above.

The pass recomputes only what a change touches: junction trims are memoised
on the graph (keyed on everything they read, and read from the node's own
arms only), pockets are tried for all edges of one scan before the network is
aligned again, and a trial re-checks a stub from its trims (a single-edge road
is straight). Each scan reads the graph's topology from one `adjacency()`
snapshot.

## Graph transforms

`kadmos/generators/transforms/` holds passes from RoadGraph to RoadGraph
that a generator runs at its end. They never touch OpenDRIVE, so the builder
sees ordinary roads, and any generator can use them. Each is deterministic
for the random generator it gets. lsystem gives each its own sub-stream.

| Module | Function | Used by |
|--------|----------|---------|
| `culdesac.py` | `add_culdesacs`: turn-around bulbs at dead ends | [lsystem](lsystem.md#connectivity-and-cul-de-sacs) |
| `stagger.py` | `stagger_crossings`: 4-way crossings into offset T pairs | [lsystem](lsystem.md#junction-variety) |
| `roundabout.py` | `add_roundabouts`: junctions into one-way rings | [lsystem](lsystem.md#roundabouts) |
| `interchange.py` | `add_diamond`: diamond interchanges on freeways | [below](#interchanges) |

Each list of built features goes into `metadata` (`culdesacs`,
`roundabouts`, `interchanges`).

### Interchanges

`transforms/interchange.py` builds a diamond at a grade-separated crossing
of a highway with an avenue or street. Their parameters are in the `interchange:`
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
- **All or nothing.** Nothing changes unless the whole interchange fits:
  - no other road may join the rebuilt roads inside it;
  - no ramp may come within 8 m (plus lane widths) of another road.

lsystem builds them on its freeways
([lsystem](lsystem.md#freeways-tunnels-and-interchanges)).

## Adding a generator

1. Create `kadmos/generators/<name>.py` with a frozen params dataclass,
   validated in `__post_init__`, and optional presets (`presets.yaml` under
   `generators.<name>`, `PRESETS` plus `preset()`). Defaults can stay in the
   dataclass; the built-in generators keep theirs in `defaults.yaml` under
   `generators.<name>` (read through `generator_defaults`), so a config file
   can change them.
2. Subclass `RoadNetworkGenerator`, set `params_type` and `description`,
   decorate it with `@register("<name>")`, and implement
   `generate(params, seed)`. Draw all randomness from
   `np.random.default_rng(seed)`.
3. End with `repair_short_edges` and `assign_node_kinds(graph)`, and fill
   `graph.metadata` (`name`, `generator`, `seed`, `params`).
4. Import the module in `kadmos/generators/__init__.py`.
5. To read maps, set `uses_maps = True`, take `maps: Optional[MapSet]`,
   implement `map_bounds`, and sample with `MapLayer.sample(x, y)` in world
   coordinates.
6. Add tests: determinism, `graph.validate() == []`, and a build with
   `OpenDriveBuilder` without errors. `tests/test_registry.py` runs a minimal
   generator (one crossing, no maps, no config section) through `generate`
   and `pipeline`; it is a working template.

The CLI picks the generator up automatically: `generate <name>`, `pipeline
<name>`, `--preset`, `--params` and `--set`.
