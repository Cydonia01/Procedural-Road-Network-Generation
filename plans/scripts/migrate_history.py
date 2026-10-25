"""One-off: replay the runs of the old runs/history.json into run folders (out/<hash>/).

    docker compose run --rm -T dev python plans/scripts/migrate_history.py [HISTORY_JSON]

Runs are replayed oldest first with the current code and defaults. Output paths
(-o, positional outputs, metrics --json) become file names inside the run's
folder, and input paths that an earlier run wrote point to that run's folder.
GUI runs (view, simulate --gui) are skipped: they need the gui service.
Writes out/migration.json: old hash -> new hash (or the reason it has none).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cli import run_argv  # noqa: E402

log = logging.getLogger("migrate")

# Commands whose output is a positional argument, and its index in argv.
POSITIONAL_OUTPUT = {"plot-graph": 2, "plot-map": 2, "build-xodr": 2, "ground": 1}


def output_indices(argv: list[str]) -> list[int]:
    """Indices in argv of the paths a command writes to."""
    out = [i + 1 for i, a in enumerate(argv) if a in ("-o", "--output", "--json")]
    if argv[0] in POSITIONAL_OUTPUT:
        out.append(POSITIONAL_OUTPUT[argv[0]])
    return out


def rewrite(argv: list[str], moved: dict[str, str]) -> tuple[list[str], list[str]]:
    """(new argv, old output directories)."""
    argv, dirs = list(argv), []
    for i in output_indices(argv):
        dirs.append(str(Path(argv[i]).parent))
        argv[i] = Path(argv[i]).name
    for i, a in enumerate(argv):
        key, sep, value = a.partition("=")
        if a in moved:
            argv[i] = moved[a]
        elif sep and value in moved:
            argv[i] = f"{key}={moved[value]}"
    return argv, dirs


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "runs" / "history.json")
    data = json.loads(path.read_text())
    moved: dict[str, str] = {}  # old output file -> file in the new run folder
    result: dict[str, str] = {}
    for old, run in data["runs"].items():
        argv = run["argv"]
        if argv[0] == "view" or "--gui" in argv:
            result[old] = "skipped: GUI"
            continue
        new_argv, dirs = rewrite(argv, moved)
        if run.get("config_file"):
            new_argv = ["--config", run["config_file"], *new_argv]
        if run.get("note"):
            new_argv = ["--note", run["note"], *new_argv]
        if argv[0] == "simulate":  # esmini's outputs used to go next to the .xosc
            dirs.append(str(Path(argv[1]).parent))
        try:
            rc, h = run_argv(new_argv)
        except SystemExit as e:
            rc, h = 1, None
            log.error("%s: %s", old, e)
        if rc != 0 or h is None:
            result[old] = "failed"
            log.error("%s failed: %s", old, " ".join(new_argv))
            continue
        result[old] = h
        folder = ROOT / "out" / h
        for f in folder.iterdir():
            for d in dirs:
                moved[str(Path(d) / f.name)] = str(Path("out") / h / f.name)
        log.info("%s -> %s", old, h)
    (ROOT / "out" / "migration.json").write_text(json.dumps(result, indent=1) + "\n")
    failed = [k for k, v in result.items() if v == "failed"]
    log.info("%d runs migrated, %d skipped, %d failed: %s",
             sum(not v.startswith(("skipped", "failed")) for v in result.values()),
             sum(v.startswith("skipped") for v in result.values()), len(failed), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
