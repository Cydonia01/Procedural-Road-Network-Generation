#!/usr/bin/env bash
# Final-product examples: each is one `pipeline` run that gives a city with HD
# maps (side lanes, marks, signs, lights, turn lanes), a ground mesh where the
# city has input maps, and buildings, then opens it in odrviewer. Run from the
# repository root on the host:
#
#   bash runs/examples.sh            # generate all, then view each (needs a desktop)
#   NO_GUI=1 bash runs/examples.sh   # generate only
#   bash runs/examples.sh view       # view only (after a generate run)
#
# Every run gets its own folder out/<hash>/ with its outputs and run.yaml (see
# runs/README.md); replay one with `python -m cli HASH`. A view stays open
# until you close its window.
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

# name | note | pipeline arguments (the output name is added)
EXAMPLES=(
"city|Mixed-density city on generated maps: highways, ring roads, river, hills|--preset city --maps --seed 7 --ground"
"manhattan|Manhattan grid: alternating one-way avenues and streets, ring road|--preset manhattan_like --seed 1"
"paris|Paris: star of avenues, rings, grid outside|--preset paris"
"suburb|Suburb: cul-de-sac bulbs, suburban cross-section|--preset suburb --seed 1 --cross-section suburban"
"mixed_city|Mixed city: style-map districts on generated maps|--preset mixed_city --maps --seed 1 --ground"
"hill_town|Hill town: contour streets on hilly maps|--preset hill_town --maps --seed 1 --ground"
"freeways|City with freeways and overpasses|--preset city --maps --seed 1 --set highway_crossings=separated --ground"
"highway_corridor|Freeway corridor with a diamond interchange, lane adds and drops|--preset highway_corridor --maps --seed 1 --ground"
)

generate() {
    local entry name note args
    for entry in "${EXAMPLES[@]}"; do
        IFS='|' read -r name note args <<< "$entry"
        echo "== $name"
        # shellcheck disable=SC2086
        _run dev --note "$note" pipeline lsystem $args --allow-traps --buildings --drive-check -o "$name"
    done
}

view() {
    local entry name
    for entry in "${EXAMPLES[@]}"; do
        IFS='|' read -r name _ _ <<< "$entry"
        local dir
        dir=$(ls -d out/*/ | while read -r d; do [ -f "$d/$name.xodr" ] && echo "${d%/}"; done | head -1)
        [ -z "$dir" ] && { echo "no run for $name"; continue; }
        local scene=()
        for s in _scene.osgt _buildings.osgt; do
            [ -f "$dir/$name$s" ] && { scene=(--scenegraph "$dir/$name$s"); break; }
        done
        echo "== view $name (close the window to continue)"
        docker compose run --rm gui python -m cli --no-history view "$dir/$name.xodr" "${scene[@]}"
    done
}

case "${1:-all}" in
    view) view ;;
    *) generate; [ -n "${NO_GUI:-}" ] || view ;;
esac
