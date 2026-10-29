#!/usr/bin/env bash
# Stage B example set: build, check and photograph every example network.
#
# Usage (dev container), from the repo root:
#   bash plans/scripts/stage_b_examples.sh OUTDIR [extra pipeline flags...]
# e.g. bash plans/scripts/stage_b_examples.sh out/stage_b/b0
#      bash plans/scripts/stage_b_examples.sh out/stage_b/b2 --hd
#
# For each network: pipeline (validators, drive-check except interchanges),
# a top-view screenshot, metrics --hd, build time and .xodr size; on `city`
# and `mixed_city` also a short headless simulate. Everything ends up in
# OUTDIR/<name>.*, with a summary in OUTDIR/summary.json. Runs are not
# recorded (--no-history). Exit status 1 if any step failed.
set -u
OUT=${1:?usage: stage_b_examples.sh OUTDIR [pipeline flags...]}
shift
mkdir -p "$OUT"
cli() { python -m cli --no-history "$@"; }

# name | generator | preset | seed | maps | drive-check | simulate
EXAMPLES="
grid|lsystem|grid|0|no|yes|no
manhattan_like|lsystem|manhattan_like|1|no|yes|no
paris|lsystem|paris|1|no|yes|no
suburb|lsystem|suburb|1|no|yes|no
organic_town|lsystem|organic_town|1|no|yes|no
city|lsystem|city|7|yes|yes|yes
mixed_city|lsystem|mixed_city|1|yes|yes|yes
highway_corridor|lsystem|highway_corridor|1|yes|no|no
junction_k|junction|k|0|no|yes|no
junction_roundabout|junction|roundabout|0|no|yes|no
junction_diamond|junction|diamond|0|no|no|no
"

failed=0
summary="$OUT/summary.jsonl"
: > "$summary"
for line in $EXAMPLES; do
    IFS='|' read -r name gen preset seed maps check sim <<< "$line"
    prefix="$OUT/$name"
    flags=(--preset "$preset" --seed "$seed" --allow-traps)
    [ "$maps" = yes ] && flags+=(--maps)
    [ "$check" = yes ] && flags+=(--drive-check)
    start=$(date +%s.%N)
    if cli pipeline "$gen" "${flags[@]}" "$@" -o "$prefix" > "$prefix.run.log" 2>&1; then status=ok; else status=FAILED; failed=1; fi
    secs=$(python -c "print(round($(date +%s.%N) - $start, 1))")
    cli view "$prefix.xodr" --screenshot "$prefix.view.png" >> "$prefix.run.log" 2>&1 || status="$status, no screenshot"
    cli metrics "$prefix.xodr" --hd --json "$prefix.metrics.json" >> "$prefix.run.log" 2>&1 || status="$status, no metrics"
    if [ "$sim" = yes ]; then
        cli scenario "$prefix.xodr" --vehicles 50 --duration 30 --seed 1 -o "$prefix.xosc" >> "$prefix.run.log" 2>&1 \
            && cli simulate "$prefix.xosc" >> "$prefix.run.log" 2>&1 || status="$status, simulate failed"
    fi
    size=$(stat -c %s "$prefix.xodr" 2>/dev/null || echo 0)
    echo "{\"name\": \"$name\", \"status\": \"$status\", \"pipeline_s\": $secs, \"xodr_bytes\": $size}" >> "$summary"
    echo "$name: $status (${secs} s, $size bytes)"
done
exit $failed
