"""Writes runs/README.md from the run folders out/<hash>/: the example runs by phase.

    python runs/make_readme.py

The phase of a run is the number its --note starts with ("3 xodr | ...").
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from roadgen.history import RunStore  # noqa: E402

PHASES = {
    "1": "Input maps",
    "2": "Road network (RoadGraph JSON)",
    "3": "OpenDRIVE (.xodr) and validation",
    "4": "3D scene (buildings, ground)",
    "5": "Scenario (.xosc)",
    "6": "Simulation (headless esmini)",
    "7": "esmini GUI",
    "8": "Road geometry (stage A): fitted curves, metrics, align, curved streets",
}
# From scratch to the GUI: the notes of each chain's runs, in order.
CHAINS = {
    "Manhattan grid with buildings: pipeline, scenario, simulation, GUI": [
        "4 scene | pipeline: manhattan_like + buildings",
        "5 scenario | manhattan_like with its buildings as scene graph",
        "6 simulate | manhattan_like with buildings",
        "7 gui | esmini: manhattan_like with buildings, top camera",
        "7 gui | odrviewer: manhattan_like with buildings, random cars",
    ],
    "Manhattan grid, one stage per command": [
        "2 network | manhattan, preset manhattan_like (one-way avenues, rotated 29 deg)",
        "3 xodr | build-xodr: manhattan_like",
        "5 scenario | manhattan_like, 100 vehicles, random junction choices",
        "6 simulate | manhattan_like, random traffic",
    ],
    "Organic lsystem grid with buildings": [
        "4 scene | pipeline: lsystem grid_organic + buildings (.osgt)",
        "5 scenario | grid_organic with its buildings as scene graph",
        "6 simulate | grid_organic with buildings",
        "7 gui | odrviewer: grid_organic with buildings, 2 cars / 100 m",
    ],
    "Coastal city on input maps, with ground and buildings": [
        "1 maps | terrain noise, preset coastal (sea on one side)",
        "1 maps | elevation fbm, preset rolling, on the coastal terrain (water lies flat in the valleys)",
        "1 maps | population centers, preset polycentric, on the coastal terrain + elevation",
        "2 network | lsystem city grown on the coastal maps: highways towards the centres, heights (draped)",
        "3 xodr | build-xodr: coastal city with elevation profiles",
        "4 scene | ground: coastal terrain levelled under the coastal city, with its buildings",
        "5 scenario | coastal city on its ground + buildings scene",
        "6 simulate | coastal city on ground + buildings",
        "7 gui | esmini: coastal city on its ground + buildings",
        "7 gui | odrviewer: coastal city with heights, on its ground + buildings",
    ],
    "Road geometry: build, measure and drive the coastal city": [
        "8 geometry | build-xodr: coastal city with fitted curves, banking and junction planes",
        "8 geometry | metrics: coastal city (lateral acceleration, two-road junctions, curvature jumps)",
        "8 geometry | drive-check: coastal city, every junction movement and every lane end to end",
    ],
    "City on generated maps in one command, then the GUI": [
        "6 simulate | whole chain in one pipeline: maps, city, ground, buildings, esmini run",
        "7 gui | odrviewer: the one-command city (maps, ground, buildings) from phase 6",
    ],
}

INTRO = """# Example runs

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
"""


def main() -> None:
    runs = [r for r in RunStore(ROOT / "out").runs() if r.note[:1].isdigit()]
    by_note = {r.note: r.hash for r in runs}
    out = [INTRO, "## From scratch to the GUI\n"]
    for title, notes in CHAINS.items():
        hashes = [by_note[n] for n in notes if n in by_note]
        out.append(f"**{title}:** " + " → ".join(f"`{h}`" for h in hashes) + "\n")
    for phase, title in PHASES.items():
        out.append(f"## {phase}. {title}\n")
        for r in runs:
            if r.note.startswith(f"{phase} "):
                what = r.note.split("|", 1)[-1].strip()
                out.append(f"- `{r.hash}` {what}\n\n  ```bash\n  {r.command_line()}\n  ```\n")
    (HERE / "README.md").write_text("\n".join(out))


if __name__ == "__main__":
    main()
