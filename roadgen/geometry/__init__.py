"""Neutral road geometry: shared by generators, maps, scene and the OpenDRIVE
builder. Never imports scenariogeneration or roadgen.opendrive."""

# Version of the road geometry the builder produces, recorded in the sidecar
# and the run history: 1 = one straight road per edge; 2 = chain roads with
# lines, spirals and arcs.
GEOMETRY_VERSION = 2
