#!/usr/bin/env bash
# Fills runs/history.json with example runs, phase by phase: input maps ->
# road network -> OpenDRIVE -> 3D scene -> scenario -> headless simulation ->
# road geometry (stage A) -> esmini GUI. Run from the repository root on the host:
#
#   bash runs/examples.sh            # everything (the GUI phase opens windows)
#   NO_GUI=1 bash runs/examples.sh   # skip phase 7
#
# Every successful command is recorded under its hash (see runs/README.md);
# replay one with `python -m cli HASH`. Commands that read files read the
# outputs of earlier ones, so replay hashes in this order. Outputs go to
# out/examples/ (git-ignored).
set -u
cd "$(dirname "$0")/.."

cli() { docker compose run --rm -T dev python -m cli "$@" 2>&1 | grep -E "error|ERROR|run [0-9a-f]{12}" ; }
gui() { docker compose run --rm -T gui python -m cli "$@" 2>&1 | grep -E "error|ERROR|run [0-9a-f]{12}" ; }

O=out/examples
M=$O/maps
N=$O/net
P=$O/pl

docker compose run --rm -T dev python runs/inputs/make_inputs.py

echo "== 1 maps"
GRID=(--origin -200 -200 --size 3400 2800)  # the coastal stack covers the city area plus 200 m
cli --note "1 maps | terrain noise, preset coastal (sea on one side)" \
    map terrain noise --preset coastal --seed 3 "${GRID[@]}" -o $M/coast_terrain
cli --note "1 maps | elevation fbm, preset rolling, on the coastal terrain (water lies flat in the valleys)" \
    map elevation fbm --preset rolling --seed 3 "${GRID[@]}" --on terrain=$M/coast_terrain.npz -o $M/coast_elevation
cli --note "1 maps | population centers, preset polycentric, on the coastal terrain + elevation" \
    map population centers --preset polycentric --seed 3 "${GRID[@]}" \
    --on terrain=$M/coast_terrain.npz --on elevation=$M/coast_elevation.npz -o $M/coast_population
cli --note "1 maps | terrain noise, preset river_city (one wide river)" \
    map terrain noise --preset river_city --seed 5 -o $M/river_terrain
cli --note "1 maps | elevation fbm, preset rolling, on the river terrain" \
    map elevation fbm --preset rolling --seed 5 --on terrain=$M/river_terrain.npz -o $M/river_elevation
cli --note "1 maps | population centers, preset monocentric, on the river terrain + elevation" \
    map population centers --preset monocentric --seed 5 \
    --on terrain=$M/river_terrain.npz --on elevation=$M/river_elevation.npz -o $M/river_population
cli --note "1 maps | terrain noise, preset lakes" map terrain noise --preset lakes --seed 2 -o $M/lakes_terrain
cli --note "1 maps | terrain noise, preset inland (no water)" map terrain noise --preset inland --seed 2 -o $M/inland_terrain
cli --note "1 maps | elevation fbm, preset mountainous, 4 x 3 km at 20 m cells" \
    map elevation fbm --preset mountainous --seed 4 --size 4000 3000 --cell-size 20 -o $M/mountains_elevation
cli --note "1 maps | elevation fbm, preset flat" map elevation fbm --preset flat -o $M/flat_elevation
cli --note "1 maps | population centers, preset sprawl" map population centers --preset sprawl --seed 6 -o $M/sprawl_population
cli --note "1 maps | terrain noise, bare parameters: two rivers, half a coast, more parks" \
    map terrain noise --seed 8 --set water_fraction=0.2 --set coast_strength=0.5 --set coast_angle_deg=270 \
    --set rivers=2 --set river_width=60 --set vegetation_fraction=0.25 -o $M/bare_terrain
cli --note "1 maps | elevation fbm, bare parameters: ridged crests up to 120 m, no erosion" \
    map elevation fbm --seed 8 --set max_elevation=120 --set ridge=0.6 --set exponent=1.4 \
    --set erosion_iterations=0 -o $M/bare_elevation
cli --note "1 maps | population centers, bare parameters: four gaussian centres" \
    map population centers --seed 8 --set n_centers=4 --set profile=gaussian --set peak_density=10000 \
    --set decay_length=500 -o $M/bare_population
cli --note "1 maps | terrain noise from a parameter file (coastal preset, sea in the west, a river)" \
    map terrain noise --params runs/inputs/terrain_params.yaml --seed 9 -o $M/params_terrain
cli --note "1 maps | terrain image: lake, river and park drawn in runs/inputs/terrain.png" \
    map terrain image --set path=runs/inputs/terrain.png -o $M/image_terrain
cli --note "1 maps | elevation image: runs/inputs/heightmap.png scaled to 0..60 m, on the image terrain" \
    map elevation image --set path=runs/inputs/heightmap.png --set max_elevation=60 \
    --on terrain=$M/image_terrain.npz -o $M/image_elevation
cli --note "1 maps | population centers, two centres, on the image terrain + elevation" \
    map population centers --seed 10 --set n_centers=2 \
    --on terrain=$M/image_terrain.npz --on elevation=$M/image_elevation.npz -o $M/image_population

echo "== 2 road network"
COAST=(--map-file terrain=$M/coast_terrain.npz --map-file elevation=$M/coast_elevation.npz
       --map-file population=$M/coast_population.npz)
RIVER=(--map-file terrain=$M/river_terrain.npz --map-file elevation=$M/river_elevation.npz
       --map-file population=$M/river_population.npz)
cli --note "2 network | manhattan, preset manhattan_like (one-way avenues, rotated 29 deg)" \
    generate manhattan --preset manhattan_like --seed 1 -o $N/manhattan_like.json
cli --note "2 network | manhattan, bare parameters: 6 x 12 grid, ring road, jitter, dropped streets" \
    generate manhattan --avenues 6 --streets 12 --avenue-spacing 220 --street-spacing 90 --rotation 15 \
    --jitter 3 --edge-drop 0.05 --boundary loop --seed 2 -o $N/manhattan_ring.json
cli --note "2 network | manhattan with --config runs/inputs/wide_lanes.yaml (3.75 m lanes)" \
    --config runs/inputs/wide_lanes.yaml generate manhattan --avenues 5 --streets 8 -o $N/manhattan_wide.json
cli --note "2 network | lsystem, preset grid (a 10 x 10 grid grown from a corner)" \
    generate lsystem --preset grid -o $N/lsystem_grid.json
cli --note "2 network | lsystem, preset grid_organic (wobbly, some branches missing)" \
    generate lsystem --preset grid_organic --seed 2 -o $N/organic.json
cli --note "2 network | lsystem, preset city without maps (flat grid streets only)" \
    generate lsystem --preset city --seed 1 -o $N/city_nomaps.json
cli --note "2 network | lsystem, bare parameters: rotated 20 deg, jittered headings and lengths" \
    generate lsystem --seed 3 --set "extent=[1500,900]" --set "block_size=[180,70]" --set angle_jitter_deg=4 \
    --set length_jitter=0.1 --set rotation_deg=20 -o $N/lsystem_bare.json
cli --note "2 network | lsystem city grown on the coastal maps: highways towards the centres, heights (draped)" \
    generate lsystem --preset city --seed 4 "${COAST[@]}" -o $N/coast_city.json
cli --note "2 network | lsystem city on the coastal maps, --flat (same roads, no heights)" \
    generate lsystem --preset city --seed 4 "${COAST[@]}" --flat -o $N/coast_city_flat.json
cli --note "2 network | lsystem city grown on the river maps" \
    generate lsystem --preset city --seed 5 "${RIVER[@]}" -o $N/river_city.json
cli --note "2 network | lsystem city on the image maps, no highways, max grade 8 %" \
    generate lsystem --preset city --seed 10 --map-file terrain=$M/image_terrain.npz \
    --map-file elevation=$M/image_elevation.npz --map-file population=$M/image_population.npz \
    --set highways=false --set max_grade=0.08 -o $N/image_city.json
cli --note "2 network | drape the flat coastal city with the coastal elevation + terrain" \
    drape $N/coast_city_flat.json --map-file elevation=$M/coast_elevation.npz \
    --map-file terrain=$M/coast_terrain.npz -o $N/coast_city_draped.json
cli --note "2 network | plot the coastal city graph" plot-graph $N/coast_city.json $N/coast_city.png
cli --note "2 network | plot the manhattan ring with node ids" \
    plot-graph $N/manhattan_ring.json $N/manhattan_ring.png --node-ids
cli --note "2 network | the coastal population map with the coastal city on top" \
    plot-map $M/coast_population.npz $N/coast_city_on_population.png --graph $N/coast_city.json

echo "== 3 OpenDRIVE"
cli --note "3 xodr | build-xodr: manhattan_like" build-xodr $N/manhattan_like.json $N/manhattan_like.xodr
cli --note "3 xodr | build-xodr: manhattan ring" build-xodr $N/manhattan_ring.json $N/manhattan_ring.xodr
cli --note "3 xodr | build-xodr: lsystem grid_organic, junction margin 3 m" \
    build-xodr $N/organic.json $N/organic.xodr --junction-margin 3
cli --note "3 xodr | build-xodr: coastal city with elevation profiles" \
    build-xodr $N/coast_city.json $N/coast_city.xodr
cli --note "3 xodr | drive-check: every junction movement of the coastal city in esmini" \
    drive-check $N/coast_city.xodr
cli --note "3 xodr | pipeline: manhattan_like -> json, png, xodr, all validators" \
    pipeline manhattan --preset manhattan_like --seed 1 -o $P/manhattan_like
cli --note "3 xodr | pipeline: lsystem grid + drive-check" \
    pipeline lsystem --preset grid --drive-check -o $P/grid
cli --note "3 xodr | pipeline: lsystem city on generated maps (--maps), seed 7" \
    pipeline lsystem --preset city --allow-traps --maps --seed 7 -o $P/city_maps
cli --note "3 xodr | pipeline: lsystem city, maps chosen per kind with --map-set overrides" \
    pipeline lsystem --preset city --allow-traps --terrain noise --elevation fbm --population centers \
    --map-set terrain.rivers=2 --map-set elevation.max_elevation=60 --map-set population.n_centers=1 \
    --seed 4 -o $P/city_rivers
cli --note "3 xodr | pipeline: lsystem city on the river map files + drive-check" \
    pipeline lsystem --preset city --allow-traps --seed 5 "${RIVER[@]}" --drive-check -o $P/river_city

echo "== 4 3D scene"
cli --note "4 scene | pipeline: lsystem grid_organic + buildings (.osgt)" \
    pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o $P/organic_buildings
cli --note "4 scene | pipeline: manhattan_like + buildings" \
    pipeline manhattan --preset manhattan_like --seed 1 --buildings -o $P/manhattan_buildings
cli --note "4 scene | pipeline: lsystem city on generated maps + ground mesh + buildings" \
    pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings -o $P/city_scene
cli --note "4 scene | ground: coastal terrain levelled under the coastal city, with its buildings" \
    ground $O/scene/coast_city_scene.osgt --map-file elevation=$M/coast_elevation.npz \
    --map-file terrain=$M/coast_terrain.npz --graph $N/coast_city.json --buildings --seed 3
cli --note "4 scene | ground: bare mountainous terrain, no roads" \
    ground $O/scene/mountains.osgt --map-file elevation=$M/mountains_elevation.npz

echo "== 5 scenario"
cli --note "5 scenario | manhattan_like, 100 vehicles, random junction choices" \
    scenario $N/manhattan_like.xodr -o $N/manhattan_like.xosc --vehicles 100 --duration 30
cli --note "5 scenario | manhattan_like pipeline, 80 vehicles on explicit shortest-path routes, 8-14 m/s" \
    scenario $P/manhattan_like.xodr -o $P/manhattan_like_routes.xosc --vehicles 80 --mode explicit_routes \
    --speed-min 8 --speed-max 14 --min-gap 15 --duration 30 --seed 2
cli --note "5 scenario | grid_organic with its buildings as scene graph" \
    scenario $P/organic_buildings.xodr -o $P/organic_buildings.xosc --vehicles 60 --duration 30 \
    --scenegraph $P/organic_buildings_buildings.osgt
cli --note "5 scenario | manhattan_like with its buildings as scene graph" \
    scenario $P/manhattan_buildings.xodr -o $P/manhattan_buildings.xosc --vehicles 100 --duration 30 \
    --scenegraph $P/manhattan_buildings_buildings.osgt
cli --note "5 scenario | coastal city on its ground + buildings scene" \
    scenario $N/coast_city.xodr -o $N/coast_city.xosc --vehicles 80 --duration 30 \
    --scenegraph $O/scene/coast_city_scene.osgt
cli --note "5 scenario | generated-maps city on its ground + buildings scene" \
    scenario $P/city_scene.xodr -o $P/city_scene.xosc --vehicles 80 --duration 30 \
    --scenegraph $P/city_scene_scene.osgt

echo "== 6 simulation (headless)"
cli --note "6 simulate | manhattan_like, random traffic" simulate $N/manhattan_like.xosc
cli --note "6 simulate | manhattan_like, explicit routes, 0.02 s timestep, no recording" \
    simulate $P/manhattan_like_routes.xosc --timestep 0.02 --no-record --no-csv
cli --note "6 simulate | grid_organic with buildings" simulate $P/organic_buildings.xosc
cli --note "6 simulate | manhattan_like with buildings" simulate $P/manhattan_buildings.xosc
cli --note "6 simulate | coastal city on ground + buildings" simulate $N/coast_city.xosc
cli --note "6 simulate | generated-maps city on ground + buildings" simulate $P/city_scene.xosc
cli --note "6 simulate | whole chain in one pipeline: maps, city, ground, buildings, esmini run" \
    pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings --simulate \
    --sim-vehicles 60 --sim-duration 20 -o $P/city_full
cli --note "6 simulate | whole chain in one pipeline: manhattan_like with buildings, esmini run" \
    pipeline manhattan --preset manhattan_like --seed 1 --buildings --simulate --sim-vehicles 100 \
    -o $P/manhattan_full

echo "== 8 road geometry (stage A)"
G=$O/geometry
cli --note "8 geometry | build-xodr: coastal city with fitted curves, banking and junction planes" \
    build-xodr $N/coast_city.json $G/coast_city.xodr
cli --note "8 geometry | metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)" \
    metrics $G/coast_city.xodr --json $G/coast_city_metrics.json
cli --note "8 geometry | drive-check: coastal city, every junction movement and every lane end to end" \
    drive-check $G/coast_city.xodr
cli --note "8 geometry | align: coastal city bends capped where their curves would leave legal ground" \
    align $N/coast_city.json --map-file terrain=$M/coast_terrain.npz \
    --map-file elevation=$M/coast_elevation.npz -o $G/coast_city.aligned.json
cli --note "8 geometry | metrics: build-failure sweep over 20 lsystem cities on generated maps" \
    metrics --sweep 20 --json $G/sweep20.json
cli --note "8 geometry | pipeline: lsystem organic_curved (drifting, bowed streets), drive-check" \
    pipeline lsystem --preset organic_curved --seed 2 --allow-traps --drive-check -o $G/organic_curved
cli --note "8 geometry | pipeline: lsystem hill_town on maps (contour streets), ground, buildings, drive-check" \
    pipeline lsystem --preset hill_town --seed 1 --maps --allow-traps --ground --buildings --drive-check \
    -o $G/hill_town

[ -n "${NO_GUI:-}" ] && exit 0
echo "== 7 esmini GUI (views stay open until you close the window)"
gui --note "7 gui | odrviewer: manhattan_like with buildings, random cars" \
    view $P/manhattan_buildings.xodr --scenegraph $P/manhattan_buildings_buildings.osgt
gui --note "7 gui | odrviewer: grid_organic with buildings, 2 cars / 100 m" \
    view $P/organic_buildings.xodr --scenegraph $P/organic_buildings_buildings.osgt --density 2
gui --note "7 gui | odrviewer: coastal city with heights, on its ground + buildings" \
    view $N/coast_city.xodr --scenegraph $O/scene/coast_city_scene.osgt
gui --note "7 gui | odrviewer: the one-command city (maps, ground, buildings) from phase 6" \
    view $P/city_full.xodr --scenegraph $P/city_full_scene.osgt
gui --note "7 gui | esmini: manhattan_like with buildings, top camera" \
    simulate $P/manhattan_buildings.xosc --gui
gui --note "7 gui | esmini: coastal city on its ground + buildings" \
    simulate $N/coast_city.xosc --gui
