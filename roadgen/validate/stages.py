"""Checks after every pipeline stage (polish V1), so a failure names the stage that made it.

Each check is cheap and tests what its stage promises, on the stage's output
(and its input, where the stage must leave something unchanged):

- map (each input map, in the order they are built): the data has the grid's
  shape and finite values; terrain and style hold known classes; a generated
  elevation map has its water at the bottom (the generator sets water flat at
  its lowest height, land above it) and a generated population map is 0 on
  water. A loaded map file only gets warnings for these two (it is used as it
  is). An all-water terrain is a warning.
- maps (all of them): they share one grid (a warning: other grids are sampled).
- generate: the RoadGraph is well formed (RoadGraph.validate), has no node
  without a road, and lies on the maps (a warning per node outside them).
- align: only Node.radius_cap changed, and every cap is positive.
- drape: the plan view is unchanged; every height is finite; bridges and
  tunnels lie within their edge, sorted, without overlap; grades over the
  drape limit are warnings (drape logs them too).
- separate: the plan view is unchanged, and at every crossing without a
  junction the upper road is at least `clearance` above the lower one.
- annotate: the plan view and heights are unchanged; profiles and junction
  controls are known values.
- fit: the RoadGraph is well formed and no edge is too short for its
  junctions with the HD widths (the builder would stop on it).
- xodr: the file validators (roadgen.validate.*), run by the CLI's
  build_and_validate.
- scene: the ground mesh is finite and its triangles index real vertices;
  road surface it covers is a warning (the scene is presentation only).

Every check returns a StageReport; the CLI logs it and stops on a map's
errors, collects a graph stage's.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from roadgen.graph.model import RoadGraph
from roadgen.maps.model import CLASSES, LAND, VEGETATION, WATER, MapLayer

log = logging.getLogger(__name__)

HEIGHT_TOL = 1e-3  # [m]
CLEARANCE_TOL = 0.05  # [m] the straight-edge heights the check reads differ this much from the fit
MAX_LISTED = 10  # findings listed per stage; the rest are counted


@dataclass
class StageReport:
    stage: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def log(self) -> None:
        for kind, items, out in (("error", self.errors, log.error), ("warning", self.warnings, log.warning)):
            for item in items[:MAX_LISTED]:
                out("stage %s: %s", self.stage, item)
            if len(items) > MAX_LISTED:
                out("stage %s: ... %d %ss in all", self.stage, len(items), kind)
        if self.ok and not self.warnings:
            log.info("stage %s: ok", self.stage)

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return f"{self.stage} ok"
        parts = [f"{len(self.errors)} errors"] * bool(self.errors) + [f"{len(self.warnings)} warnings"] * bool(
            self.warnings)
        return f"{self.stage} {', '.join(parts)}"


def summary(reports: list[StageReport]) -> str:
    return "; ".join(r.summary() for r in reports)


# --- maps ----------------------------------------------------------------------------------------------


def check_map(layer: MapLayer, upstream: Optional[dict[str, MapLayer]] = None) -> StageReport:
    """One input map, with the maps built before it (see the module docstring)."""
    r = StageReport(f"map {layer.kind}")
    data = layer.data
    if data.shape != layer.grid.shape:
        r.errors.append(f"data shape {data.shape} != grid shape {layer.grid.shape}")
        return r
    if not np.isfinite(data.astype(float)).all():
        r.errors.append(f"{int((~np.isfinite(data.astype(float))).sum())} cells are not finite")
        return r
    if layer.kind in CLASSES:
        unknown = sorted(set(np.unique(data).tolist()) - set(CLASSES[layer.kind]))
        if unknown:
            r.errors.append(f"unknown classes {unknown}; known: {sorted(CLASSES[layer.kind])}")
    if layer.kind == "terrain" and not np.isin(data, (LAND, VEGETATION)).any():
        r.warnings.append("no land: nothing can be built on it")
    generated = "source" not in layer.metadata  # a loaded file is used as it is
    terrain = (upstream or {}).get("terrain")
    wet = terrain.on_grid(layer.grid) == WATER if terrain is not None else None
    if layer.kind == "elevation" and wet is not None and wet.any() and (~wet).any():
        top, bottom = float(data[wet].max()), float(data[~wet].min())
        if top > bottom + HEIGHT_TOL:
            (r.errors if generated else r.warnings).append(
                f"water up to {top:.2f} m, above the lowest land at {bottom:.2f} m")
    if layer.kind == "population":
        if (data < 0).any():
            r.errors.append(f"{int((data < 0).sum())} cells have a negative density")
        if wet is not None and (data[wet] > 0).any():
            (r.errors if generated else r.warnings).append(f"{int((data[wet] > 0).sum())} water cells have people")
    return r


def check_maps(layers: dict[str, MapLayer]) -> StageReport:
    """All input maps together: one grid."""
    r = StageReport("maps")
    grids = {kind: layer.grid for kind, layer in layers.items()}
    if len(set(grids.values())) > 1:
        r.warnings.append("the maps have different grids (sampled where they meet): "
                          + ", ".join(f"{k} {g.origin} {g.size} @ {g.cell_size} m" for k, g in grids.items()))
    return r


# --- graph stages --------------------------------------------------------------------------------------


def check_generate(graph: RoadGraph, layers: Optional[dict[str, MapLayer]] = None) -> StageReport:
    r = _graph_report("generate", graph)
    if layers:
        x0, x1, y0, y1 = next(iter(layers.values())).grid.bounds
        out = [n.id for n in graph.nodes.values() if not (x0 <= n.x <= x1 and y0 <= n.y <= y1)]
        if out:
            r.warnings.append(f"{len(out)} nodes outside the maps (e.g. {out[:5]})")
    return r


def check_align(before: RoadGraph, after: RoadGraph) -> StageReport:
    r = StageReport("align")
    r.errors += _plan_changes(before, after, heights=True, caps=False)
    bad = [n.id for n in after.nodes.values() if n.radius_cap is not None and not n.radius_cap > 0]
    if bad:
        r.errors.append(f"radius caps not positive at nodes {bad[:10]}")
    return r


def check_drape(before: RoadGraph, after: RoadGraph, max_grade: float = 0.0, grade_length: float = 25.0
                ) -> StageReport:
    r = StageReport("drape")
    r.errors += _plan_changes(before, after, heights=False)
    r.errors += _heights(after)
    r.errors += _structures(after)
    if max_grade > 0 and not r.errors:
        steep = [eid for eid in sorted(after.edges) if _steepest(after, eid, grade_length) > max_grade + 1e-9]
        if steep:
            r.warnings.append(f"{len(steep)} edges steeper than {max_grade:.0%} over {grade_length:g} m "
                              f"(e.g. {steep[:5]})")
    return r


def check_separate(before: RoadGraph, after: RoadGraph, clearance: float, design=None,
                   junction_margin: Optional[float] = None, cross_section=None) -> StageReport:
    """design, junction_margin, cross_section: as given to separate, which places each
    crossing where the fitted roads cross."""
    from roadgen.geometry.grade_separation import find_crossings, fitted

    r = StageReport("separate")
    r.errors += _plan_changes(before, after, heights=False)
    r.errors += _heights(after)
    r.errors += _structures(after)
    if r.errors:
        return r
    for c in find_crossings(after, fitted(after, design, junction_margin, cross_section)):
        gap = abs(_height(after, c.upper, c.d_upper) - _height(after, c.lower, c.d_lower))
        if gap < clearance - CLEARANCE_TOL:
            r.errors.append(f"edges {c.upper} and {c.lower} cross at ({c.x:.1f}, {c.y:.1f}) only {gap:.2f} m "
                            f"apart in height (clearance {clearance:g} m)")
    return r


def check_annotate(before: RoadGraph, after: RoadGraph) -> StageReport:
    from roadgen.geometry.cross_section import PROFILES
    from roadgen.opendrive.control import CONTROL_KINDS

    r = StageReport("annotate")
    r.errors += _plan_changes(before, after, heights=True)
    bad = sorted(e.id for e in after.edges.values() if e.cross_section is not None and e.cross_section not in PROFILES)
    if bad:
        r.errors.append(f"unknown cross-section profiles on edges {bad[:10]}; available: {PROFILES}")
    bad = sorted(n.id for n in after.nodes.values() if n.control is not None and n.control not in CONTROL_KINDS)
    if bad:
        r.errors.append(f"unknown junction controls at nodes {bad[:10]}; available: {CONTROL_KINDS}")
    return r


def check_fit(graph: RoadGraph, design, junction_margin: float, cross_section) -> StageReport:
    from roadgen.generators.repair import short_edges

    r = _graph_report("fit", graph)
    if not r.errors and cross_section is not None:
        short = short_edges(graph, design, junction_margin, cross_section)
        if short:
            r.errors.append(f"edges too short for their junctions with the HD widths: {short[:10]}")
    return r


def check_scene(mesh, covered: int = 0, samples: int = 0, worst: float = 0.0) -> StageReport:
    """The ground mesh (roadgen.scene.ground.GroundMesh) and its poke-through count."""
    r = StageReport("scene")
    if not np.isfinite(mesh.vertices).all() or not np.isfinite(mesh.normals).all():
        r.errors.append("ground mesh has vertices or normals that are not finite")
    tris = mesh.triangles
    if len(tris) and (tris.min() < 0 or tris.max() >= len(mesh.vertices)):
        r.errors.append("ground mesh triangles index missing vertices")
    if covered:
        r.warnings.append(f"the ground covers {covered} of {samples} road-surface samples (worst {worst:.2f} m)")
    return r


# --- helpers -------------------------------------------------------------------------------------------


def _graph_report(stage: str, graph: RoadGraph) -> StageReport:
    r = StageReport(stage, errors=graph.validate())
    lone = sorted(n for n in graph.nodes if graph.degree(n) == 0)
    if lone:
        r.errors.append(f"nodes without a road: {lone[:10]}")
    return r


def _plan_changes(before: RoadGraph, after: RoadGraph, heights: bool, caps: bool = True) -> list[str]:
    """What a stage that must keep the plan view (and with heights, the heights; with
    caps, the radius caps) changed: nodes, positions, edges, their ends and lanes."""
    out = []
    if set(before.nodes) != set(after.nodes):
        out.append(f"nodes changed: {len(before.nodes)} -> {len(after.nodes)}")
    if set(before.edges) != set(after.edges):
        out.append(f"edges changed: {len(before.edges)} -> {len(after.edges)}")
    if out:
        return out
    moved = [n for n, a in before.nodes.items()
             if (a.x, a.y) != (after.nodes[n].x, after.nodes[n].y)
             or (heights and a.z != after.nodes[n].z) or (caps and a.radius_cap != after.nodes[n].radius_cap)]
    if moved:
        out.append(f"nodes changed position{', height' if heights else ''}"
                   f"{' or radius cap' if caps else ''}: {moved[:10]}")
    changed = [e for e, a in before.edges.items()
               if (a.from_node, a.to_node, a.lanes_forward, a.lanes_backward, a.lane_width, a.road_class)
               != (lambda b: (b.from_node, b.to_node, b.lanes_forward, b.lanes_backward, b.lane_width,
                              b.road_class))(after.edges[e])
               or (heights and (a.profile, a.bridges, a.tunnels) != (after.edges[e].profile, after.edges[e].bridges,
                                                                     after.edges[e].tunnels))]
    if changed:
        out.append(f"edges changed ends, lanes, class{' or heights' if heights else ''}: {changed[:10]}")
    return out


def _heights(graph: RoadGraph) -> list[str]:
    bad = [n.id for n in graph.nodes.values() if not math.isfinite(n.z)]
    bad_edges = [e.id for e in graph.edges.values() if not all(math.isfinite(z) for z in e.profile)]
    out = [f"heights not finite at nodes {bad[:10]}"] if bad else []
    return out + ([f"profile heights not finite on edges {bad_edges[:10]}"] if bad_edges else [])


def _structures(graph: RoadGraph) -> list[str]:
    """Bridges and tunnels sorted along their edge, without overlaps (RoadGraph.validate
    checks that they lie within it)."""
    out = []
    for eid, e in sorted(graph.edges.items()):
        for kind, spans in (("bridges", e.bridges), ("tunnels", e.tunnels)):
            for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
                if b0 < a1 - 1e-6:
                    out.append(f"edge {eid}: {kind} overlap or are out of order ({a0:.1f}-{a1:.1f}, {b0:.1f}-{b1:.1f})")
    return out


def _height(graph: RoadGraph, eid: int, d: float) -> float:
    ds, zs = graph.edge_profile(eid)
    return float(np.interp(d, ds, zs))


def _steepest(graph: RoadGraph, eid: int, length: float) -> float:
    """Steepest rise / run over length [m] along an edge's straight profile."""
    ds, zs = graph.edge_profile(eid)
    if ds[-1] <= 0:
        return 0.0
    step = min(length, ds[-1])
    s = np.arange(0.0, ds[-1] - step + 1e-9, max(step / 2, 1.0))
    if not len(s):
        return 0.0
    rise = np.abs(np.interp(s + step, ds, zs) - np.interp(s, ds, zs))
    return float(rise.max() / step)
