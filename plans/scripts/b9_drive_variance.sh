#!/usr/bin/env bash
# Stage B9 (prompt 12): is the drive-check's run-time variance the Docker VM's?
#
# Usage, on the HOST (macOS, Docker Desktop), from the repo root:
#   bash plans/scripts/b9_drive_variance.sh XODR [RUNS] [OUTDIR]
# e.g. bash plans/scripts/b9_drive_variance.sh out/stage_b/b8r/mixed_city.xodr 3 out/b9/variance
#
# Runs `cli drive-check XODR` RUNS times (default 3), one after the other in one
# dev container, and meanwhile records:
# - docker stats of every container, every 2 s (CPU %, memory) -> stats.csv;
# - the host's load average and swap use (sysctl vm.swapusage), every 10 s -> host.log;
# - per run, inside the VM: wall time; the drive-check's own phase times (RoadManager
#   load, driving) from its log; the kernel's swap-in / swap-out page counters
#   (/proc/vmstat pswpin, pswpout) and memory / CPU pressure stall totals
#   (/proc/pressure/*, total= in us) before and after; MemAvailable before -> runs.log.
set -u
XODR=${1:?usage: b9_drive_variance.sh XODR [RUNS] [OUTDIR]}
RUNS=${2:-3}
OUT=${3:-out/b9/variance}
mkdir -p "$OUT"
: > "$OUT/stats.csv"
: > "$OUT/host.log"

( while true; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}' 2>/dev/null \
        | sed "s/^/$(date +%s),/" >> "$OUT/stats.csv"
    sleep 2
  done ) &
STATS=$!
( while true; do
    echo "$(date +%s) load $(sysctl -n vm.loadavg) $(sysctl -n vm.swapusage)" >> "$OUT/host.log"
    sleep 10
  done ) &
HOST=$!
trap 'kill $STATS $HOST 2>/dev/null' EXIT

docker compose run --rm -T dev bash -c '
snap() {
    echo "$1 $(grep -E "^pswp(in|out) " /proc/vmstat | tr "\n" " ")mem $(grep -E "^some" /proc/pressure/memory | sed "s/.*total=//")us cpu $(grep -E "^some" /proc/pressure/cpu | sed "s/.*total=//")us avail $(grep MemAvailable /proc/meminfo | tr -s " " | cut -d" " -f2) kB"
}
for i in $(seq 1 '"$RUNS"'); do
    snap "run $i before:"
    start=$(date +%s.%N)
    python -m cli --no-history drive-check '"$XODR"' > '"$OUT"'/run$i.log 2>&1
    echo "run $i wall $(python -c "print(round($(date +%s.%N) - $start, 1))") s"
    grep -h "drive-check \(movements\|lanes\):" '"$OUT"'/run$i.log | sed "s/^INFO roadgen.sim.drive_check: /run $i /"
    snap "run $i after: "
done' 2>&1 | grep -v "^ Container" | tee "$OUT/runs.log"
