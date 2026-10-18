# Input maps and heights: reading order and CLI guide

A walkthrough of the `pre-generation-maps` changes. Every command below was run
in the dev container (124 runs). The script and full log are in
`out/try/tryall.sh` and `out/try/tryall.log`, and every output is in `out/try/`.

All the commands work. One real problem came up: on networks grown on maps,
`drive-check` fails, so `pipeline --maps --drive-check` fails too (see
[Issues found](#issues-found)).

---

## Part 1: Reading order

Read it in data-flow order: each step only uses what came before it.

| # | Files | What to look for |
|---|---|---|
| 1 | `docs/maps.md`, then `git diff docs/architecture.md` | The overall design: maps feed the generator, then drape, then the builder, then the scene. Read this before any code. |
| 2 | `git diff roadgen/graph/model.py roadgen/graph/io.py roadgen/graph/plot.py` | The RoadGraph changes everything else relies on: `Node.z`, `Edge.profile`, `Edge.bridges`, the `"highway"` road class, `edge_profile()` and `has_elevation`. `io._compact` leaves out unset heights, so a flat graph's JSON is unchanged. |
| 3 | `roadgen/maps/model.py` | `GridSpec` (origin, size, cell size; `from_bounds` and `around`), `MapLayer` (`sample` uses nearest cell for terrain and bilinear for the rest; row 0 is south), `MapSet`, and `.npz` save/load. |
| 4 | `roadgen/maps/base.py` | `MapGenerator` base class, the `register_map(kind, name)` registry, a seed salted per kind, and `make_map`, which drops upstream layers that aren't earlier in the order. |
| 5 | `roadgen/maps/noise.py`, then `image.py` | Shared helpers: Perlin noise, fBm, ridged noise, domain warp, majority filter, distance-to-mask, slope, and raster reading. |
| 6 | `terrain.py`, then `elevation.py`, then `population.py`, then `maps/plot.py`, `maps/__init__.py` | The three map generators, in pipeline order. Each `upstream` branch is the interesting part: water sits flat in valleys, and density is multiplied by habitability. |
| 7 | `git diff roadgen/defaults.yaml tests/test_config.py` | Every new parameter in one place: lsystem highways, `maps:`, `drape:`, `ground:`. |
| 8 | `git diff roadgen/generators/base.py`, then `git diff roadgen/generators/lsystem.py` | The `uses_maps` / `map_bounds` hooks, then the biggest change: the `_MapView` constraints (water, vegetation, grade, density), highways that follow population rays, and bridges. |
| 9 | `roadgen/maps/drape.py` | Gives the graph heights: node heights averaged over a disc, edge profiles, and bridge spans. |
| 10 | `roadgen/opendrive/elevation.py`, then `git diff roadgen/opendrive/junctions.py roadgen/opendrive/builder.py` | A PCHIP fit turns heights into `<elevation>` records. Connecting roads sit flat at the junction height. `<bridge>` objects are added. `node_trims` is pulled out for reuse. |
| 11 | `git diff roadgen/validate/continuity.py` | Height and grade checks at each contact point. |
| 12 | `roadgen/scene/osgt.py`, then `git diff roadgen/scene/buildings.py`, then `roadgen/scene/ground.py` | The `.osgt` writer, buildings standing on the ground, and the ground mesh levelled under the roads. |
| 13 | `git diff cli.py` | How it's wired: `generate_graph`, `map_requests`, `input_maps`, `cmd_map`, `cmd_drape`, `cmd_ground`, `cmd_pipeline`. |
| 14 | `tests/test_maps.py`, `test_lsystem_maps.py`, `test_elevation.py` | Executable specs for steps 3–6, 8 and 9–12. |
| 15 | The remaining doc diffs | Reference material. |

---

## Part 2: Command line, phase by phase

Shorthand used below:

```bash
cli() { docker compose run --rm -T dev python -m cli "$@"; }
```

**Parameter priority (lowest to highest):**
`defaults.yaml` < `--config FILE` < `--preset` < `--params FILE.yaml` < `--set`

### Phase 1: Input maps (`maps`, `map`, `plot-map`)

```bash
cli maps    # lists every map generator, its presets and parameter defaults
```

**Flags shared by all `map <kind> <generator>` commands:**

| Flag | Meaning | Tried |
|---|---|---|
| `--seed N` | Random seed. The same seed gives unrelated maps for different kinds. | `--seed 7` |
| `--preset NAME` | Start from a named parameter set. | all presets below |
| `--params FILE.yaml` | Parameter values, optionally `preset:` plus `params:` | `preset: coastal` with `coast_angle_deg: 180` |
| `--set KEY=VALUE` | Override one parameter (repeatable). The value is parsed as YAML. | every parameter, see below |
| `--origin X Y` | Lower-left corner [m], default `0 0` | `--origin 500 500` |
| `--size W H` | Map size [m], default `3000 3000` | `--size 2000 1500` |
| `--cell-size M` | Cell size [m], default 10 | `--cell-size 20` |
| `--graph NET.json` | Draw a road network on the PNG preview | yes |
| `--on KIND=FILE.npz` | Build on an earlier map. Not available for terrain. | wrong kind is rejected |
| `-o PREFIX` | Writes `PREFIX.npz` and `PREFIX.png` | |

#### 1a. Terrain: `noise` generator

Presets: `inland`, `lakes`, `coastal`, `river_city`.

```bash
cli map terrain noise --preset coastal --seed 3 -o out/coast     # result: water 30%, land 60%, vegetation 10%
cli map terrain noise --set water_fraction=0.3 --set coast_strength=1.0 --set coast_angle_deg=90
cli map terrain noise --set rivers=3 --set river_width=100 --set river_meander=400 --set river_wavelength=900
```

| Parameter (default) | Meaning |
|---|---|
| `scale` (1500) | Size of the largest water bodies [m] |
| `octaves` (5), `lacunarity` (2), `gain` (0.5) | fBm detail: number of layers, frequency step, amplitude falloff |
| `warp` (0.3) | Domain warp strength; higher bends outlines more organically |
| `water_fraction` (0.12) | Exact share of lakes or sea, before rivers |
| `coast_strength` (0) | 0 gives scattered lakes, 1 gives a straight coast on one side |
| `coast_angle_deg` (null) | Direction of the sea [deg]; `null` means random |
| `smoothing` (2) | 3×3 majority passes that remove speckles |
| `rivers` (1), `river_width` (50), `river_meander` (150), `river_wavelength` (1800) | Count, width [m], sideways amplitude [m] and meander length [m] of rivers |
| `vegetation_fraction` (0.15), `vegetation_scale` (400) | Share of the land that is park or forest, and patch size [m] |

#### 1b. Terrain: `image` generator

`path` is required; without it you get a clear error.

```bash
cli map terrain image --set path=my_map.png      # colour image: each pixel takes the nearest class colour
cli map terrain image --set path=mask.png --set gray_threshold=0.3 --set water_is_dark=false
```

- `water_color`, `land_color`, `vegetation_color`: reference RGB colours, values 0–1.
- `gray_threshold`, `water_is_dark`: how a grayscale image becomes a water mask.

#### 1c. Elevation: `fbm` generator

Presets: `flat`, `rolling`, `hilly`, `mountainous`.

```bash
cli map elevation fbm --preset hilly -o out/hilly
cli map elevation fbm --on terrain=out/coast.npz -o out/coast_elev   # water lies flat, land rises from the shore
cli map elevation fbm --set ridge=1.0 --set exponent=2.0 --set erosion_iterations=0
```

| Parameter (default) | Meaning |
|---|---|
| `min_elevation` (0), `max_elevation` (80) | Height range [m]; `min_elevation` is also the water surface |
| `scale`, `octaves`, `lacunarity`, `gain`, `warp` | As for terrain; higher `gain` gives rougher ground |
| `ridge` (0) | Blend of ridged noise (0–1), which gives mountain crests |
| `exponent` (1) | Values above 1 flatten valleys and sharpen peaks |
| `erosion_iterations` (20), `talus_deg` (30), `erosion_rate` (0.5) | Thermal erosion: number of passes, steepest stable slope, share of excess moved per pass |
| `shore_width` (600), `shore_height` (2) | Only with `--on terrain`: land reaches full height this far from water [m], and starts this high above the water [m] |

**Elevation `image` generator:** `path`, `min_elevation` / `max_elevation`
(the heights at black and white), and `stretch=true`, which uses the raster's
own min..max instead of 0..1. Tried with a `.npy` file.

#### 1d. Population: `centers` generator

Presets: `monocentric`, `polycentric`, `sprawl`.

```bash
cli map population centers --on terrain=out/coast.npz --on elevation=out/coast_elev.npz -o out/coast_pop
cli map population centers --set n_centers=6 --set profile=gaussian --set "secondary_weight=[0.8,1.0]"
```

| Parameter (default) | Meaning |
|---|---|
| `n_centers` (3), `peak_density` (15000), `decay_length` (700) | Number of centres, main peak [people/km²], decay length [m] (Clark model) |
| `profile` (exponential) | `exponential` or `gaussian` (gaussian has a flatter core) |
| `secondary_weight` ([0.3, 0.7]) | Peak of the other centres relative to the main one |
| `min_center_distance` (800), `center_margin` (0.15) | Spacing between centres [m]; how far centres stay from the edges, as a share of map size |
| `noise_amount` (0.35), `noise_scale` (400) | Neighbourhood-scale fBm variation |
| `base_density` (50) | Rural floor everywhere [people/km²] |
| `vegetation_factor` (0.1), `max_slope_deg` (20) | Only with upstream maps: density multiplier in parks, and the slope at which density falls to 0 |

**Population `image` generator:** `path`, `min_density`, `max_density`.

#### 1e. Re-render a map

```bash
cli plot-map out/coast_pop.npz out/pop_roads.png --graph out/net.json   # any .npz, optionally with roads on top
```

### Phase 2: Growing a network on maps (`generate ... --map-file`, `--flat`)

```bash
M="--map-file terrain=out/coast.npz --map-file elevation=out/coast_elev.npz --map-file population=out/coast_pop.npz"
cli generate lsystem --preset city -o out/net_nomaps.json            # 779 nodes; unchanged from before
cli generate lsystem --preset city $M -o out/net.json                # 460 nodes, draped automatically (steepest grade 13.9 %)
cli generate lsystem --preset city $M --flat -o out/net_flat.json    # same network, no heights
```

`--map-file KIND=FILE.npz` can be repeated. With an elevation map the graph is
draped automatically unless you pass `--flat`. Manhattan accepts maps too, but
only gets heights from them: one hilly run reached a 47.7 % grade, which
`docs/maps.md` lists as a known limitation.

**New lsystem parameters, each tried with `--set` on top of `$M`:**

| Parameter (default) | Meaning | Result on this map |
|---|---|---|
| `highways` (true) | Grow highways first (needs a population map) | `false`: 410 nodes instead of 460 |
| `highway_length` (150) | Length of one segment [m] | 250: 384 nodes |
| `highway_rays` (7) | Directions tried at each highway end | 3: 435 |
| `highway_max_turn_deg` (25) | Half-angle of the fan of directions | 10: 443, 3 bridges |
| `highway_max_detour_deg` (60) | Fallback turn when nothing in the fan fits | Must satisfy turn ≤ detour < 90; 90 is rejected |
| `highway_ray_length` (800), `highway_ray_samples` (8) | How far the population rays look, and samples per ray | 400: 474; 4: 497 |
| `highway_branch_prob` (0.1), `highway_branch_delay` (6) | Chance of a new highway branch, and its queue delay | 0.3: 486; 2: 431 |
| `highway_min_density` (1500) | Highways stop below this density | 5000: 451 |
| `highway_lanes` ([2, 2]), `highway_speed` (22.2) | Lane counts and speed [m/s] | Same topology (460); only attributes change |
| `max_bridge_length` (250) | Longest water crossing a highway may bridge [m] | 50: no change, since this map needs no long bridge |
| `street_min_density` (500) | Streets stop below this density | 2000: 338 |
| `avoid_vegetation` (true) | Keep streets out of parks | `false`: 465 |
| `prune_factor` (0.4) | A street that hits water or vegetation keeps at least this share of its length | 0.8: 395 |
| `max_grade` (0.15) | Steepest allowed rise over run; 0 means no limit | 0.05: only 48 nodes |

### Phase 3: Draping an existing graph (`drape`)

```bash
cli drape out/net_flat.json --map-file elevation=out/coast_elev.npz       # writes out/net_flat.draped.json
cli drape out/net_flat.json --map-file elevation=out/coast_elev.npz \
    --map-file terrain=out/coast.npz -o out/net_draped.json
```

- `--map-file elevation=` is required; without it you get a clear error.
  Adding `terrain=` enables bridges.
- Its parameters live only in the config file. Example `drape.yaml`:

  ```yaml
  drape: {sample_spacing: 10.0, smoothing: 0.0, bridge_clearance: 6.0}
  ```

  ```bash
  cli --config drape.yaml drape out/net_flat.json \
      --map-file elevation=out/coast_elev.npz -o out/net_rough.json     # grade rose to 16.2 %
  ```

| Parameter (default) | Meaning |
|---|---|
| `sample_spacing` (20) | Distance between profile points [m] |
| `smoothing` (80) | Terrain averaging length [m]; 0 follows every bump |
| `bridge_clearance` (2) | Minimum deck height above water [m] |

### Phase 4: OpenDRIVE with heights (`build-xodr`, `drive-check`)

```bash
cli build-xodr out/net_draped.json out/net_draped.xodr                  # 6375 <elevation> records; continuity checks z and grade
cli build-xodr out/net_draped.json out/net_m4.xodr --junction-margin 4
cli drive-check out/net_draped.xodr                                     # currently fails, see Issues found
```

### Phase 5: 3D scene (`ground`)

```bash
cli ground out/ground.osgt --map-file elevation=out/coast_elev.npz        # bare terrain
cli ground out/ground.osgt --map-file elevation=out/coast_elev.npz \
    --map-file terrain=out/coast.npz                                      # coloured by terrain class
cli ground out/scene.osgt --map-file elevation=out/coast_elev.npz --map-file terrain=out/coast.npz \
    --graph out/net_draped.json --buildings --seed 3 --junction-margin 2  # 176 buildings
```

| Flag | Meaning |
|---|---|
| `--graph` | Level the ground under this (draped) network's roads |
| `--buildings` | Add the graph's buildings; only has an effect together with `--graph` |
| `--seed` | Building heights |
| `--junction-margin` | Must match the value used for the `.xodr` |

Config: `ground: {cell_size: 20, road_margin: 2, below_road: 0.3}`. With
`cell_size: 40` the file shrank from about 7.6 MB to 0.77 MB.

### Phase 6: Scenario and simulation with the scene

```bash
cli scenario out/net_draped.xodr -o out/net_draped.xosc --vehicles 30 --scenegraph out/scene.osgt
cli simulate out/net_draped.xosc --no-record --no-csv     # 30 vehicles, 60 s simulated in 8.3 s, 0 errors
cli simulate out/net_draped.xosc --gui                    # view it (gui container)
```

### Phase 7: Everything in one `pipeline` call

```bash
cli pipeline lsystem --preset city --allow-traps --maps -o out/pl          # builds all 3 maps around the area; writes pl_<kind>.npz/.png
cli pipeline lsystem --preset city --allow-traps --terrain noise --map-set terrain.rivers=2   # terrain map only
cli pipeline lsystem --preset city --allow-traps --maps --elevation image --map-set elevation.path=heightmap.png
cli pipeline lsystem --preset city --allow-traps --maps --map-set terrain.water_fraction=0.3 \
    --map-set elevation.max_elevation=150 --map-set population.n_centers=1
cli pipeline lsystem --preset city --allow-traps --maps --map-file terrain=out/coast.npz   # a file wins over a generator for that kind
cli pipeline lsystem --preset city --allow-traps --maps --flat
cli pipeline lsystem --preset city --allow-traps --maps --buildings        # writes pl_buildings.osgt (219 buildings)
cli pipeline lsystem --preset city --allow-traps --maps --ground           # writes pl_scene.osgt
cli pipeline lsystem --preset city --allow-traps --maps --ground --buildings --simulate --sim-vehicles 20 --sim-duration 10
cli pipeline manhattan --avenues 4 --streets 4 --maps --ground             # maps used for heights only
```

| Flag | Meaning |
|---|---|
| `--maps` | Use the default generator for each kind from `maps.pipeline` (`noise`, `fbm`, `centers`) |
| `--terrain`, `--elevation`, `--population NAME` | Choose the generator for one kind; overrides `--maps` for that kind |
| `--map-set KIND.KEY=VALUE` | Override one map parameter. An unknown key fails with a clear error |
| `--map-file KIND=FILE.npz` | Load a map from a file; wins over a generator for that kind |
| `--ground` | Write a ground mesh; needs an elevation map, otherwise a clear error |
| `--buildings` | Write buildings (on the ground when there are maps) |
| `--flat` | No heights |

All of these runs passed. The schema check was skipped because
`OPENDRIVE_XSD` isn't set.

---

## Issues found

1. **`drive-check` fails on networks grown on maps.** On the hand-built
   network, 3102 of 3127 movements passed. With `pipeline --maps --drive-check`,
   4177 of 4209 passed, so the pipeline reports FAILED. Elevation is not the
   cause: the same graph built flat fails the same 25 movements, and the
   network grown without maps passes 5591 of 5591. The failing roads are short
   edges between junctions, such as highway road 363 (47 m) and street road
   527 (41 m). esmini reports `lane on entry None`, which suggests the junction
   trims leave very little road on these edges. Unit tests don't catch this;
   it's worth fixing, or covering with a test, before you commit.
2. Minor: with `highways=false` the drape still found one bridge, although
   streets are supposed to stop at water. It's probably a street clipping a
   water cell. Check `out/try/net_highways.json` if it matters to you.
