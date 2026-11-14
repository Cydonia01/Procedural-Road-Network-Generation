from __future__ import annotations

import math
from itertools import combinations

import pytest
from shapely.geometry import LineString

from cli import fit_graph
from roadgen.config import DEFAULTS
from roadgen.generators.base import build_params
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.model import RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.validate.continuity import check_continuity
from roadgen.validate.topology import check_topology
from tests.grids import GRID


def road_set(g: RoadGraph) -> set:
    """Roads as unordered coordinate pairs (ids, directions and classes ignored)."""
    out = set()
    for e in g.edges.values():
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        pa, pb = (round(a.x, 3), round(a.y, 3)), (round(b.x, 3), round(b.y, 3))
        out.add((min(pa, pb), max(pa, pb)))
    return out


def grow(seed: int = 0, preset: str | None = None, **kw) -> RoadGraph:
    params = LSystemParams.preset(preset, **kw) if preset else LSystemParams(**kw)
    return LSystemGenerator().generate(params, seed)


# --- grid goal grows an exact lattice ---------------------------------------------------


def lattice(a: int, s: int, dx: float, dy: float, rotation_deg: float = 0.0,
            origin: tuple[float, float] = (0.0, 0.0)) -> set:
    """road_set of an a x s lattice: avenues along y at x = i dx, streets along x at y = j dy."""
    c, sn = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))

    def pt(i: int, j: int) -> tuple[float, float]:
        x, y = i * dx, j * dy
        return round(origin[0] + c * x - sn * y, 3), round(origin[1] + sn * x + c * y, 3)

    out = {(min(pt(i, j), pt(i, j + 1)), max(pt(i, j), pt(i, j + 1)))
           for i in range(a) for j in range(s - 1)}
    out |= {(min(pt(i, j), pt(i + 1, j)), max(pt(i, j), pt(i + 1, j)))
            for i in range(a - 1) for j in range(s)}
    return out


def test_grid_is_a_10x10_lattice():
    g = grow(**GRID)
    assert road_set(g) == lattice(10, 10, 250.0, 80.0)
    assert g.validate() == []


@pytest.mark.parametrize("start", [(0.0, 0.0), (0.5, 0.5), (1.0, 0.3)])
def test_grid_goal_grows_the_lattice_from_any_lattice_start(start):
    # 5 avenues x 7 streets, spacing 200 x 60, rotated 20 degrees; the start
    # point is a lattice point so the grown lattice lines up.
    a, s, dx, dy = 5, 7, 200.0, 60.0
    sx = round(start[0] * (a - 1)) / (a - 1)
    sy = round(start[1] * (s - 1)) / (s - 1)
    g = grow(extent=((a - 1) * dx, (s - 1) * dy), block_size=(dx, dy), start=(sx, sy),
             rotation_deg=20.0, origin=(100.0, -50.0))
    assert road_set(g) == lattice(a, s, dx, dy, 20.0, (100.0, -50.0))


# --- local constraints hold on noisy growth -------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_organic_growth_respects_constraints(seed):
    # bulbs are added after growth; a quarter of city's area holds the same local rules
    p = LSystemParams.preset("city", dead_ends=False, extent=(1500.0, 1200.0))
    g = LSystemGenerator().generate(p, seed)
    assert g.validate() == []
    assert len(g.edges) > 50

    lines = {eid: LineString([(g.nodes[e.from_node].x, g.nodes[e.from_node].y),
                              (g.nodes[e.to_node].x, g.nodes[e.to_node].y)])
             for eid, e in g.edges.items()}
    # Minimum segment length.
    assert min(line.length for line in lines.values()) >= p.min_segment_length - 1e-6
    # Planar: roads only meet at shared nodes.
    for (i, la), (j, lb) in combinations(lines.items(), 2):
        ea, eb = g.edges[i], g.edges[j]
        shared = {ea.from_node, ea.to_node} & {eb.from_node, eb.to_node}
        inter = la.intersection(lb)
        if shared:
            assert inter.geom_type == "Point", (i, j)
        else:
            assert inter.is_empty, (i, j)
    # Minimum angle between roads at every node.
    for nid in g.nodes:
        dirs = []
        for e in g.incident_edges(nid):
            other = g.nodes[e.to_node if e.from_node == nid else e.from_node]
            dirs.append(math.atan2(other.y - g.nodes[nid].y, other.x - g.nodes[nid].x))
        for u, v in combinations(dirs, 2):
            diff = abs((u - v + math.pi) % (2 * math.pi) - math.pi)
            assert math.degrees(diff) >= p.min_angle_deg - 1e-6
    # Inside the area.
    w, h = p.extent
    assert all(-1e-6 <= n.x <= w + 1e-6 and -1e-6 <= n.y <= h + 1e-6 for n in g.nodes.values())


def test_deterministic():
    p = LSystemParams.preset("city", extent=(1500.0, 900.0))
    a, b = LSystemGenerator().generate(p, 7), LSystemGenerator().generate(p, 7)
    assert a == b
    assert road_set(a) != road_set(LSystemGenerator().generate(p, 8))


def test_max_segments_budget():
    g = grow(**GRID, max_segments=25, dead_ends=False)
    assert len(g.edges) == 25


# Every LSystemParams check, by feature (one table instead of one test per feature file).
@pytest.mark.parametrize("kw", [
    # growth
    {"global_goal": "radial"},
    {"extent": (0.0, 100.0)},
    {"start": (1.5, 0.5)},
    {"branch_prob": 2.0},
    {"snap_radius": 200.0},
    {"min_angle_deg": 95.0},
    {"major_lanes": (0, 0)},
    {"arterial_share": 0.6, "collector_share": 0.6},
    {"curve_tortuosity": 0.9},
    # grids: one-way patterns and the ring road
    {"oneway_pattern": ("zigzag", "none")},
    {"oneway_pattern": ("none",)},
    {"boundary": "wrap"},
    {"ring_offset": 0.0},
    {"ring_lanes": (0, 0)},
    # pattern fields
    {"pattern_weights": (1, 0, 0)},
    {"pattern_weights": (0, 0, 0, 0, 0)},
    {"pattern_weights": (1, -1, 0, 0, 0)},
    {"organic": 1.5},
    {"radial_spokes": 1},
    # suburbs: connectors and cul-de-sacs
    {"detour_ratio": 0.5},
    {"connect_radius": 0.0},
    {"culdesac_prob": 2.0},
    {"culdesac_radius": 0.0},
    {"suburb_end_prob": -0.1},
    # highways on input maps, crossings and interchanges
    {"highway_max_turn_deg": 70.0, "highway_max_detour_deg": 60.0},
    {"prune_factor": 1.5},
    {"highway_lanes": (0, 0)},
    {"highway_crossings": "maybe"},
    {"interchanges": True},
    {"interchange_spacing": 0.0},
    {"highway_lane_change_prob": 2.0},
])
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        LSystemParams(**kw)


def test_unknown_preset():
    with pytest.raises(ValueError, match="unknown preset"):
        build_params(LSystemParams, preset="atlantis")


# --- OpenDRIVE ------------------------------------------------------------------------------


@pytest.mark.slow
def test_organic_network_builds_valid_xodr(tmp_path):
    # As the pipeline does: the HD fit repairs edges too short once side lanes widen the trims.
    g = fit_graph(grow(0, preset="city", extent=(1500.0, 900.0)), DEFAULTS, HdMapParams(), 2.0)
    b = OpenDriveBuilder()
    b.build(g)
    xodr = tmp_path / "o.xodr"
    b.write(xodr)
    assert check_topology(xodr) == []
    assert check_continuity(xodr) == []


