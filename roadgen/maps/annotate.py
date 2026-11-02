"""HD-map annotations from the input maps (stage B5): cross-section profiles and
junction control.

After grade separation, with HD maps on (`annotate`, pipeline with maps). The
builder never sees a map, so what depends on one goes into the graph's two
optional overrides here:

- Edge.cross_section by the population density at the edge's midpoint:
  urban from urban_density, suburban from suburban_density (and in the style
  map's suburb districts below urban density), rural below;
- Node.control at junctions of three or more roads whose highest class is
  an avenue or more and that also have a lower class: "signal" from
  signal_density, "stop" below stop_density (junction_control section).

Values already set (by hand) are kept. Deterministic; the input graph is left
unchanged; the counts go to graph.metadata["annotate"].
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Optional

from roadgen.config import DEFAULTS, section
from roadgen.geometry.chains import find_chains, junction_nodes
from roadgen.geometry.cross_section import CLASSES
from roadgen.graph.model import RoadGraph
from roadgen.maps.model import STYLE_SUBURB, MapLayer

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "annotate")
_DC = section(DEFAULTS, "junction_control")
RANK = {c: k for k, c in enumerate(CLASSES)}  # street < avenue < highway


@dataclass(frozen=True)
class AnnotateParams:
    urban_density: float = _D["urban_density"]  # urban profile from this density [people/km^2]
    suburban_density: float = _D["suburban_density"]  # suburban from this one, rural below [people/km^2]

    def __post_init__(self) -> None:
        if not 0 <= self.suburban_density <= self.urban_density:
            raise ValueError("annotate needs 0 <= suburban_density <= urban_density")


def annotate(graph: RoadGraph, population: MapLayer, style: Optional[MapLayer] = None,
             params: AnnotateParams = AnnotateParams(), signal_density: float = _DC["signal_density"],
             stop_density: float = _DC["stop_density"]) -> RoadGraph:
    """A copy of graph with Edge.cross_section and Node.control set from the maps;
    signal_density, stop_density [people/km^2] from the junction_control section."""
    out = copy.deepcopy(graph)
    profiles = {"urban": 0, "suburban": 0, "rural": 0}
    for e in out.edges.values():
        if e.cross_section is not None:
            continue
        a, b = out.nodes[e.from_node], out.nodes[e.to_node]
        x, y = (a.x + b.x) / 2, (a.y + b.y) / 2
        density = float(population.sample(x, y))
        suburb = style is not None and int(style.sample(x, y)) == STYLE_SUBURB
        if density >= params.urban_density and not suburb:
            e.cross_section = "urban"
        elif density >= params.suburban_density:  # suburb districts are at most suburban
            e.cross_section = "suburban"
        else:
            e.cross_section = "rural"
        profiles[e.cross_section] += 1
    controls = {"signal": 0, "stop": 0}
    for n in sorted(junction_nodes(out, find_chains(out))):
        node = out.nodes[n]
        ranks = [RANK[e.road_class] for e in out.incident_edges(n)]
        if (node.control is not None or node.kind == "ramp" or len(ranks) < 3
                or max(ranks) < RANK["avenue"] or min(ranks) == max(ranks)):
            continue
        density = float(population.sample(node.x, node.y))
        if density >= signal_density:
            node.control = "signal"
        elif density < stop_density:
            node.control = "stop"
        else:
            continue
        controls[node.control] += 1
    out.metadata["annotate"] = {"profiles": profiles, "controls": controls}
    log.info("annotate: profiles %s, junction control %s", profiles, controls)
    return out
