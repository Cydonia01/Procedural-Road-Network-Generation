"""Graph -> graph passes run at the end of a generator (stage C).

Like kadmos.generators.repair, they only edit the RoadGraph (nodes, edges,
metadata), never OpenDRIVE, so any generator can use them and the builder
sees ordinary roads. Each is deterministic for the random generator it is
given.
"""
