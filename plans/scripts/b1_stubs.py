"""Stage B, B1: stub roads (< 5 m between two junctions) of suburb s1, today and with the
urban cross-section, after the fit has fixed the edges that are too short.

Showed that today's suburb already has 90 stubs (cul-de-sac loop arcs), which the fit must
leave alone.

Usage (dev container): python plans/scripts/b1_stubs.py
"""
from roadgen.config import DEFAULTS, section
from roadgen.generators import repair as R
from roadgen.generators.base import get_generator, load_params
from roadgen.geometry import alignment as A
from roadgen.geometry.cross_section import CrossSectionParams, loop_edges

gen = get_generator("lsystem")
cs = CrossSectionParams()
p = load_params(gen.params_type, preset="suburb", defaults=section(DEFAULTS, "generators", "lsystem"))
g = gen.generate(p, 1)
for eid in R.short_edges(g, cross_section=cs):
    R._collapse(g, eid, 2.0, 5, cs) or R._remove(g, eid)
loops = loop_edges(g)
for label, c in (("today", None), ("urban", cs)):
    try:
        na = A.network_alignment(g, cross_section=c)
    except A.ShortEdgeError as e:
        print(label, "short", e); continue
    js = set(na.junctions)
    stubs = [ca for ca in na.chains if len(ca.chain.edges) == 1 and ca.chain.start in js and ca.chain.end in js and ca.length < R.MIN_ROAD_LENGTH]
    print(label, "stubs", len(stubs))
    for ca in stubs[:12]:
        e = g.edges[ca.chain.edge_ids[0]]
        print(f"   edge {e.id} len {g.edge_length(e.id):.1f} road {ca.length:.2f} loop {e.id in loops} {e.road_class} {e.lanes_forward}/{e.lanes_backward} degrees {g.degree(e.from_node)}/{g.degree(e.to_node)} trims {na.trims[ca.chain.start].get(e.id,0):.1f}+{na.trims[ca.chain.end].get(e.id,0):.1f}")
