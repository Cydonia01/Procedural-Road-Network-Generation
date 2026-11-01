#!/usr/bin/env bash
# Regenerates the two large files of the esmini issue draft
# (plans/2026-10-31-esmini-issue-draft.md), which are not committed:
# city_as_built.xodr.gz and city_reversed.xodr.gz (about 4.5 MB each).
#
# Usage (dev container), from the repo root:
#   bash plans/scripts/esmini_repro_city.sh [OUTDIR]     (default out/esmini-issue)
#
# Builds the stage B example `city` (lsystem preset city, seed 7, on maps)
# with HD maps, then writes it as built and with its roads reversed
# (b9_esmini_repro.py, no timing). The small grid files stay in
# plans/esmini-issue/.
set -eu
OUT=${1:-out/esmini-issue}
mkdir -p "$OUT"
python -m cli --no-history pipeline lsystem --preset city --seed 7 --maps --allow-traps --hd -o "$OUT/city" > "$OUT/city.run.log" 2>&1
python plans/scripts/b9_esmini_repro.py "$OUT/city.json" 7 "$OUT" 0
mv "$OUT/city_with_corners.xodr" "$OUT/city_as_built.xodr"
rm -f "$OUT/city_without_corners.xodr"
gzip -kf "$OUT/city_as_built.xodr" "$OUT/city_reversed.xodr"
ls -l "$OUT"/city_as_built.xodr.gz "$OUT"/city_reversed.xodr.gz
