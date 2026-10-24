"""Writes runs/README.md from runs/history.json: the example runs by phase.

    python runs/make_readme.py

The phase of a run is the number its --note starts with ("3 xodr | ...").
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

HERE = Path(__file__).parent
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

Every successful `python -m cli` command is recorded in
[`history.json`](history.json) under a 12-digit hash of its inputs (all
parsed parameters, defaults included, and the effective configuration).
The same inputs are stored once, however they were typed. See
[CLI reference → history](../docs/cli-reference.md#history).

```bash
python -m cli history                 # all runs: hash, note, command
python -m cli history --grep "7 gui"  # filter by note or command
python -m cli HASH                    # run it again (same as: replay HASH)
python -m cli replay HASH --show      # only print the command
python -m cli --note "my run" ...     # describe a run in the history
python -m cli --no-history ...        # don't record a throwaway run
```

In Docker, prefix the commands with `docker compose run --rm -T dev`, or
with `docker compose run --rm gui` for phase 7 (needs a desktop session; see
[docs/docker.md](../docs/docker.md)). The phase 7 views (`odrviewer`) stay open
until you close the window; the `simulate --gui` runs close when the scenario
ends.

The runs below were made by [`examples.sh`](examples.sh) (which also
regenerates the input images in [`inputs/`](inputs/)); this page is written
by [`make_readme.py`](make_readme.py). They write to `out/examples/`
(git-ignored), and later runs read files written by earlier ones, so replay
them in order. Each note starts with its phase (`1 maps |`, `2 network |`,
`3 xodr |`, `4 scene |`, `5 scenario |`, `6 simulate |`, `7 gui |`,
`8 geometry |`):

```bash
# everything, oldest first (phase 7 needs the gui service)
for h in $(python -m cli history | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
# one phase
for h in $(python -m cli history --grep "1 maps |" | grep -oE '^[0-9a-f]{12}'); do python -m cli $h; done
```

or one chain from the list below.

The runs of phases 1–7 were recorded with road geometry version 1 (one
straight road per edge, every bend a junction). Replaying them builds the
current geometry (version 2: fitted curves, banking, junction planes) and
logs a warning that the network differs; phase 8 shows the new geometry.
"""


def main() -> None:
    data = json.loads((HERE / "history.json").read_text())
    runs = [(h, r) for h, r in data["runs"].items()]
    by_note = {r["note"]: h for h, r in runs}
    out = [INTRO, "## From scratch to the GUI\n"]
    for title, notes in CHAINS.items():
        hashes = [by_note[n] for n in notes if n in by_note]
        out.append(f"**{title}:** " + " → ".join(f"`{h}`" for h in hashes) + "\n")
    for phase, title in PHASES.items():
        out.append(f"## {phase}. {title}\n")
        for h, r in runs:
            if not r["note"].startswith(f"{phase} "):
                continue
            what = r["note"].split("|", 1)[-1].strip()
            cmd = "python -m cli " + shlex.join(r["argv"])
            if r.get("config_file"):
                cmd = cmd.replace("python -m cli ", f"python -m cli --config {r['config_file']} ", 1)
            out.append(f"- `{h}` {what}\n\n  ```bash\n  {cmd}\n  ```\n")
    (HERE / "README.md").write_text("\n".join(out))


if __name__ == "__main__":
    main()
