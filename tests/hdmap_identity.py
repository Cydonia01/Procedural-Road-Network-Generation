"""Hashes of built networks that stage B must leave unchanged while HD maps are off.

Stage B adds side lanes, marks, signals, objects and junction control to the
OpenDRIVE builder, all behind hdmap.enabled. With it off, the builder's
output must stay byte for byte what it was: this guard hashes the .xodr and
the .roadmap.json sidecar that `pipeline` writes for a few networks. The
expected values were recorded with the code before stage B (B0, on top of
1cc11cc).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

EXPECTED = Path(__file__).parent / "data" / "hdmap_identity.json"
# (name, lsystem preset, seed, with input maps)
CASES = [
    ("grid", "grid", 0, False),
    ("manhattan_like", "manhattan_like", 1, False),
    ("organic_town", "organic_town", 1, False),
    ("city_maps", "city", 7, True),
]


def build(preset: str, seed: int, maps: bool, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """Hashes of the .xodr and the sidecar a pipeline run writes."""
    from cli import main

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "net"
        argv = ["--no-history", *extra, "pipeline", "lsystem", "--preset", preset, "--seed", str(seed),
                "--allow-traps", "-o", str(out)]
        if maps:
            argv.append("--maps")
        if main(argv) != 0:
            raise RuntimeError(f"pipeline failed: {argv}")
        return {suffix: hashlib.sha256(out.with_suffix(suffix).read_bytes()).hexdigest()[:16]
                for suffix in (".xodr", ".roadmap.json")}


def hashes() -> dict[str, dict[str, str]]:
    return {name: build(preset, seed, maps) for name, preset, seed, maps in CASES}


if __name__ == "__main__":  # record the expected values (only before an intended change)
    EXPECTED.write_text(json.dumps(hashes(), indent=2) + "\n")
    print(EXPECTED.read_text())
