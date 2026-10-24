# Example runs

Every successful `python -m cli` command is recorded in
[`history.json`](history.json) under a 12-digit hash of its inputs (all
parsed parameters, defaults included, and the effective configuration).
The same inputs are stored once, however they were typed. See
[CLI reference → history](../docs/cli-reference.md#history).

```bash
python -m cli history                 # all runs: hash, note, command
python -m cli history --grep "7 gui"  # filter by note or command
python -m cli HASH                    # run it again (same as: replay HASH)
python -m cli replay HASH --show      # only print the command
python -m cli --note "my run" ...     # describe a run in the history
python -m cli --no-history ...        # don't record a throwaway run
```

In Docker, prefix the commands with `docker compose run --rm -T dev`, or
with `docker compose run --rm gui` for phase 7 (needs a desktop session; see
[docs/docker.md](../docs/docker.md)). The phase 7 views (`odrviewer`) stay open
until you close the window; the `simulate --gui` runs close when the scenario
ends.

The runs below were made by [`examples.sh`](examples.sh) (which also
regenerates the input images in [`inputs/`](inputs/)); this page is written
by [`make_readme.py`](make_readme.py). They write to `out/examples/`
(git-ignored), and later runs read files written by earlier ones, so replay
them in order. Each note starts with its phase (`1 maps |`, `2 network |`,
`3 xodr |`, `4 scene |`, `5 scenario |`, `6 simulate |`, `7 gui |`,
`8 geometry |`):

```bash
# everything, oldest first (phase 7 needs the gui service)
for h in $(python -m cli history | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
# one phase
for h in $(python -m cli history --grep "1 maps |" | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
```

or one chain from the list below.

The runs of phases 1–7 were recorded with road geometry version 1 (one
straight road per edge, every bend a junction). Replaying them builds the
current geometry (version 2: fitted curves, banking, junction planes) and
logs a warning that the network differs; phase 8 shows the new geometry.

## From scratch to the GUI

**Manhattan grid with buildings: pipeline, scenario, simulation, GUI:** `495cd073e19e` → `9f82122169a2` → `99b10e867a5f` → `909299126a95` → `3369a331a99f`

**Manhattan grid, one stage per command:** `234f458fa465` → `063741241dea` → `e698213b661a` → `0bc96a18db4c`

**Organic lsystem grid with buildings:** `7096c372e1bf` → `b829a179eecb` → `5cc2b1ab4f3b` → `97f8879ee028`

**Coastal city on input maps, with ground and buildings:** `0bafbe99d5d4` → `606b0af80169` → `38855ef051e8` → `6b4975a09dbc` → `13ebf755086b` → `df96fadf942a` → `68fefd666ab7` → `ac96d08ca541` → `2e45f514ec2e` → `6539301a9834`

**Road geometry: build, measure and drive the coastal city:** `9852ee91656d` → `2a9597708ab6` → `f72fb09c3674`

**City on generated maps in one command, then the GUI:** `de96553ef40d` → `cbc76231e493`

## 1. Input maps

- `0bafbe99d5d4` terrain noise, preset coastal (sea on one side)

  ```bash
  python -m cli map terrain noise --preset coastal --seed 3 --origin -200 -200 --size 3400 2800 -o out/examples/maps/coast_terrain
  ```

- `606b0af80169` elevation fbm, preset rolling, on the coastal terrain (water lies flat in the valleys)

  ```bash
  python -m cli map elevation fbm --preset rolling --seed 3 --origin -200 -200 --size 3400 2800 --on terrain=out/examples/maps/coast_terrain.npz -o out/examples/maps/coast_elevation
  ```

- `38855ef051e8` population centers, preset polycentric, on the coastal terrain + elevation

  ```bash
  python -m cli map population centers --preset polycentric --seed 3 --origin -200 -200 --size 3400 2800 --on terrain=out/examples/maps/coast_terrain.npz --on elevation=out/examples/maps/coast_elevation.npz -o out/examples/maps/coast_population
  ```

- `afa63898b118` terrain noise, preset river_city (one wide river)

  ```bash
  python -m cli map terrain noise --preset river_city --seed 5 -o out/examples/maps/river_terrain
  ```

- `46527fce93ae` elevation fbm, preset rolling, on the river terrain

  ```bash
  python -m cli map elevation fbm --preset rolling --seed 5 --on terrain=out/examples/maps/river_terrain.npz -o out/examples/maps/river_elevation
  ```

- `1acdb8132abd` population centers, preset monocentric, on the river terrain + elevation

  ```bash
  python -m cli map population centers --preset monocentric --seed 5 --on terrain=out/examples/maps/river_terrain.npz --on elevation=out/examples/maps/river_elevation.npz -o out/examples/maps/river_population
  ```

- `d9472794ba8d` terrain noise, preset lakes

  ```bash
  python -m cli map terrain noise --preset lakes --seed 2 -o out/examples/maps/lakes_terrain
  ```

- `f8b98efafc21` terrain noise, preset inland (no water)

  ```bash
  python -m cli map terrain noise --preset inland --seed 2 -o out/examples/maps/inland_terrain
  ```

- `8b3c9ff417ee` elevation fbm, preset mountainous, 4 x 3 km at 20 m cells

  ```bash
  python -m cli map elevation fbm --preset mountainous --seed 4 --size 4000 3000 --cell-size 20 -o out/examples/maps/mountains_elevation
  ```

- `bf97c6d5a67a` elevation fbm, preset flat

  ```bash
  python -m cli map elevation fbm --preset flat -o out/examples/maps/flat_elevation
  ```

- `b35f78867469` population centers, preset sprawl

  ```bash
  python -m cli map population centers --preset sprawl --seed 6 -o out/examples/maps/sprawl_population
  ```

- `41284a1707f8` terrain noise, bare parameters: two rivers, half a coast, more parks

  ```bash
  python -m cli map terrain noise --seed 8 --set water_fraction=0.2 --set coast_strength=0.5 --set coast_angle_deg=270 --set rivers=2 --set river_width=60 --set vegetation_fraction=0.25 -o out/examples/maps/bare_terrain
  ```

- `7cc86bc90ff1` elevation fbm, bare parameters: ridged crests up to 120 m, no erosion

  ```bash
  python -m cli map elevation fbm --seed 8 --set max_elevation=120 --set ridge=0.6 --set exponent=1.4 --set erosion_iterations=0 -o out/examples/maps/bare_elevation
  ```

- `ed86d6583618` population centers, bare parameters: four gaussian centres

  ```bash
  python -m cli map population centers --seed 8 --set n_centers=4 --set profile=gaussian --set peak_density=10000 --set decay_length=500 -o out/examples/maps/bare_population
  ```

- `4bb9f95b3f34` terrain noise from a parameter file (coastal preset, sea in the west, a river)

  ```bash
  python -m cli map terrain noise --params runs/inputs/terrain_params.yaml --seed 9 -o out/examples/maps/params_terrain
  ```

- `ac718cb7b9a3` terrain image: lake, river and park drawn in runs/inputs/terrain.png

  ```bash
  python -m cli map terrain image --set path=runs/inputs/terrain.png -o out/examples/maps/image_terrain
  ```

- `30196fef574c` elevation image: runs/inputs/heightmap.png scaled to 0..60 m, on the image terrain

  ```bash
  python -m cli map elevation image --set path=runs/inputs/heightmap.png --set max_elevation=60 --on terrain=out/examples/maps/image_terrain.npz -o out/examples/maps/image_elevation
  ```

- `e04a70fc66a8` population centers, two centres, on the image terrain + elevation

  ```bash
  python -m cli map population centers --seed 10 --set n_centers=2 --on terrain=out/examples/maps/image_terrain.npz --on elevation=out/examples/maps/image_elevation.npz -o out/examples/maps/image_population
  ```

## 2. Road network (RoadGraph JSON)

- `234f458fa465` manhattan, preset manhattan_like (one-way avenues, rotated 29 deg)

  ```bash
  python -m cli generate manhattan --preset manhattan_like --seed 1 -o out/examples/net/manhattan_like.json
  ```

- `41a5766b33f2` manhattan, bare parameters: 6 x 12 grid, ring road, jitter, dropped streets

  ```bash
  python -m cli generate manhattan --avenues 6 --streets 12 --avenue-spacing 220 --street-spacing 90 --rotation 15 --jitter 3 --edge-drop 0.05 --boundary loop --seed 2 -o out/examples/net/manhattan_ring.json
  ```

- `65095a3342b1` manhattan with --config runs/inputs/wide_lanes.yaml (3.75 m lanes)

  ```bash
  python -m cli --config runs/inputs/wide_lanes.yaml generate manhattan --avenues 5 --streets 8 -o out/examples/net/manhattan_wide.json
  ```

- `c5d6a17157d3` lsystem, preset grid (a 10 x 10 grid grown from a corner)

  ```bash
  python -m cli generate lsystem --preset grid -o out/examples/net/lsystem_grid.json
  ```

- `25ceb2f2b113` lsystem, preset grid_organic (wobbly, some branches missing)

  ```bash
  python -m cli generate lsystem --preset grid_organic --seed 2 -o out/examples/net/organic.json
  ```

- `93e4d1ab27eb` lsystem, preset city without maps (flat grid streets only)

  ```bash
  python -m cli generate lsystem --preset city --seed 1 -o out/examples/net/city_nomaps.json
  ```

- `fb1176624cbf` lsystem, bare parameters: rotated 20 deg, jittered headings and lengths

  ```bash
  python -m cli generate lsystem --seed 3 --set 'extent=[1500,900]' --set 'block_size=[180,70]' --set angle_jitter_deg=4 --set length_jitter=0.1 --set rotation_deg=20 -o out/examples/net/lsystem_bare.json
  ```

- `6b4975a09dbc` lsystem city grown on the coastal maps: highways towards the centres, heights (draped)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/examples/maps/coast_terrain.npz --map-file elevation=out/examples/maps/coast_elevation.npz --map-file population=out/examples/maps/coast_population.npz -o out/examples/net/coast_city.json
  ```

- `341748593655` lsystem city on the coastal maps, --flat (same roads, no heights)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/examples/maps/coast_terrain.npz --map-file elevation=out/examples/maps/coast_elevation.npz --map-file population=out/examples/maps/coast_population.npz --flat -o out/examples/net/coast_city_flat.json
  ```

- `7dba22fed774` lsystem city grown on the river maps

  ```bash
  python -m cli generate lsystem --preset city --seed 5 --map-file terrain=out/examples/maps/river_terrain.npz --map-file elevation=out/examples/maps/river_elevation.npz --map-file population=out/examples/maps/river_population.npz -o out/examples/net/river_city.json
  ```

- `a1e8bf08e1f0` lsystem city on the image maps, no highways, max grade 8 %

  ```bash
  python -m cli generate lsystem --preset city --seed 10 --map-file terrain=out/examples/maps/image_terrain.npz --map-file elevation=out/examples/maps/image_elevation.npz --map-file population=out/examples/maps/image_population.npz --set highways=false --set max_grade=0.08 -o out/examples/net/image_city.json
  ```

- `bd2b5bb35ba0` drape the flat coastal city with the coastal elevation + terrain

  ```bash
  python -m cli drape out/examples/net/coast_city_flat.json --map-file elevation=out/examples/maps/coast_elevation.npz --map-file terrain=out/examples/maps/coast_terrain.npz -o out/examples/net/coast_city_draped.json
  ```

- `2df710c322a3` plot the coastal city graph

  ```bash
  python -m cli plot-graph out/examples/net/coast_city.json out/examples/net/coast_city.png
  ```

- `5979b8407e41` plot the manhattan ring with node ids

  ```bash
  python -m cli plot-graph out/examples/net/manhattan_ring.json out/examples/net/manhattan_ring.png --node-ids
  ```

- `ff542f4b9b0f` the coastal population map with the coastal city on top

  ```bash
  python -m cli plot-map out/examples/maps/coast_population.npz out/examples/net/coast_city_on_population.png --graph out/examples/net/coast_city.json
  ```

## 3. OpenDRIVE (.xodr) and validation

- `063741241dea` build-xodr: manhattan_like

  ```bash
  python -m cli build-xodr out/examples/net/manhattan_like.json out/examples/net/manhattan_like.xodr
  ```

- `e1d927634e4a` build-xodr: manhattan ring

  ```bash
  python -m cli build-xodr out/examples/net/manhattan_ring.json out/examples/net/manhattan_ring.xodr
  ```

- `f42f1db9a3c6` build-xodr: lsystem grid_organic, junction margin 3 m

  ```bash
  python -m cli build-xodr out/examples/net/organic.json out/examples/net/organic.xodr --junction-margin 3
  ```

- `13ebf755086b` build-xodr: coastal city with elevation profiles

  ```bash
  python -m cli build-xodr out/examples/net/coast_city.json out/examples/net/coast_city.xodr
  ```

- `783b23109fbf` drive-check: every junction movement of the coastal city in esmini

  ```bash
  python -m cli drive-check out/examples/net/coast_city.xodr
  ```

- `a095a6febcd9` pipeline: manhattan_like -> json, png, xodr, all validators

  ```bash
  python -m cli pipeline manhattan --preset manhattan_like --seed 1 -o out/examples/pl/manhattan_like
  ```

- `3ef2d6aaf62f` pipeline: lsystem grid + drive-check

  ```bash
  python -m cli pipeline lsystem --preset grid --drive-check -o out/examples/pl/grid
  ```

- `2baedd8c0c60` pipeline: lsystem city on generated maps (--maps), seed 7

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 -o out/examples/pl/city_maps
  ```

- `dad31cfc7e79` pipeline: lsystem city, maps chosen per kind with --map-set overrides

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --terrain noise --elevation fbm --population centers --map-set terrain.rivers=2 --map-set elevation.max_elevation=60 --map-set population.n_centers=1 --seed 4 -o out/examples/pl/city_rivers
  ```

- `84edfeb2622e` pipeline: lsystem city on the river map files + drive-check

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --seed 5 --map-file terrain=out/examples/maps/river_terrain.npz --map-file elevation=out/examples/maps/river_elevation.npz --map-file population=out/examples/maps/river_population.npz --drive-check -o out/examples/pl/river_city
  ```

## 4. 3D scene (buildings, ground)

- `7096c372e1bf` pipeline: lsystem grid_organic + buildings (.osgt)

  ```bash
  python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o out/examples/pl/organic_buildings
  ```

- `495cd073e19e` pipeline: manhattan_like + buildings

  ```bash
  python -m cli pipeline manhattan --preset manhattan_like --seed 1 --buildings -o out/examples/pl/manhattan_buildings
  ```

- `a75ac46317f5` pipeline: lsystem city on generated maps + ground mesh + buildings

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings -o out/examples/pl/city_scene
  ```

- `df96fadf942a` ground: coastal terrain levelled under the coastal city, with its buildings

  ```bash
  python -m cli ground out/examples/scene/coast_city_scene.osgt --map-file elevation=out/examples/maps/coast_elevation.npz --map-file terrain=out/examples/maps/coast_terrain.npz --graph out/examples/net/coast_city.json --buildings --seed 3
  ```

- `83a249752387` ground: bare mountainous terrain, no roads

  ```bash
  python -m cli ground out/examples/scene/mountains.osgt --map-file elevation=out/examples/maps/mountains_elevation.npz
  ```

## 5. Scenario (.xosc)

- `e698213b661a` manhattan_like, 100 vehicles, random junction choices

  ```bash
  python -m cli scenario out/examples/net/manhattan_like.xodr -o out/examples/net/manhattan_like.xosc --vehicles 100 --duration 30
  ```

- `be58277cc05b` manhattan_like pipeline, 80 vehicles on explicit shortest-path routes, 8-14 m/s

  ```bash
  python -m cli scenario out/examples/pl/manhattan_like.xodr -o out/examples/pl/manhattan_like_routes.xosc --vehicles 80 --mode explicit_routes --speed-min 8 --speed-max 14 --min-gap 15 --duration 30 --seed 2
  ```

- `b829a179eecb` grid_organic with its buildings as scene graph

  ```bash
  python -m cli scenario out/examples/pl/organic_buildings.xodr -o out/examples/pl/organic_buildings.xosc --vehicles 60 --duration 30 --scenegraph out/examples/pl/organic_buildings_buildings.osgt
  ```

- `9f82122169a2` manhattan_like with its buildings as scene graph

  ```bash
  python -m cli scenario out/examples/pl/manhattan_buildings.xodr -o out/examples/pl/manhattan_buildings.xosc --vehicles 100 --duration 30 --scenegraph out/examples/pl/manhattan_buildings_buildings.osgt
  ```

- `68fefd666ab7` coastal city on its ground + buildings scene

  ```bash
  python -m cli scenario out/examples/net/coast_city.xodr -o out/examples/net/coast_city.xosc --vehicles 80 --duration 30 --scenegraph out/examples/scene/coast_city_scene.osgt
  ```

- `cb9d940b0849` generated-maps city on its ground + buildings scene

  ```bash
  python -m cli scenario out/examples/pl/city_scene.xodr -o out/examples/pl/city_scene.xosc --vehicles 80 --duration 30 --scenegraph out/examples/pl/city_scene_scene.osgt
  ```

## 6. Simulation (headless esmini)

- `0bc96a18db4c` manhattan_like, random traffic

  ```bash
  python -m cli simulate out/examples/net/manhattan_like.xosc
  ```

- `5ce2a2457ed7` manhattan_like, explicit routes, 0.02 s timestep, no recording

  ```bash
  python -m cli simulate out/examples/pl/manhattan_like_routes.xosc --timestep 0.02 --no-record --no-csv
  ```

- `5cc2b1ab4f3b` grid_organic with buildings

  ```bash
  python -m cli simulate out/examples/pl/organic_buildings.xosc
  ```

- `99b10e867a5f` manhattan_like with buildings

  ```bash
  python -m cli simulate out/examples/pl/manhattan_buildings.xosc
  ```

- `ac96d08ca541` coastal city on ground + buildings

  ```bash
  python -m cli simulate out/examples/net/coast_city.xosc
  ```

- `b53fa05dfae8` generated-maps city on ground + buildings

  ```bash
  python -m cli simulate out/examples/pl/city_scene.xosc
  ```

- `de96553ef40d` whole chain in one pipeline: maps, city, ground, buildings, esmini run

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings --simulate --sim-vehicles 60 --sim-duration 20 -o out/examples/pl/city_full
  ```

- `f035568101d1` whole chain in one pipeline: manhattan_like with buildings, esmini run

  ```bash
  python -m cli pipeline manhattan --preset manhattan_like --seed 1 --buildings --simulate --sim-vehicles 100 -o out/examples/pl/manhattan_full
  ```

## 7. esmini GUI

- `909299126a95` esmini: manhattan_like with buildings, top camera

  ```bash
  python -m cli simulate out/examples/pl/manhattan_buildings.xosc --gui
  ```

- `2e45f514ec2e` esmini: coastal city on its ground + buildings

  ```bash
  python -m cli simulate out/examples/net/coast_city.xosc --gui
  ```

- `3369a331a99f` odrviewer: manhattan_like with buildings, random cars

  ```bash
  python -m cli view out/examples/pl/manhattan_buildings.xodr --scenegraph out/examples/pl/manhattan_buildings_buildings.osgt
  ```

- `97f8879ee028` odrviewer: grid_organic with buildings, 2 cars / 100 m

  ```bash
  python -m cli view out/examples/pl/organic_buildings.xodr --scenegraph out/examples/pl/organic_buildings_buildings.osgt --density 2
  ```

- `6539301a9834` odrviewer: coastal city with heights, on its ground + buildings

  ```bash
  python -m cli view out/examples/net/coast_city.xodr --scenegraph out/examples/scene/coast_city_scene.osgt
  ```

- `cbc76231e493` odrviewer: the one-command city (maps, ground, buildings) from phase 6

  ```bash
  python -m cli view out/examples/pl/city_full.xodr --scenegraph out/examples/pl/city_full_scene.osgt
  ```

## 8. Road geometry (stage A): fitted curves, metrics, align, curved streets

- `9852ee91656d` build-xodr: coastal city with fitted curves, banking and junction planes

  ```bash
  python -m cli build-xodr out/examples/net/coast_city.json out/examples/geometry/coast_city.xodr
  ```

- `2a9597708ab6` metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)

  ```bash
  python -m cli metrics out/examples/geometry/coast_city.xodr --json out/examples/geometry/coast_city_metrics.json
  ```

- `f72fb09c3674` drive-check: coastal city, every junction movement and every lane end to end

  ```bash
  python -m cli drive-check out/examples/geometry/coast_city.xodr
  ```

- `65bda359a875` align: coastal city bends capped where their curves would leave legal ground

  ```bash
  python -m cli align out/examples/net/coast_city.json --map-file terrain=out/examples/maps/coast_terrain.npz --map-file elevation=out/examples/maps/coast_elevation.npz -o out/examples/geometry/coast_city.aligned.json
  ```

- `75b46161625c` metrics: build-failure sweep over 20 lsystem cities on generated maps

  ```bash
  python -m cli metrics --sweep 20 --json out/examples/geometry/sweep20.json
  ```

- `16f9f56c8071` pipeline: lsystem organic_curved (drifting, bowed streets), drive-check

  ```bash
  python -m cli pipeline lsystem --preset organic_curved --seed 2 --allow-traps --drive-check -o out/examples/geometry/organic_curved
  ```

- `7eb244b18978` pipeline: lsystem hill_town on maps (contour streets), ground, buildings, drive-check

  ```bash
  python -m cli pipeline lsystem --preset hill_town --seed 1 --maps --allow-traps --ground --buildings --drive-check -o out/examples/geometry/hill_town
  ```
