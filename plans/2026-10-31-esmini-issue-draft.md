# esmini issue draft: RoadManager load time (two findings)

## Prompts

**2026-10-31, prompt 13** (item 3, answers to the B9 report)

> 3. Don't build or profile esmini. Instead, put a minimal reproducing .xodr and an issue draft for both esmini findings (corner-road load cost, 3× load-time variance) under plans/; I'll decide whether to file it.

## Decisions

- Not filed: the user decides (prompt 13).
- Two findings, written as two issues so they can be filed separately; both are about `RoadManager` load time (`RM_Init`, `roadmanager::Position::LoadOpenDrive`) in esmini v3.9.0.
- Reproducing files in `plans/esmini-issue/` (gzipped OpenDRIVE 1.6, made by `plans/scripts/b9_esmini_repro.py` from the `grid` and `city` examples of stage B9). The city files are the only ones on which the variance showed; they are 4.5 MB gzipped each.
- Measured with esmini's prebuilt Linux x86_64 binaries (`esmini-bin_Linux.zip`, v3.9.0) in Docker (Debian, Python ctypes calling `RM_Init` of `libesminiRMLib.so`), on an Apple Silicon Mac under Rosetta. The draft says so: the maintainers may not see the same absolute times natively.

---

## Issue 1: Junction roads with only non-driving lanes load much more slowly than other roads

**esmini version:** v3.9.0 (prebuilt `esmini-bin_Linux.zip`)
**Platform:** Linux x86_64 (Debian, in Docker on macOS, Rosetta)

### What happens

`RM_Init` (and `esmini --osc` on a scenario using the network) takes much longer for networks that contain short connecting roads carrying only sidewalk-type lanes around junction corners, relative to how much road they add:

| File | Lane length [km] (corner roads) | `RM_Init`, best of 5 [s] (median, worst) |
|---|---|---|
| `grid_without_corners.xodr` | 298.9 (0) | 2.73 (2.76, 2.77) |
| `grid_with_corners.xodr` | 319.8 (20.9) | 3.66 (3.69, 3.78) |
| `city_as_built.xodr` without its corner roads | 758.8 (0) | 30.60 (30.65, 31.12) |
| `city_as_built.xodr` | 798.2 (39.4) | 56.82 (58.21, 58.56) |

On the grid, the corner roads are 7 % of the lane length and add 34 % to the load; on a larger network (city, 4,365 roads, 348 junctions) they are 5 % of the lane length and nearly double it. The extra time per metre of corner-road lane grows with the size of the network: about 44 ms per corner road on the city against 3 ms on the grid.

### The corner roads

Each is a connecting road (`junction` attribute set) between two neighbouring arms of a junction, around the corner on the right-turn path, with only non-driving lanes: `biking`, `curb` (with `<height>`), `sidewalk`. Example from the grid file (road 186, junction 177):

- `planView`: three spirals (about 14 m);
- `laneOffset` constant −1.75;
- right lanes: `biking`, `curb` (height 0.15), `sidewalk` whose width is a chain of 17 cubic records (it widens smoothly between the two arms);
- road links to the two arms (`contactPoint` start), lane links to their side lanes, and a `<connection>` in the junction with `<laneLink>`s between side lanes only.

### What we ruled out (on the city file, `RM_Init`)

- **Their place in the file:** the corner roads first (60.2 s) or last (61.3 s) against as built (56.8 s).
- **OSI points:** esmini exports no OSI lanes for them (the same 11,220 OSI lanes and 136,089 centre-line points with and without them); they add 21,868 lane-boundary points, 0.55 per lane metre against 0.29 elsewhere.
- **Attributes, one at a time** (variants of the file): without curb heights, with constant widths and no lane offset, without lane links (still ≥ 40 s), without their junction connections (40.5 s: about 10 s of the 26 s), with one lane each (43.9 s), with straight geometry (45.6 s). No single attribute explains it.

We could not profile esmini (no profiling build at hand; under Rosetta sampling profilers do not see its code).

### To reproduce

```bash
gunzip -k grid_with_corners.xodr.gz grid_without_corners.xodr.gz
python - <<'EOF'
import ctypes, sys, time
lib = ctypes.CDLL("path/to/libesminiRMLib.so")
lib.RM_Init.argtypes = [ctypes.c_char_p]
for f in ("grid_without_corners.xodr", "grid_with_corners.xodr"):
    t = time.perf_counter(); lib.RM_Init(f.encode()); print(f, round(time.perf_counter() - t, 2), "s")
EOF
```

(One process per file gives cleaner numbers; `RM_Close` between loads works too.)

### Question

Is this expected (some per-road or per-junction work that grows with the network), and is there a way to make such roads cheaper to load, or an attribute we should set differently on them?

---

## Issue 2: The same file loads in very different times from run to run (up to 3×), all of it CPU time

**esmini version:** v3.9.0 (prebuilt `esmini-bin_Linux.zip`)
**Platform:** Linux x86_64 (Debian, in Docker on macOS, Rosetta)

### What happens

Loading one and the same `.xodr` with `RM_Init`, each time in a fresh process, sometimes takes about three times as long, and the extra time is CPU time of the process (no waiting, no swapping: checked with `/proc/vmstat`, `/proc/pressure`, `docker stats`):

| File | `RM_Init` per run, wall / CPU [s] |
|---|---|
| `city_reversed.xodr`, earlier build of the same network (3 runs) | 335.1 / 335.1, 329.3 / 329.3, 105.8 / 105.7 |
| the same earlier build (3 more runs) | 102.9 (best), 287.8 (median), 335.7 (worst) |
| `city_reversed.xodr` (the attached file, 5 runs) | 302.5 / 302.4, 178.7 / 178.6, 98.5 / 98.5, 99.9 / 99.9, 100.4 / 100.4 |
| `city_as_built.xodr` (the attached file, original order, 5 runs) | 56.8 – 58.6 (CPU the same) |

An earlier drive check on another network took 2,278 s once where it usually takes about 400 s (two loads), which we now think was the same effect.

`city_reversed.xodr` is `city_as_built.xodr` with its `<road>` elements in reverse order (the normal roads last). That order also makes the load slower on every run (about 2×, also on the small grid: 3.66 → 7.40 s, deterministic there), which fits `OpenDrive::GetRoadById` scanning `road_` linearly while OSI points are computed per lane; but the 3× spread between runs of the same file is a separate effect: we only saw it on the large file.

### What we could not check

Whether the slow runs depend on the memory layout: Docker's default seccomp profile does not allow `setarch -R` (no address-space randomisation), so we could not run the load with a fixed layout.

### To reproduce

Load `city_reversed.xodr` with `RM_Init` five or more times, each in a fresh process, and compare the CPU times (`time.process_time()` around the call, or `/usr/bin/time`).

### Question

Is there something in the load (for example an iteration whose length depends on uninitialised memory, a container ordered by pointer, or a search whose starting point varies) that could make the work differ between runs on the same input?

---

## Files (`plans/esmini-issue/`)

| File | Size (gzipped) | Roads / junctions | For |
|---|---|---|---|
| `grid_with_corners.xodr.gz` | 0.24 MB | 1,645 / 96 (352 corner roads) | issue 1 |
| `grid_without_corners.xodr.gz` | 0.12 MB | 1,293 / 96 | issue 1 |
| `grid_reversed.xodr.gz` | 0.24 MB | as `grid_with_corners`, roads reversed | issue 2 (order) |
| `city_as_built.xodr.gz` | 4.5 MB | 4,365 / 348 | issues 1, 2 |
| `city_reversed.xodr.gz` | 4.5 MB | as above, roads reversed | issue 2 |
