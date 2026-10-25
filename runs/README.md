# Example runs

Every successful `python -m cli` command gets its own folder `out/<hash>/`
(git-ignored), named by a 12-digit hash of what defines the run: the
command, the arguments that differ from their defaults and the parameters
that differ from `roadgen/defaults.yaml`. The folder holds the command's
outputs (`-o` only names them) and `run.yaml`, which shows exactly those
non-default parameters. The same inputs, however they were typed, give the
same hash; running them again replaces the folder. See
[CLI reference → history](../docs/cli-reference.md#history).

```bash
python -m cli history                 # all runs: hash, note, command
python -m cli history --grep "7 gui"  # filter by note or command
python -m cli HASH                    # run it again (same as: replay HASH)
python -m cli run out/HASH/run.yaml   # the same, from its run file (copy and edit it for a new run)
python -m cli replay HASH --show      # only print the command and the folder
python -m cli --note "my run" ...     # describe a run in its run.yaml
python -m cli --no-history ...        # throwaway run: no folder, -o is a path
```

In Docker, prefix the commands with `docker compose run --rm -T dev`, or
with `docker compose run --rm gui` for phase 7 (needs a desktop session; see
[docs/docker.md](../docs/docker.md)). The phase 7 views (`odrviewer`) stay open
until you close the window; the `simulate --gui` runs close when the scenario
ends.

The runs below are made by [`examples.sh`](examples.sh) (which also
regenerates the input images in [`inputs/`](inputs/)); this page is written
from the run folders by [`make_readme.py`](make_readme.py). Later runs read
files from the folders of earlier ones, so on a fresh checkout run
`examples.sh` (or replay the hashes in order). Each note starts with its
phase (`1 maps |`, `2 network |`, `3 xodr |`, `4 scene |`, `5 scenario |`,
`6 simulate |`, `7 gui |`, `8 geometry |`):

```bash
# everything, oldest first (phase 7 needs the gui service)
for h in $(python -m cli history | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
# one phase
for h in $(python -m cli history --grep "1 maps |" | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
```

or one chain from the list below.

## From scratch to the GUI

**Manhattan grid with buildings: pipeline, scenario, simulation, GUI:** `c2dadca3254d` → `74a83f83d331` → `d6cddf84921b`

**Manhattan grid, one stage per command:** `96cf0788afd7` → `8041950f9676` → `095e71ae01ba` → `def27a16b5da`

**Organic lsystem grid with buildings:** `91cb2656a43b` → `a4c12a47636d` → `b5abc4460de9`

**Coastal city on input maps, with ground and buildings:** `6ea43a5eb9f7` → `28df1d3f8e6f` → `7a18cf892540` → `917a6d6b33dc` → `696e1a3bd6a6` → `aaf412c7a61e` → `a139de222223`

**Road geometry: build, measure and drive the coastal city:** `f6913f35fdf4` → `259d8d1f271c` → `6042ecc9fd09`

**City on generated maps in one command, then the GUI:** `b3782f374977`

## 1. Input maps

- `6ea43a5eb9f7` terrain noise, preset coastal (sea on one side)

  ```bash
  python -m cli map terrain noise --preset coastal --seed 3 --origin -200 -200 --size 3400 2800 -o coast_terrain
  ```

- `28df1d3f8e6f` elevation fbm, preset rolling, on the coastal terrain (water lies flat in the valleys)

  ```bash
  python -m cli map elevation fbm --preset rolling --seed 3 --origin -200 -200 --size 3400 2800 --on terrain=out/6ea43a5eb9f7/coast_terrain.npz -o coast_elevation
  ```

- `7a18cf892540` population centers, preset polycentric, on the coastal terrain + elevation

  ```bash
  python -m cli map population centers --preset polycentric --seed 3 --origin -200 -200 --size 3400 2800 --on terrain=out/6ea43a5eb9f7/coast_terrain.npz --on elevation=out/28df1d3f8e6f/coast_elevation.npz -o coast_population
  ```

- `0586de5f1335` terrain noise, preset river_city (one wide river)

  ```bash
  python -m cli map terrain noise --preset river_city --seed 5 -o river_terrain
  ```

- `8a5b52de4967` elevation fbm, preset rolling, on the river terrain

  ```bash
  python -m cli map elevation fbm --preset rolling --seed 5 --on terrain=out/0586de5f1335/river_terrain.npz -o river_elevation
  ```

- `e18763460584` population centers, preset monocentric, on the river terrain + elevation

  ```bash
  python -m cli map population centers --preset monocentric --seed 5 --on terrain=out/0586de5f1335/river_terrain.npz --on elevation=out/8a5b52de4967/river_elevation.npz -o river_population
  ```

- `41a0a3d2f014` terrain noise, preset lakes

  ```bash
  python -m cli map terrain noise --preset lakes --seed 2 -o lakes_terrain
  ```

- `cfe41278a3e3` terrain noise, preset inland (no water)

  ```bash
  python -m cli map terrain noise --preset inland --seed 2 -o inland_terrain
  ```

- `707006d4edf6` elevation fbm, preset mountainous, 4 x 3 km at 20 m cells

  ```bash
  python -m cli map elevation fbm --preset mountainous --seed 4 --size 4000 3000 --cell-size 20 -o mountains_elevation
  ```

- `5693c8b08fb2` elevation fbm, preset flat

  ```bash
  python -m cli map elevation fbm --preset flat -o flat_elevation
  ```

- `347add19c53b` population centers, preset sprawl

  ```bash
  python -m cli map population centers --preset sprawl --seed 6 -o sprawl_population
  ```

- `1c8cee22cf08` terrain noise, bare parameters: two rivers, half a coast, more parks

  ```bash
  python -m cli map terrain noise --seed 8 --set water_fraction=0.2 --set coast_strength=0.5 --set coast_angle_deg=270 --set rivers=2 --set river_width=60 --set vegetation_fraction=0.25 -o bare_terrain
  ```

- `cb0b4efc547b` elevation fbm, bare parameters: ridged crests up to 120 m, no erosion

  ```bash
  python -m cli map elevation fbm --seed 8 --set max_elevation=120 --set ridge=0.6 --set exponent=1.4 --set erosion_iterations=0 -o bare_elevation
  ```

- `294da2fa5233` population centers, bare parameters: four gaussian centres

  ```bash
  python -m cli map population centers --seed 8 --set n_centers=4 --set profile=gaussian --set peak_density=10000 --set decay_length=500 -o bare_population
  ```

- `5a131d17d64f` terrain noise from a parameter file (coastal preset, sea in the west, a river)

  ```bash
  python -m cli map terrain noise --params runs/inputs/terrain_params.yaml --seed 9 -o params_terrain
  ```

- `eba2a61e963b` terrain image: lake, river and park drawn in runs/inputs/terrain.png

  ```bash
  python -m cli map terrain image --set path=runs/inputs/terrain.png -o image_terrain
  ```

- `6545241184bf` elevation image: runs/inputs/heightmap.png scaled to 0..60 m, on the image terrain

  ```bash
  python -m cli map elevation image --set path=runs/inputs/heightmap.png --set max_elevation=60 --on terrain=out/eba2a61e963b/image_terrain.npz -o image_elevation
  ```

- `ca1035a92d48` population centers, two centres, on the image terrain + elevation

  ```bash
  python -m cli map population centers --seed 10 --set n_centers=2 --on terrain=out/eba2a61e963b/image_terrain.npz --on elevation=out/6545241184bf/image_elevation.npz -o image_population
  ```

## 2. Road network (RoadGraph JSON)

- `96cf0788afd7` manhattan, preset manhattan_like (one-way avenues, rotated 29 deg)

  ```bash
  python -m cli generate manhattan --preset manhattan_like --seed 1 -o manhattan_like.json
  ```

- `f6b9e5be6e95` manhattan, bare parameters: 6 x 12 grid, ring road, jitter, dropped streets

  ```bash
  python -m cli generate manhattan --avenues 6 --streets 12 --avenue-spacing 220 --street-spacing 90 --rotation 15 --jitter 3 --edge-drop 0.05 --boundary loop --seed 2 -o manhattan_ring.json
  ```

- `a205b6835272` manhattan with --config runs/inputs/wide_lanes.yaml (3.75 m lanes)

  ```bash
  python -m cli --config runs/inputs/wide_lanes.yaml generate manhattan --avenues 5 --streets 8 -o manhattan_wide.json
  ```

- `6f67948a90cf` lsystem, preset grid (a 10 x 10 grid grown from a corner)

  ```bash
  python -m cli generate lsystem --preset grid -o lsystem_grid.json
  ```

- `3e111dd0e011` lsystem, preset grid_organic (wobbly, some branches missing)

  ```bash
  python -m cli generate lsystem --preset grid_organic --seed 2 -o organic.json
  ```

- `f05235dc08d9` lsystem, preset city without maps (flat grid streets only)

  ```bash
  python -m cli generate lsystem --preset city --seed 1 -o city_nomaps.json
  ```

- `37043cbde9a9` lsystem, bare parameters: rotated 20 deg, jittered headings and lengths

  ```bash
  python -m cli generate lsystem --seed 3 --set extent=[1500,900] --set block_size=[180,70] --set angle_jitter_deg=4 --set length_jitter=0.1 --set rotation_deg=20 -o lsystem_bare.json
  ```

- `917a6d6b33dc` lsystem city grown on the coastal maps: highways towards the centres, heights (draped)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file population=out/7a18cf892540/coast_population.npz -o coast_city.json
  ```

- `eb6f86beab73` lsystem city on the coastal maps, --flat (same roads, no heights)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file population=out/7a18cf892540/coast_population.npz --flat -o coast_city_flat.json
  ```

- `dde6128fb778` lsystem city grown on the river maps

  ```bash
  python -m cli generate lsystem --preset city --seed 5 --map-file terrain=out/0586de5f1335/river_terrain.npz --map-file elevation=out/8a5b52de4967/river_elevation.npz --map-file population=out/e18763460584/river_population.npz -o river_city.json
  ```

- `ff7bda1df7bf` lsystem city on the image maps, no highways, max grade 8 %

  ```bash
  python -m cli generate lsystem --preset city --seed 10 --map-file terrain=out/eba2a61e963b/image_terrain.npz --map-file elevation=out/6545241184bf/image_elevation.npz --map-file population=out/ca1035a92d48/image_population.npz --set highways=false --set max_grade=0.08 -o image_city.json
  ```

- `1dba7a1749ac` drape the flat coastal city with the coastal elevation + terrain

  ```bash
  python -m cli drape out/eb6f86beab73/coast_city_flat.json --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz -o coast_city_draped.json
  ```

- `7e5b7aa01bc0` plot the coastal city graph

  ```bash
  python -m cli plot-graph out/917a6d6b33dc/coast_city.json coast_city.png
  ```

- `ec6195bec0e5` plot the manhattan ring with node ids

  ```bash
  python -m cli plot-graph out/f6b9e5be6e95/manhattan_ring.json manhattan_ring.png --node-ids
  ```

- `76b9bf7d8355` the coastal population map with the coastal city on top

  ```bash
  python -m cli plot-map out/7a18cf892540/coast_population.npz coast_city_on_population.png --graph out/917a6d6b33dc/coast_city.json
  ```

## 3. OpenDRIVE (.xodr) and validation

- `8041950f9676` build-xodr: manhattan_like

  ```bash
  python -m cli build-xodr out/96cf0788afd7/manhattan_like.json manhattan_like.xodr
  ```

- `22574e9eac77` build-xodr: manhattan ring

  ```bash
  python -m cli build-xodr out/f6b9e5be6e95/manhattan_ring.json manhattan_ring.xodr
  ```

- `53e7746b1454` build-xodr: lsystem grid_organic, junction margin 3 m

  ```bash
  python -m cli build-xodr out/3e111dd0e011/organic.json organic.xodr --junction-margin 3
  ```

- `e17ca502d42f` pipeline: manhattan_like -> json, png, xodr, all validators

  ```bash
  python -m cli pipeline manhattan --preset manhattan_like --seed 1 -o manhattan_like
  ```

- `a0fa6335f51e` pipeline: lsystem grid + drive-check

  ```bash
  python -m cli pipeline lsystem --preset grid --drive-check -o grid
  ```

- `f3eacaa85e32` pipeline: lsystem city on generated maps (--maps), seed 7

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 -o city_maps
  ```

- `1afcfd14e2ea` pipeline: lsystem city, maps chosen per kind with --map-set overrides

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --terrain noise --elevation fbm --population centers --map-set terrain.rivers=2 --map-set elevation.max_elevation=60 --map-set population.n_centers=1 --seed 4 -o city_rivers
  ```

- `a4318ffe9c44` pipeline: lsystem city on the river map files + drive-check

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --seed 5 --map-file terrain=out/0586de5f1335/river_terrain.npz --map-file elevation=out/8a5b52de4967/river_elevation.npz --map-file population=out/e18763460584/river_population.npz --drive-check -o river_city
  ```

## 4. 3D scene (buildings, ground)

- `91cb2656a43b` pipeline: lsystem grid_organic + buildings (.osgt)

  ```bash
  python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o organic_buildings
  ```

- `c2dadca3254d` pipeline: manhattan_like + buildings

  ```bash
  python -m cli pipeline manhattan --preset manhattan_like --seed 1 --buildings -o manhattan_buildings
  ```

- `02a6a9d152b6` pipeline: lsystem city on generated maps + ground mesh + buildings

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings -o city_scene
  ```

- `696e1a3bd6a6` ground: coastal terrain levelled under the coastal city, with its buildings

  ```bash
  python -m cli ground coast_city_scene.osgt --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz --graph out/917a6d6b33dc/coast_city.json --buildings --seed 3
  ```

- `779bb5ccc01c` ground: bare mountainous terrain, no roads

  ```bash
  python -m cli ground mountains.osgt --map-file elevation=out/707006d4edf6/mountains_elevation.npz
  ```

## 5. Scenario (.xosc)

- `095e71ae01ba` manhattan_like, 100 vehicles, random junction choices

  ```bash
  python -m cli scenario out/8041950f9676/manhattan_like.xodr -o manhattan_like.xosc --vehicles 100 --duration 30
  ```

- `c1b852debe68` manhattan_like pipeline, 80 vehicles on explicit shortest-path routes, 8-14 m/s

  ```bash
  python -m cli scenario out/e17ca502d42f/manhattan_like.xodr -o manhattan_like_routes.xosc --vehicles 80 --mode explicit_routes --speed-min 8 --speed-max 14 --min-gap 15 --duration 30 --seed 2
  ```

- `a4c12a47636d` grid_organic with its buildings as scene graph

  ```bash
  python -m cli scenario out/91cb2656a43b/organic_buildings.xodr -o organic_buildings.xosc --vehicles 60 --duration 30 --scenegraph out/91cb2656a43b/organic_buildings_buildings.osgt
  ```

- `74a83f83d331` manhattan_like with its buildings as scene graph

  ```bash
  python -m cli scenario out/c2dadca3254d/manhattan_buildings.xodr -o manhattan_buildings.xosc --vehicles 100 --duration 30 --scenegraph out/c2dadca3254d/manhattan_buildings_buildings.osgt
  ```

- `aaf412c7a61e` coastal city on its ground + buildings scene

  ```bash
  python -m cli scenario out/f6913f35fdf4/coast_city.xodr -o coast_city.xosc --vehicles 80 --duration 30 --scenegraph out/696e1a3bd6a6/coast_city_scene.osgt
  ```

- `cf91d56ddf68` generated-maps city on its ground + buildings scene

  ```bash
  python -m cli scenario out/02a6a9d152b6/city_scene.xodr -o city_scene.xosc --vehicles 80 --duration 30 --scenegraph out/02a6a9d152b6/city_scene_scene.osgt
  ```

## 6. Simulation (headless esmini)

- `def27a16b5da` manhattan_like, random traffic

  ```bash
  python -m cli simulate out/095e71ae01ba/manhattan_like.xosc
  ```

- `348b181f56c0` manhattan_like, explicit routes, 0.02 s timestep, no recording

  ```bash
  python -m cli simulate out/c1b852debe68/manhattan_like_routes.xosc --timestep 0.02 --no-record --no-csv
  ```

- `b5abc4460de9` grid_organic with buildings

  ```bash
  python -m cli simulate out/a4c12a47636d/organic_buildings.xosc
  ```

- `d6cddf84921b` manhattan_like with buildings

  ```bash
  python -m cli simulate out/74a83f83d331/manhattan_buildings.xosc
  ```

- `a139de222223` coastal city on ground + buildings

  ```bash
  python -m cli simulate out/aaf412c7a61e/coast_city.xosc
  ```

- `53f6182f8e2b` generated-maps city on ground + buildings

  ```bash
  python -m cli simulate out/cf91d56ddf68/city_scene.xosc
  ```

- `b3782f374977` whole chain in one pipeline: maps, city, ground, buildings, esmini run

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings --simulate --sim-vehicles 60 --sim-duration 20 -o city_full
  ```

- `3a616fdf488f` whole chain in one pipeline: manhattan_like with buildings, esmini run

  ```bash
  python -m cli pipeline manhattan --preset manhattan_like --seed 1 --buildings --simulate --sim-vehicles 100 -o manhattan_full
  ```

## 7. esmini GUI

## 8. Road geometry (stage A): fitted curves, metrics, align, curved streets

- `f6913f35fdf4` build-xodr: coastal city with fitted curves, banking and junction planes

  ```bash
  python -m cli build-xodr out/917a6d6b33dc/coast_city.json coast_city.xodr
  ```

- `259d8d1f271c` metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)

  ```bash
  python -m cli metrics out/f6913f35fdf4/coast_city.xodr --json coast_city_metrics.json
  ```

- `6042ecc9fd09` drive-check: coastal city, every junction movement and every lane end to end

  ```bash
  python -m cli drive-check out/f6913f35fdf4/coast_city.xodr
  ```

- `c20d3b20d5f0` align: coastal city bends capped where their curves would leave legal ground

  ```bash
  python -m cli align out/917a6d6b33dc/coast_city.json --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz -o coast_city.aligned.json
  ```

- `059c0c2a06b5` metrics: build-failure sweep over 20 lsystem cities on generated maps

  ```bash
  python -m cli metrics --sweep 20 --json sweep20.json
  ```

- `9623941ffedb` pipeline: lsystem organic_curved (drifting, bowed streets), drive-check

  ```bash
  python -m cli pipeline lsystem --preset organic_curved --seed 2 --allow-traps --drive-check -o organic_curved
  ```

- `50712981312c` pipeline: lsystem hill_town on maps (contour streets), ground, buildings, drive-check

  ```bash
  python -m cli pipeline lsystem --preset hill_town --seed 1 --maps --allow-traps --ground --buildings --drive-check -o hill_town
  ```
