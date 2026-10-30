"""Stage B, B1: what makes city (maps, seed 7) edges too short for cross-section trims.

Counts short_edges with the urban profile, without highway shoulders at junctions,
with a smaller highway corner radius, and both.

Usage (dev container): python plans/scripts/b1_short_causes.py
"""
import sys

sys.path.insert(0, "plans/scripts")
from side_room import build

from roadgen.generators.repair import short_edges
from roadgen.geometry.cross_section import CrossSectionParams

g, _ = build("city", 7, True, {})
cases = {"urban": CrossSectionParams(),
         "no highway shoulder": CrossSectionParams(urban_highway=()),
         "highway radius 4.9": CrossSectionParams(corner_radius=(3.0, 4.9, 4.9)),
         "both": CrossSectionParams(urban_highway=(), corner_radius=(3.0, 4.9, 4.9))}
for k, cs in cases.items():
    s = short_edges(g, cross_section=cs)
    print(k, len(s), sorted({g.edges[e].road_class for e in s}))
