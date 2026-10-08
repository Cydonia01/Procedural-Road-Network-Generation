from __future__ import annotations

import math
from itertools import combinations

import pytest
from shapely.geometry import LineString

from cli import main
from roadgen.generators.base import build_params
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.manhattan import ManhattanGenerator, ManhattanParams
from roadgen.graph.model import RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.continuity import check_continuity
from roadgen.validate.topology import check_topology


def road_set(g: RoadGraph) -> set:
    """Roads as unordered coordinate pairs + class (ids and directions ignored)."""
    out = set()
    for e in g.edges.values():
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        pa, pb = (round(a.x, 3), round(a.y, 3)), (round(b.x, 3), round(b.y, 3))
        out.add((min(pa, pb), max(pa, pb), e.road_class))
    return out


def grow(seed: int = 0, preset: str | None = None, **kw) -> RoadGraph:
    params = LSystemParams.preset(preset, **kw) if preset else LSystemParams(**kw)
    return LSystemGenerator().generate(params, seed)


# --- grid goal reproduces a Manhattan grid ---------------------------------------------


def test_grid_preset_is_the_10x10_manhattan_grid():
    g = grow(preset="grid")
    m = ManhattanGenerator().generate(ManhattanParams(10, 10), 0)
    assert road_set(g) == road_set(m)
    assert g.validate() == []


@pytest.mark.parametrize("start", [(0.0, 0.0), (0.5, 0.5), (1.0, 0.3)])
def test_grid_goal_matches_manhattan_from_any_lattice_start(start):
    # 5 avenues x 7 streets, spacing 200 x 60, rotated 20 degrees; the start
    # point is a lattice point so the grown lattice lines up with Manhattan's.
    a, s, dx, dy = 5, 7, 200.0, 60.0
    sx = round(start[0] * (a - 1)) / (a - 1)
    sy = round(start[1] * (s - 1)) / (s - 1)
    g = grow(extent=((a - 1) * dx, (s - 1) * dy), block_size=(dx, dy), start=(sx, sy),
             rotation_deg=20.0, origin=(100.0, -50.0))
    m = ManhattanGenerator().generate(
        ManhattanParams(a, s, avenue_spacing=dx, street_spacing=dy, rotation_deg=20.0,
                        origin=(100.0, -50.0)), 0)
    assert road_set(g) == road_set(m)


# --- local constraints hold on noisy growth -------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_organic_growth_respects_constraints(seed):
    p = LSystemParams.preset("grid_organic")
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
    p = LSystemParams.preset("grid_organic")
    a, b = LSystemGenerator().generate(p, 7), LSystemGenerator().generate(p, 7)
    assert a == b
    assert road_set(a) != road_set(LSystemGenerator().generate(p, 8))


def test_max_segments_budget():
    g = grow(preset="grid", max_segments=25)
    assert len(g.edges) == 25


@pytest.mark.parametrize("kw", [
    {"global_goal": "radial"},
    {"extent": (0.0, 100.0)},
    {"start": (1.5, 0.5)},
    {"branch_prob": 2.0},
    {"snap_radius": 200.0},
    {"min_angle_deg": 95.0},
    {"major_lanes": (0, 0)},
])
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        LSystemParams(**kw)


def test_unknown_preset():
    with pytest.raises(ValueError, match="unknown preset"):
        build_params(LSystemParams, preset="paris")


# --- OpenDRIVE ------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(3))
def test_organic_network_builds_valid_xodr(tmp_path, seed):
    g = grow(seed, preset="grid_organic")
    b = OpenDriveBuilder()
    b.build(g)
    xodr = tmp_path / "o.xodr"
    b.write(xodr)
    assert check_topology(xodr) == []
    assert check_continuity(xodr) == []


def test_acceptance_pipeline_lsystem_grid(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    monkeypatch.chdir(tmp_path)  # default output goes to ./out
    assert main(["pipeline", "lsystem", "--preset", "grid"]) == 0
    assert (tmp_path / "out" / "lsystem_grid_s0.xodr").is_file()


def _esmini_available() -> bool:
    from roadgen.sim.esmini import esmini_binary

    try:
        esmini_binary("esmini")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found")
def test_acceptance_runs_in_esmini(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert main(["pipeline", "lsystem", "--preset", "grid", "--simulate", "--sim-vehicles", "20",
                 "--sim-duration", "10", "-o", str(tmp_path / "g")]) == 0
