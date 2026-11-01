"""Stage B9: where the cross-section fit (repair before an HD build) spends its time.

  python plans/scripts/b9_fit_profile.py PRESET SEED
  e.g. python plans/scripts/b9_fit_profile.py city 7

Runs `pipeline lsystem --preset PRESET --seed SEED --maps --hd` (no drive-check, not
recorded) under cProfile and prints the callees of repair.fit_cross_sections by
cumulative time, and how often the trims and the alignment were computed. Run in the
dev container.
"""
from __future__ import annotations

import cProfile
import pstats
import sys

import cli


def main() -> None:
    preset, seed = sys.argv[1], sys.argv[2]
    prof = cProfile.Profile()
    prof.enable()
    cli.main(["--no-history", "pipeline", "lsystem", "--preset", preset, "--seed", seed, "--maps", "--hd",
              "--allow-traps", "-o", f"out/b9/fit_profile/{preset}"])
    prof.disable()
    stats = pstats.Stats(prof).sort_stats("cumulative")
    stats.print_callees(r"fit_cross_sections")
    stats.print_callees(r"repair_short_edges")
    stats.print_callees(r"_drop_pockets")
    stats.print_stats(r"(short_edges|stub_roads|network_alignment|node_trims|_collapse|_drop_pockets)")


if __name__ == "__main__":
    main()
