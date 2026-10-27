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
`6 simulate |`, `7 gui |`, `8 geometry |`, `9 diversity |`):

```bash
# everything, oldest first (phase 7 needs the gui service)
for h in $(python -m cli history | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
# one phase
for h in $(python -m cli history --grep "1 maps |" | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
```

or one chain from the list below.

Every run that wrote a road network also lists a `view` command for it:
odrviewer with random cars, on the run's 3D scene (ground and buildings)
where it has one. Run it with the gui service in Docker
(`docker compose run --rm gui python -m cli view ...`); a view is not
recorded as a run.

## From scratch to the GUI

**Manhattan-style grid with buildings: pipeline, scenario, simulation, GUI:** `05a1dc23ae2b` → `c3abc191f8ad` → `cb0ecaa10b0b`

**Manhattan-style grid, one stage per command:** `3a4b5ef07762` → `8b4c71462366` → `2308a6946c86` → `dcac112546fd`

**Organic lsystem grid with buildings:** `91cb2656a43b` → `a4c12a47636d` → `b5abc4460de9`

**Coastal city on input maps, with ground and buildings:** `19dbda6ba2a3` → `df2efcf1515d` → `2875d0c5f0a0` → `1423b47a4348` → `87811a4affa6` → `784aea2c024c` → `07dc3e231fa7`

**Road geometry: build, measure and drive the coastal city:** `462244afda2e` → `b1a6e1fd7411` → `3f63f67639a5`

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

- `19dbda6ba2a3` terrain noise, preset coastal (sea on one side)

  ```bash
  python -m cli map terrain noise --preset coastal --seed 3 --origin -200 -200 --size 3400 2800 -o coast_terrain
  ```

- `df2efcf1515d` elevation fbm, preset rolling, on the coastal terrain (water lies flat in the valleys)

  ```bash
  python -m cli map elevation fbm --preset rolling --seed 3 --origin -200 -200 --size 3400 2800 --on terrain=out/19dbda6ba2a3/coast_terrain.npz -o coast_elevation
  ```

- `2875d0c5f0a0` population centers, preset polycentric, on the coastal terrain + elevation

  ```bash
  python -m cli map population centers --preset polycentric --seed 3 --origin -200 -200 --size 3400 2800 --on terrain=out/19dbda6ba2a3/coast_terrain.npz --on elevation=out/df2efcf1515d/coast_elevation.npz -o coast_population
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

- `917a6d6b33dc` lsystem city grown on the coastal maps: highways towards the centres, heights (draped)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file population=out/7a18cf892540/coast_population.npz -o coast_city.json
  ```

- `1dba7a1749ac` drape the flat coastal city with the coastal elevation + terrain

  ```bash
  python -m cli drape out/eb6f86beab73/coast_city_flat.json --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz -o coast_city_draped.json
  ```

- `7e5b7aa01bc0` plot the coastal city graph

  ```bash
  python -m cli plot-graph out/917a6d6b33dc/coast_city.json coast_city.png
  ```

- `76b9bf7d8355` the coastal population map with the coastal city on top

  ```bash
  python -m cli plot-map out/7a18cf892540/coast_population.npz coast_city_on_population.png --graph out/917a6d6b33dc/coast_city.json
  ```

- `3a4b5ef07762` lsystem, preset manhattan_like (alternating one-way avenues and streets, ring road, rotated 29 deg)

  ```bash
  python -m cli generate lsystem --preset manhattan_like --seed 1 -o manhattan_like.json
  ```

- `9357ea111398` lsystem grid, bare parameters: 6 x 12 grid, ring road, heading noise, dropped streets

  ```bash
  python -m cli generate lsystem --preset grid --set extent=[1100,990] --set block_size=[220,90] --set rotation_deg=15 --set angle_jitter_deg=1.5 --set edge_drop_prob=0.05 --set boundary=ring --seed 2 -o grid_ring.json
  ```

- `caba17774f9a` lsystem grid with --config runs/inputs/wide_lanes.yaml (3.75 m lanes)

  ```bash
  python -m cli --config runs/inputs/wide_lanes.yaml generate lsystem --preset grid --set extent=[1000,560] -o grid_wide.json
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

- `1423b47a4348` lsystem city grown on the coastal maps: highways towards the centres, heights (draped)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/19dbda6ba2a3/coast_terrain.npz --map-file elevation=out/df2efcf1515d/coast_elevation.npz --map-file population=out/2875d0c5f0a0/coast_population.npz -o coast_city.json
  ```

- `03868f0c847e` lsystem city on the coastal maps, --flat (same roads, no heights)

  ```bash
  python -m cli generate lsystem --preset city --seed 4 --map-file terrain=out/19dbda6ba2a3/coast_terrain.npz --map-file elevation=out/df2efcf1515d/coast_elevation.npz --map-file population=out/2875d0c5f0a0/coast_population.npz --flat -o coast_city_flat.json
  ```

- `dde6128fb778` lsystem city grown on the river maps

  ```bash
  python -m cli generate lsystem --preset city --seed 5 --map-file terrain=out/0586de5f1335/river_terrain.npz --map-file elevation=out/8a5b52de4967/river_elevation.npz --map-file population=out/e18763460584/river_population.npz -o river_city.json
  ```

- `ff7bda1df7bf` lsystem city on the image maps, no highways, max grade 8 %

  ```bash
  python -m cli generate lsystem --preset city --seed 10 --map-file terrain=out/eba2a61e963b/image_terrain.npz --map-file elevation=out/6545241184bf/image_elevation.npz --map-file population=out/ca1035a92d48/image_population.npz --set highways=false --set max_grade=0.08 -o image_city.json
  ```

- `e93321c62bb0` drape the flat coastal city with the coastal elevation + terrain

  ```bash
  python -m cli drape out/03868f0c847e/coast_city_flat.json --map-file elevation=out/df2efcf1515d/coast_elevation.npz --map-file terrain=out/19dbda6ba2a3/coast_terrain.npz -o coast_city_draped.json
  ```

- `4dbed407744b` plot the coastal city graph

  ```bash
  python -m cli plot-graph out/1423b47a4348/coast_city.json coast_city.png
  ```

- `8dd81e04c524` plot the ring-road grid with node ids

  ```bash
  python -m cli plot-graph out/9357ea111398/grid_ring.json grid_ring.png --node-ids
  ```

- `361cee72b004` the coastal population map with the coastal city on top

  ```bash
  python -m cli plot-map out/2875d0c5f0a0/coast_population.npz coast_city_on_population.png --graph out/1423b47a4348/coast_city.json
  ```

## 3. OpenDRIVE (.xodr) and validation

- `8b4c71462366` build-xodr: manhattan_like

  ```bash
  python -m cli build-xodr out/3a4b5ef07762/manhattan_like.json manhattan_like.xodr
  python -m cli view out/8b4c71462366/manhattan_like.xodr   # look at it
  ```

- `fd55ff5bf4da` build-xodr: ring-road grid

  ```bash
  python -m cli build-xodr out/9357ea111398/grid_ring.json grid_ring.xodr
  python -m cli view out/fd55ff5bf4da/grid_ring.xodr   # look at it
  ```

- `53e7746b1454` build-xodr: lsystem grid_organic, junction margin 3 m

  ```bash
  python -m cli build-xodr out/3e111dd0e011/organic.json organic.xodr --junction-margin 3
  python -m cli view out/53e7746b1454/organic.xodr   # look at it
  ```

- `6c4ca581b5a4` pipeline: manhattan_like -> json, png, xodr, all validators

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 -o manhattan_like
  python -m cli view out/6c4ca581b5a4/manhattan_like.xodr   # look at it
  ```

- `a0fa6335f51e` pipeline: lsystem grid + drive-check

  ```bash
  python -m cli pipeline lsystem --preset grid --drive-check -o grid
  python -m cli view out/a0fa6335f51e/grid.xodr   # look at it
  ```

- `f3eacaa85e32` pipeline: lsystem city on generated maps (--maps), seed 7

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 -o city_maps
  python -m cli view out/f3eacaa85e32/city_maps.xodr   # look at it
  ```

- `1afcfd14e2ea` pipeline: lsystem city, maps chosen per kind with --map-set overrides

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --terrain noise --elevation fbm --population centers --map-set terrain.rivers=2 --map-set elevation.max_elevation=60 --map-set population.n_centers=1 --seed 4 -o city_rivers
  python -m cli view out/1afcfd14e2ea/city_rivers.xodr   # look at it
  ```

- `a4318ffe9c44` pipeline: lsystem city on the river map files + drive-check

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --seed 5 --map-file terrain=out/0586de5f1335/river_terrain.npz --map-file elevation=out/8a5b52de4967/river_elevation.npz --map-file population=out/e18763460584/river_population.npz --drive-check -o river_city
  python -m cli view out/a4318ffe9c44/river_city.xodr   # look at it
  ```

## 4. 3D scene (buildings, ground)

- `696e1a3bd6a6` ground: coastal terrain levelled under the coastal city, with its buildings

  ```bash
  python -m cli ground coast_city_scene.osgt --map-file elevation=out/28df1d3f8e6f/coast_elevation.npz --map-file terrain=out/6ea43a5eb9f7/coast_terrain.npz --graph out/917a6d6b33dc/coast_city.json --buildings --seed 3
  ```

- `91cb2656a43b` pipeline: lsystem grid_organic + buildings (.osgt)

  ```bash
  python -m cli pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o organic_buildings
  python -m cli view out/91cb2656a43b/organic_buildings.xodr --scenegraph out/91cb2656a43b/organic_buildings_buildings.osgt   # look at it
  ```

- `05a1dc23ae2b` pipeline: manhattan_like + buildings

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 --buildings -o manhattan_buildings
  python -m cli view out/05a1dc23ae2b/manhattan_buildings.xodr --scenegraph out/05a1dc23ae2b/manhattan_buildings_buildings.osgt   # look at it
  ```

- `02a6a9d152b6` pipeline: lsystem city on generated maps + ground mesh + buildings

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings -o city_scene
  python -m cli view out/02a6a9d152b6/city_scene.xodr --scenegraph out/02a6a9d152b6/city_scene_scene.osgt   # look at it
  ```

- `87811a4affa6` ground: coastal terrain levelled under the coastal city, with its buildings

  ```bash
  python -m cli ground coast_city_scene.osgt --map-file elevation=out/df2efcf1515d/coast_elevation.npz --map-file terrain=out/19dbda6ba2a3/coast_terrain.npz --graph out/1423b47a4348/coast_city.json --buildings --seed 3
  ```

- `779bb5ccc01c` ground: bare mountainous terrain, no roads

  ```bash
  python -m cli ground mountains.osgt --map-file elevation=out/707006d4edf6/mountains_elevation.npz
  ```

## 5. Scenario (.xosc)

- `2308a6946c86` manhattan_like, 100 vehicles, random junction choices

  ```bash
  python -m cli scenario out/8b4c71462366/manhattan_like.xodr -o manhattan_like.xosc --vehicles 100 --duration 30
  ```

- `10c8201dcd3e` manhattan_like pipeline, 80 vehicles on explicit shortest-path routes, 8-14 m/s

  ```bash
  python -m cli scenario out/6c4ca581b5a4/manhattan_like.xodr -o manhattan_like_routes.xosc --vehicles 80 --mode explicit_routes --speed-min 8 --speed-max 14 --min-gap 15 --duration 30 --seed 2
  ```

- `a4c12a47636d` grid_organic with its buildings as scene graph

  ```bash
  python -m cli scenario out/91cb2656a43b/organic_buildings.xodr -o organic_buildings.xosc --vehicles 60 --duration 30 --scenegraph out/91cb2656a43b/organic_buildings_buildings.osgt
  ```

- `c3abc191f8ad` manhattan_like with its buildings as scene graph

  ```bash
  python -m cli scenario out/05a1dc23ae2b/manhattan_buildings.xodr -o manhattan_buildings.xosc --vehicles 100 --duration 30 --scenegraph out/05a1dc23ae2b/manhattan_buildings_buildings.osgt
  ```

- `784aea2c024c` coastal city on its ground + buildings scene

  ```bash
  python -m cli scenario out/462244afda2e/coast_city.xodr -o coast_city.xosc --vehicles 80 --duration 30 --scenegraph out/87811a4affa6/coast_city_scene.osgt
  ```

- `cf91d56ddf68` generated-maps city on its ground + buildings scene

  ```bash
  python -m cli scenario out/02a6a9d152b6/city_scene.xodr -o city_scene.xosc --vehicles 80 --duration 30 --scenegraph out/02a6a9d152b6/city_scene_scene.osgt
  ```

## 6. Simulation (headless esmini)

- `dcac112546fd` manhattan_like, random traffic

  ```bash
  python -m cli simulate out/2308a6946c86/manhattan_like.xosc
  ```

- `2be9f523d1f7` manhattan_like, explicit routes, 0.02 s timestep, no recording

  ```bash
  python -m cli simulate out/10c8201dcd3e/manhattan_like_routes.xosc --timestep 0.02 --no-record --no-csv
  ```

- `b5abc4460de9` grid_organic with buildings

  ```bash
  python -m cli simulate out/a4c12a47636d/organic_buildings.xosc
  ```

- `cb0ecaa10b0b` manhattan_like with buildings

  ```bash
  python -m cli simulate out/c3abc191f8ad/manhattan_buildings.xosc
  ```

- `07dc3e231fa7` coastal city on ground + buildings

  ```bash
  python -m cli simulate out/784aea2c024c/coast_city.xosc
  ```

- `53f6182f8e2b` generated-maps city on ground + buildings

  ```bash
  python -m cli simulate out/cf91d56ddf68/city_scene.xosc
  ```

- `b3782f374977` whole chain in one pipeline: maps, city, ground, buildings, esmini run

  ```bash
  python -m cli pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings --simulate --sim-vehicles 60 --sim-duration 20 -o city_full
  python -m cli view out/b3782f374977/city_full.xodr --scenegraph out/b3782f374977/city_full_scene.osgt   # look at it
  ```

- `5ef634b930b3` whole chain in one pipeline: manhattan_like with buildings, esmini run

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 --buildings --simulate --sim-vehicles 100 -o manhattan_full
  python -m cli view out/5ef634b930b3/manhattan_full.xodr --scenegraph out/5ef634b930b3/manhattan_full_buildings.osgt   # look at it
  ```

## 7. esmini GUI

## 8. Road geometry (stage A): fitted curves, metrics, align, curved streets

- `259d8d1f271c` metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)

  ```bash
  python -m cli metrics out/f6913f35fdf4/coast_city.xodr --json coast_city_metrics.json
  ```

- `6042ecc9fd09` drive-check: coastal city, every junction movement and every lane end to end

  ```bash
  python -m cli drive-check out/f6913f35fdf4/coast_city.xodr
  ```

- `462244afda2e` build-xodr: coastal city with fitted curves, banking and junction planes

  ```bash
  python -m cli build-xodr out/1423b47a4348/coast_city.json coast_city.xodr
  python -m cli view out/462244afda2e/coast_city.xodr   # look at it
  ```

- `b1a6e1fd7411` metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)

  ```bash
  python -m cli metrics out/462244afda2e/coast_city.xodr --json coast_city_metrics.json
  ```

- `3f63f67639a5` drive-check: coastal city, every junction movement and every lane end to end

  ```bash
  python -m cli drive-check out/462244afda2e/coast_city.xodr
  ```

- `f0c5e2794689` align: coastal city bends capped where their curves would leave legal ground

  ```bash
  python -m cli align out/1423b47a4348/coast_city.json --map-file terrain=out/19dbda6ba2a3/coast_terrain.npz --map-file elevation=out/df2efcf1515d/coast_elevation.npz -o coast_city.aligned.json
  ```

- `059c0c2a06b5` metrics: build-failure sweep over 20 lsystem cities on generated maps

  ```bash
  python -m cli metrics --sweep 20 --json sweep20.json
  ```

- `9623941ffedb` pipeline: lsystem organic_curved (drifting, bowed streets), drive-check

  ```bash
  python -m cli pipeline lsystem --preset organic_curved --seed 2 --allow-traps --drive-check -o organic_curved
  python -m cli view out/9623941ffedb/organic_curved.xodr   # look at it
  ```

- `50712981312c` pipeline: lsystem hill_town on maps (contour streets), ground, buildings, drive-check

  ```bash
  python -m cli pipeline lsystem --preset hill_town --seed 1 --maps --allow-traps --ground --buildings --drive-check -o hill_town
  python -m cli view out/50712981312c/hill_town.xodr --scenegraph out/50712981312c/hill_town_scene.osgt   # look at it
  ```

## 9. Long-tail diversity (stage C): pattern fields, style maps, cul-de-sacs, junction variety, roundabouts, overpasses, tunnels, interchanges, network metrics

- `acdf5813b00c` metrics --graph: network metrics of manhattan_like, the coastal city and the stage C presets

  ```bash
  python -m cli metrics --graph out/3a4b5ef07762/manhattan_like.json --graph out/917a6d6b33dc/coast_city.json --graph out/5ff781b88a35/paris.json --graph out/9adc5a4a4810/organic_town.json --graph out/25102d21d7bf/skewed_grid.json --graph out/6afb7e051c73/suburb.json --graph out/b0c06fe41f40/mixed_city.json --json network_metrics.json
  ```

- `5ff781b88a35` pipeline: lsystem paris (a star of avenues and rings, a grid outside), drive-check

  ```bash
  python -m cli pipeline lsystem --preset paris --allow-traps --drive-check -o paris
  python -m cli view out/5ff781b88a35/paris.xodr   # look at it
  ```

- `9adc5a4a4810` pipeline: lsystem organic_town (grid-to-organic knob 0.8)

  ```bash
  python -m cli pipeline lsystem --preset organic_town --seed 1 --allow-traps -o organic_town
  python -m cli view out/9adc5a4a4810/organic_town.xodr   # look at it
  ```

- `49fb2a4f902c` pipeline: lsystem organic_town with the grid-to-organic knob at 0.3

  ```bash
  python -m cli pipeline lsystem --preset organic_town --seed 1 --set organic=0.3 --allow-traps -o organic_town_03
  python -m cli view out/49fb2a4f902c/organic_town_03.xodr   # look at it
  ```

- `25102d21d7bf` pipeline: lsystem skewed_grid (streets at 60-90 deg to the avenues)

  ```bash
  python -m cli pipeline lsystem --preset skewed_grid --allow-traps -o skewed_grid
  python -m cli view out/25102d21d7bf/skewed_grid.xodr   # look at it
  ```

- `6afb7e051c73` pipeline: lsystem suburb (cul-de-sac bulbs at dead ends), drive-check

  ```bash
  python -m cli pipeline lsystem --preset suburb --seed 1 --allow-traps --drive-check -o suburb
  python -m cli view out/6afb7e051c73/suburb.xodr   # look at it
  ```

- `92933db3e9d3` pipeline: lsystem suburb with connectors (detour ratio 1.5)

  ```bash
  python -m cli pipeline lsystem --preset suburb --seed 1 --set detour_ratio=1.5 --allow-traps -o suburb_connected
  python -m cli view out/92933db3e9d3/suburb_connected.xodr   # look at it
  ```

- `b0c06fe41f40` pipeline: lsystem mixed_city on generated maps (style map districts), drive-check

  ```bash
  python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 --allow-traps --drive-check -o mixed_city
  python -m cli view out/b0c06fe41f40/mixed_city.xodr   # look at it
  ```

- `97575958aa0d` map style zones, preset old_town, on the mixed city's maps

  ```bash
  python -m cli map style zones --preset old_town --seed 1 --on terrain=out/b0c06fe41f40/mixed_city_terrain.npz --on elevation=out/b0c06fe41f40/mixed_city_elevation.npz --on population=out/b0c06fe41f40/mixed_city_population.npz -o old_town_style
  ```

- `9f985375afc1` pipeline: junction preset k (a through road and two diagonals), drive-check

  ```bash
  python -m cli pipeline junction --preset k --seed 3 --allow-traps --drive-check -o junction_k
  python -m cli view out/9f985375afc1/junction_k.xodr   # look at it
  ```

- `679e822f5060` pipeline: junction preset random, seed 17 (JunctionArt-style long tail)

  ```bash
  python -m cli pipeline junction --preset random --seed 17 --allow-traps -o junction_random
  python -m cli view out/679e822f5060/junction_random.xodr   # look at it
  ```

- `8dce8d1f6536` pipeline: junction preset roundabout, drive-check

  ```bash
  python -m cli pipeline junction --preset roundabout --seed 2 --allow-traps --drive-check -o roundabout
  python -m cli view out/8dce8d1f6536/roundabout.xodr   # look at it
  ```

- `f59489f24621` pipeline: junction preset diamond (highway interchange)

  ```bash
  python -m cli pipeline junction --preset diamond --allow-traps -o diamond
  python -m cli view out/f59489f24621/diamond.xodr   # look at it
  ```

- `5bc976595436` pipeline: junction preset cloverleaf (highway x highway interchange)

  ```bash
  python -m cli pipeline junction --preset cloverleaf --allow-traps -o cloverleaf
  python -m cli view out/5bc976595436/cloverleaf.xodr   # look at it
  ```

- `52322eca64c7` pipeline: lsystem city on maps with freeways (overpasses), drive-check

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 1 --set highway_crossings=separated --allow-traps --drive-check -o city_freeways
  python -m cli view out/52322eca64c7/city_freeways.xodr   # look at it
  ```

- `83e16ac16e35` pipeline: lsystem highway_corridor on maps (freeways, a diamond, lane adds and drops)

  ```bash
  python -m cli pipeline lsystem --preset highway_corridor --maps --seed 1 --allow-traps --ground -o highway_corridor
  python -m cli view out/83e16ac16e35/highway_corridor.xodr --scenegraph out/83e16ac16e35/highway_corridor_scene.osgt   # look at it
  ```

- `c1caa4c9a929` pipeline: lsystem city on hilly maps with a highway tunnel, ground

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 3 --set tunnels=true --set highway_max_grade=0.05 --map-set elevation.max_elevation=150 --map-set elevation.ridge=0.5 --allow-traps --ground -o tunnel_city
  python -m cli view out/c1caa4c9a929/tunnel_city.xodr --scenegraph out/c1caa4c9a929/tunnel_city_scene.osgt   # look at it
  ```

- `23d68c6e276b` metrics --graph: network metrics of manhattan_like, the coastal city and the stage C presets

  ```bash
  python -m cli metrics --graph out/3a4b5ef07762/manhattan_like.json --graph out/917a6d6b33dc/coast_city.json --graph out/5ff781b88a35/paris.json --graph out/9adc5a4a4810/organic_town.json --graph out/25102d21d7bf/skewed_grid.json --graph out/6afb7e051c73/suburb.json --graph out/b0c06fe41f40/mixed_city.json --graph out/83e16ac16e35/highway_corridor.json --graph out/679e822f5060/junction_random.json --json network_metrics.json
  ```
