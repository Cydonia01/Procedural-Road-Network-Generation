#!/usr/bin/env bash
# Records example runs, phase by phase: input maps -> road network ->
# OpenDRIVE -> 3D scene -> scenario -> headless simulation -> road geometry
# (stage A) -> esmini GUI. Run from the repository root on the host:
#
#   bash runs/examples.sh            # everything (the GUI phase opens windows)
#   NO_GUI=1 bash runs/examples.sh   # skip phase 7
#
# Every successful command gets its own folder out/<hash>/ with its outputs
# and run.yaml (see runs/README.md); replay one with `python -m cli HASH`.
# -o only names the outputs. Commands that read files read them from the
# folders of earlier runs, kept in variables below.
set -u
cd "$(dirname "$0")/.."

# Runs one command, shows its errors and run line, and sets RUN to its folder.
_run() {
    local service=$1 log
    shift
    log=$(docker compose run --rm -T "$service" python -m cli "$@" 2>&1)
    echo "$log" | grep -E "error|ERROR|roadgen.cli: run [0-9a-f]{12}"
    RUN=out/$(echo "$log" | grep -oE "roadgen.cli: run [0-9a-f]{12}" | tail -1 | grep -oE "[0-9a-f]{12}$")
}
cli() { _run dev "$@"; }
gui() { _run gui "$@"; }

docker compose run --rm -T dev python runs/inputs/make_inputs.py

echo "== 1 maps"
GRID=(--origin -200 -200 --size 3400 2800)  # the coastal stack covers the city area plus 200 m
cli --note "1 maps | terrain noise, preset coastal (sea on one side)" \
    map terrain noise --preset coastal --seed 3 "${GRID[@]}" -o coast_terrain
CT=$RUN/coast_terrain.npz
cli --note "1 maps | elevation fbm, preset rolling, on the coastal terrain (water lies flat in the valleys)" \
    map elevation fbm --preset rolling --seed 3 "${GRID[@]}" --on terrain=$CT -o coast_elevation
CE=$RUN/coast_elevation.npz
cli --note "1 maps | population centers, preset polycentric, on the coastal terrain + elevation" \
    map population centers --preset polycentric --seed 3 "${GRID[@]}" \
    --on terrain=$CT --on elevation=$CE -o coast_population
CP=$RUN/coast_population.npz
cli --note "1 maps | terrain noise, preset river_city (one wide river)" \
    map terrain noise --preset river_city --seed 5 -o river_terrain
RT=$RUN/river_terrain.npz
cli --note "1 maps | elevation fbm, preset rolling, on the river terrain" \
    map elevation fbm --preset rolling --seed 5 --on terrain=$RT -o river_elevation
RE=$RUN/river_elevation.npz
cli --note "1 maps | population centers, preset monocentric, on the river terrain + elevation" \
    map population centers --preset monocentric --seed 5 --on terrain=$RT --on elevation=$RE -o river_population
RP=$RUN/river_population.npz
cli --note "1 maps | terrain noise, preset lakes" map terrain noise --preset lakes --seed 2 -o lakes_terrain
cli --note "1 maps | terrain noise, preset inland (no water)" map terrain noise --preset inland --seed 2 -o inland_terrain
cli --note "1 maps | elevation fbm, preset mountainous, 4 x 3 km at 20 m cells" \
    map elevation fbm --preset mountainous --seed 4 --size 4000 3000 --cell-size 20 -o mountains_elevation
ME=$RUN/mountains_elevation.npz
cli --note "1 maps | elevation fbm, preset flat" map elevation fbm --preset flat -o flat_elevation
cli --note "1 maps | population centers, preset sprawl" map population centers --preset sprawl --seed 6 -o sprawl_population
cli --note "1 maps | terrain noise, bare parameters: two rivers, half a coast, more parks" \
    map terrain noise --seed 8 --set water_fraction=0.2 --set coast_strength=0.5 --set coast_angle_deg=270 \
    --set rivers=2 --set river_width=60 --set vegetation_fraction=0.25 -o bare_terrain
cli --note "1 maps | elevation fbm, bare parameters: ridged crests up to 120 m, no erosion" \
    map elevation fbm --seed 8 --set max_elevation=120 --set ridge=0.6 --set exponent=1.4 \
    --set erosion_iterations=0 -o bare_elevation
cli --note "1 maps | population centers, bare parameters: four gaussian centres" \
    map population centers --seed 8 --set n_centers=4 --set profile=gaussian --set peak_density=10000 \
    --set decay_length=500 -o bare_population
cli --note "1 maps | terrain noise from a parameter file (coastal preset, sea in the west, a river)" \
    map terrain noise --params runs/inputs/terrain_params.yaml --seed 9 -o params_terrain
cli --note "1 maps | terrain image: lake, river and park drawn in runs/inputs/terrain.png" \
    map terrain image --set path=runs/inputs/terrain.png -o image_terrain
IT=$RUN/image_terrain.npz
cli --note "1 maps | elevation image: runs/inputs/heightmap.png scaled to 0..60 m, on the image terrain" \
    map elevation image --set path=runs/inputs/heightmap.png --set max_elevation=60 \
    --on terrain=$IT -o image_elevation
IE=$RUN/image_elevation.npz
cli --note "1 maps | population centers, two centres, on the image terrain + elevation" \
    map population centers --seed 10 --set n_centers=2 --on terrain=$IT --on elevation=$IE -o image_population
IP=$RUN/image_population.npz

echo "== 2 road network"
COAST=(--map-file terrain=$CT --map-file elevation=$CE --map-file population=$CP)
RIVER=(--map-file terrain=$RT --map-file elevation=$RE --map-file population=$RP)
cli --note "2 network | lsystem, preset manhattan_like (alternating one-way avenues and streets, ring road, rotated 29 deg)" \
    generate lsystem --preset manhattan_like --seed 1 -o manhattan_like.json
ML=$RUN/manhattan_like.json
cli --note "2 network | lsystem grid, bare parameters: 6 x 12 grid, ring road, heading noise, dropped streets" \
    generate lsystem --preset grid --set "extent=[1100,990]" --set "block_size=[220,90]" --set rotation_deg=15 \
    --set angle_jitter_deg=1.5 --set edge_drop_prob=0.05 --set boundary=ring --seed 2 -o grid_ring.json
GR=$RUN/grid_ring.json
cli --note "2 network | lsystem grid with --config runs/inputs/wide_lanes.yaml (3.75 m lanes)" \
    --config runs/inputs/wide_lanes.yaml generate lsystem --preset grid --set "extent=[1000,560]" -o grid_wide.json
cli --note "2 network | lsystem, preset grid (a 10 x 10 grid grown from a corner)" \
    generate lsystem --preset grid -o lsystem_grid.json
cli --note "2 network | lsystem, preset grid_organic (wobbly, some branches missing)" \
    generate lsystem --preset grid_organic --seed 2 -o organic.json
OG=$RUN/organic.json
cli --note "2 network | lsystem, preset city without maps (flat grid streets only)" \
    generate lsystem --preset city --seed 1 -o city_nomaps.json
cli --note "2 network | lsystem, bare parameters: rotated 20 deg, jittered headings and lengths" \
    generate lsystem --seed 3 --set "extent=[1500,900]" --set "block_size=[180,70]" --set angle_jitter_deg=4 \
    --set length_jitter=0.1 --set rotation_deg=20 -o lsystem_bare.json
cli --note "2 network | lsystem city grown on the coastal maps: highways towards the centres, heights (draped)" \
    generate lsystem --preset city --seed 4 "${COAST[@]}" -o coast_city.json
CC=$RUN/coast_city.json
cli --note "2 network | lsystem city on the coastal maps, --flat (same roads, no heights)" \
    generate lsystem --preset city --seed 4 "${COAST[@]}" --flat -o coast_city_flat.json
CF=$RUN/coast_city_flat.json
cli --note "2 network | lsystem city grown on the river maps" \
    generate lsystem --preset city --seed 5 "${RIVER[@]}" -o river_city.json
cli --note "2 network | lsystem city on the image maps, no highways, max grade 8 %" \
    generate lsystem --preset city --seed 10 --map-file terrain=$IT --map-file elevation=$IE \
    --map-file population=$IP --set highways=false --set max_grade=0.08 -o image_city.json
cli --note "2 network | drape the flat coastal city with the coastal elevation + terrain" \
    drape $CF --map-file elevation=$CE --map-file terrain=$CT -o coast_city_draped.json
cli --note "2 network | plot the coastal city graph" plot-graph $CC coast_city.png
cli --note "2 network | plot the ring-road grid with node ids" plot-graph $GR grid_ring.png --node-ids
cli --note "2 network | the coastal population map with the coastal city on top" \
    plot-map $CP coast_city_on_population.png --graph $CC

echo "== 3 OpenDRIVE"
cli --note "3 xodr | build-xodr: manhattan_like" build-xodr $ML manhattan_like.xodr
MLX=$RUN/manhattan_like.xodr
cli --note "3 xodr | build-xodr: ring-road grid" build-xodr $GR grid_ring.xodr
cli --note "3 xodr | build-xodr: lsystem grid_organic, junction margin 3 m" \
    build-xodr $OG organic.xodr --junction-margin 3
cli --note "3 xodr | build-xodr: coastal city with elevation profiles" build-xodr $CC coast_city.xodr
CCX=$RUN/coast_city.xodr
cli --note "3 xodr | drive-check: every junction movement of the coastal city in esmini" drive-check $CCX
cli --note "3 xodr | pipeline: manhattan_like -> json, png, xodr, all validators" \
    pipeline lsystem --preset manhattan_like --seed 1 -o manhattan_like
PML=$RUN/manhattan_like
cli --note "3 xodr | pipeline: lsystem grid + drive-check" pipeline lsystem --preset grid --drive-check -o grid
cli --note "3 xodr | pipeline: lsystem city on generated maps (--maps), seed 7" \
    pipeline lsystem --preset city --allow-traps --maps --seed 7 -o city_maps
cli --note "3 xodr | pipeline: lsystem city, maps chosen per kind with --map-set overrides" \
    pipeline lsystem --preset city --allow-traps --terrain noise --elevation fbm --population centers \
    --map-set terrain.rivers=2 --map-set elevation.max_elevation=60 --map-set population.n_centers=1 \
    --seed 4 -o city_rivers
cli --note "3 xodr | pipeline: lsystem city on the river map files + drive-check" \
    pipeline lsystem --preset city --allow-traps --seed 5 "${RIVER[@]}" --drive-check -o river_city

echo "== 4 3D scene"
cli --note "4 scene | pipeline: lsystem grid_organic + buildings (.osgt)" \
    pipeline lsystem --preset grid_organic --seed 2 --allow-traps --buildings -o organic_buildings
POB=$RUN/organic_buildings
cli --note "4 scene | pipeline: manhattan_like + buildings" \
    pipeline lsystem --preset manhattan_like --seed 1 --buildings -o manhattan_buildings
PMB=$RUN/manhattan_buildings
cli --note "4 scene | pipeline: lsystem city on generated maps + ground mesh + buildings" \
    pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings -o city_scene
PCS=$RUN/city_scene
cli --note "4 scene | ground: coastal terrain levelled under the coastal city, with its buildings" \
    ground coast_city_scene.osgt --map-file elevation=$CE --map-file terrain=$CT --graph $CC --buildings --seed 3
CCS=$RUN/coast_city_scene.osgt
cli --note "4 scene | ground: bare mountainous terrain, no roads" ground mountains.osgt --map-file elevation=$ME

echo "== 5 scenario"
cli --note "5 scenario | manhattan_like, 100 vehicles, random junction choices" \
    scenario $MLX -o manhattan_like.xosc --vehicles 100 --duration 30
S1=$RUN/manhattan_like.xosc
cli --note "5 scenario | manhattan_like pipeline, 80 vehicles on explicit shortest-path routes, 8-14 m/s" \
    scenario $PML.xodr -o manhattan_like_routes.xosc --vehicles 80 --mode explicit_routes \
    --speed-min 8 --speed-max 14 --min-gap 15 --duration 30 --seed 2
S2=$RUN/manhattan_like_routes.xosc
cli --note "5 scenario | grid_organic with its buildings as scene graph" \
    scenario $POB.xodr -o organic_buildings.xosc --vehicles 60 --duration 30 --scenegraph ${POB}_buildings.osgt
S3=$RUN/organic_buildings.xosc
cli --note "5 scenario | manhattan_like with its buildings as scene graph" \
    scenario $PMB.xodr -o manhattan_buildings.xosc --vehicles 100 --duration 30 --scenegraph ${PMB}_buildings.osgt
S4=$RUN/manhattan_buildings.xosc
cli --note "5 scenario | coastal city on its ground + buildings scene" \
    scenario $CCX -o coast_city.xosc --vehicles 80 --duration 30 --scenegraph $CCS
S5=$RUN/coast_city.xosc
cli --note "5 scenario | generated-maps city on its ground + buildings scene" \
    scenario $PCS.xodr -o city_scene.xosc --vehicles 80 --duration 30 --scenegraph ${PCS}_scene.osgt
S6=$RUN/city_scene.xosc

echo "== 6 simulation (headless)"
cli --note "6 simulate | manhattan_like, random traffic" simulate $S1
cli --note "6 simulate | manhattan_like, explicit routes, 0.02 s timestep, no recording" \
    simulate $S2 --timestep 0.02 --no-record --no-csv
cli --note "6 simulate | grid_organic with buildings" simulate $S3
cli --note "6 simulate | manhattan_like with buildings" simulate $S4
cli --note "6 simulate | coastal city on ground + buildings" simulate $S5
cli --note "6 simulate | generated-maps city on ground + buildings" simulate $S6
cli --note "6 simulate | whole chain in one pipeline: maps, city, ground, buildings, esmini run" \
    pipeline lsystem --preset city --allow-traps --maps --seed 7 --ground --buildings --simulate \
    --sim-vehicles 60 --sim-duration 20 -o city_full
PCF=$RUN/city_full
cli --note "6 simulate | whole chain in one pipeline: manhattan_like with buildings, esmini run" \
    pipeline lsystem --preset manhattan_like --seed 1 --buildings --simulate --sim-vehicles 100 \
    -o manhattan_full

echo "== 8 road geometry (stage A)"
cli --note "8 geometry | build-xodr: coastal city with fitted curves, banking and junction planes" \
    build-xodr $CC coast_city.xodr
GX=$RUN/coast_city.xodr
cli --note "8 geometry | metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)" \
    metrics $GX --json coast_city_metrics.json
cli --note "8 geometry | drive-check: coastal city, every junction movement and every lane end to end" \
    drive-check $GX
cli --note "8 geometry | align: coastal city bends capped where their curves would leave legal ground" \
    align $CC --map-file terrain=$CT --map-file elevation=$CE -o coast_city.aligned.json
cli --note "8 geometry | metrics: build-failure sweep over 20 lsystem cities on generated maps" \
    metrics --sweep 20 --json sweep20.json
cli --note "8 geometry | pipeline: lsystem organic_curved (drifting, bowed streets), drive-check" \
    pipeline lsystem --preset organic_curved --seed 2 --allow-traps --drive-check -o organic_curved
cli --note "8 geometry | pipeline: lsystem hill_town on maps (contour streets), ground, buildings, drive-check" \
    pipeline lsystem --preset hill_town --seed 1 --maps --allow-traps --ground --buildings --drive-check \
    -o hill_town

echo "== 9 long-tail diversity (stage C)"
cli --note "9 diversity | pipeline: lsystem paris (a star of avenues and rings, a grid outside), drive-check" \
    pipeline lsystem --preset paris --allow-traps --drive-check -o paris
PA=$RUN/paris
cli --note "9 diversity | pipeline: lsystem organic_town (grid-to-organic knob 0.8)" \
    pipeline lsystem --preset organic_town --seed 1 --allow-traps -o organic_town
OT=$RUN/organic_town
cli --note "9 diversity | pipeline: lsystem organic_town with the grid-to-organic knob at 0.3" \
    pipeline lsystem --preset organic_town --seed 1 --set organic=0.3 --allow-traps -o organic_town_03
cli --note "9 diversity | pipeline: lsystem skewed_grid (streets at 60-90 deg to the avenues)" \
    pipeline lsystem --preset skewed_grid --allow-traps -o skewed_grid
SG=$RUN/skewed_grid
cli --note "9 diversity | pipeline: lsystem suburb (cul-de-sac bulbs at dead ends), drive-check" \
    pipeline lsystem --preset suburb --seed 1 --allow-traps --drive-check -o suburb
SB=$RUN/suburb
cli --note "9 diversity | pipeline: lsystem suburb with connectors (detour ratio 1.5)" \
    pipeline lsystem --preset suburb --seed 1 --set detour_ratio=1.5 --allow-traps -o suburb_connected
cli --note "9 diversity | pipeline: lsystem mixed_city on generated maps (style map districts), drive-check" \
    pipeline lsystem --preset mixed_city --maps --seed 1 --allow-traps --drive-check -o mixed_city
MC=$RUN/mixed_city
cli --note "9 diversity | map style zones, preset old_town, on the mixed city's maps" \
    map style zones --preset old_town --seed 1 --on terrain=${MC}_terrain.npz \
    --on elevation=${MC}_elevation.npz --on population=${MC}_population.npz -o old_town_style
cli --note "9 diversity | metrics --graph: network metrics of manhattan_like, the coastal city and the stage C presets" \
    metrics --graph $ML --graph $CC --graph $PA.json --graph $OT.json --graph $SG.json --graph $SB.json \
    --graph $MC.json --json network_metrics.json

[ -n "${NO_GUI:-}" ] && exit 0
echo "== 7 esmini GUI (views stay open until you close the window)"
gui --note "7 gui | odrviewer: manhattan_like with buildings, random cars" \
    view $PMB.xodr --scenegraph ${PMB}_buildings.osgt
gui --note "7 gui | odrviewer: grid_organic with buildings, 2 cars / 100 m" \
    view $POB.xodr --scenegraph ${POB}_buildings.osgt --density 2
gui --note "7 gui | odrviewer: coastal city with heights, on its ground + buildings" \
    view $CCX --scenegraph $CCS
gui --note "7 gui | odrviewer: the one-command city (maps, ground, buildings) from phase 6" \
    view $PCF.xodr --scenegraph ${PCF}_scene.osgt
gui --note "7 gui | odrviewer: paris (radial star and rings), 2 cars / 100 m" view $PA.xodr --density 2
gui --note "7 gui | odrviewer: suburb with cul-de-sac bulbs" view $SB.xodr
gui --note "7 gui | odrviewer: mixed_city districts on generated maps" view $MC.xodr
gui --note "7 gui | esmini: manhattan_like with buildings, top camera" simulate $S4 --gui
gui --note "7 gui | esmini: coastal city on its ground + buildings" simulate $S5 --gui
