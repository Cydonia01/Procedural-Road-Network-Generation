"""Writes runs/README.md from the run folders out/<hash>/: the example runs.

    python runs/make_readme.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from roadgen.history import RunStore  # noqa: E402

INTRO = """# Example runs

Ten networks, each the final product: one `pipeline` run with HD maps (side
lanes, road marks, signs, lights, turn lanes), buildings, a ground mesh where
the city is grown on input maps, and a drive-check. Each opens in odrviewer.
Made by [`examples.sh`](examples.sh); this page is written from the run
folders by [`make_readme.py`](make_readme.py).

```bash
bash runs/examples.sh            # generate all, then view each (needs a desktop session)
NO_GUI=1 bash runs/examples.sh   # generate only
bash runs/examples.sh view       # view only
```

Every successful `python -m cli` command gets its own folder `out/<hash>/`
(git-ignored) with its outputs and `run.yaml`, which shows the command and
the parameters that differ from `roadgen/defaults.yaml`. See
[CLI reference → history](../docs/cli-reference.md#run-folders-history-replay-run).

```bash
python -m cli history        # all runs: hash, note, command
python -m cli HASH           # run one again
```

In Docker, prefix commands with `docker compose run --rm -T dev`, or
`docker compose run --rm gui` for views (see
[docs/docker.md](../docs/docker.md)). A view stays open until you close the
window and is not recorded as a run.
"""


def view_command(folder: Path) -> str | None:
    """`view` command for the network a run wrote, on its 3D scene if it has one."""
    xodrs = sorted(folder.glob("*.xodr"))
    if not xodrs:
        return None
    xodr = xodrs[0]
    rel = xodr.relative_to(ROOT)
    for suffix in ("_scene.osgt", "_buildings.osgt"):
        scene = xodr.with_name(xodr.stem + suffix)
        if scene.is_file():
            return f"python -m cli view {rel} --scenegraph {scene.relative_to(ROOT)}"
    return f"python -m cli view {rel}"


def main() -> None:
    out = [INTRO, "## Cities\n"]
    for r in RunStore(ROOT / "out").runs():
        if not r.note:  # examples.sh gives every example a note; other runs stay out
            continue
        lines = [r.command_line()]
        view = view_command(ROOT / "out" / r.hash)
        if view:
            lines.append(f"{view}   # look at it")
        out.append(f"- `{r.hash}` {r.note}\n\n  ```bash\n" + "".join(f"  {x}\n" for x in lines) + "  ```\n")
    (HERE / "README.md").write_text("\n".join(out))


if __name__ == "__main__":
    main()
