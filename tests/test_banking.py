"""Stage A8: banked curves and sloped junction planes."""

from __future__ import annotations

import math

import pytest
from lxml import etree

from roadgen.geometry import design
from roadgen.geometry.design import DesignParams
from roadgen.geometry.vertical import junction_planes
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.continuity import RoadEval, check_continuity, check_curvature
from roadgen.validate.dynamics import check_dynamics

V = 13.9


def l_shape(z=(0.0, 0.0, 0.0)) -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y), h in zip((1, 2, 3), ((0.0, 0.0), (400.0, 0.0), (400.0, 400.0)), z):
        g.add_node(Node(nid, x, y, z=h))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1))
    g.add_edge(Edge(2, 2, 3, "street", 1, 1))
    return g


def build(g: RoadGraph, tmp_path, params: DesignParams = DesignParams()):
    b = OpenDriveBuilder(design=params)
    b.build(g)
    path = tmp_path / "net.xodr"
    b.write(path)
    assert check_continuity(path) == []
    assert check_curvature(path) == ([], [])
    assert check_dynamics(path) == []
    return b, path, RoadEval(etree.parse(str(path)).getroot().find("road"))


def test_design_curve_is_banked_towards_its_centre(tmp_path):
    b, _, road = build(l_shape(), tmp_path)
    (c,) = b.alignment.chains[0].corners
    mid = 0.5 * (c.s_start + c.s_end)
    # A left turn falls to the left: negative superelevation, atan(e_max) on the arc.
    assert road.superelevation(mid) == pytest.approx(-math.atan(0.04))
    assert road.superelevation(c.s_start) == pytest.approx(0.0, abs=1e-12)
    assert road.superelevation(c.s_end) == pytest.approx(0.0, abs=1e-12)
    # The inner (left) lane is lower than the outer one.
    assert road.lane_height(1, mid) < road.lane_height(-1, mid)
    assert c.advisory_speed is None
    assert c.lane_radius == pytest.approx(design.min_radius(V, 0.04))


def test_without_banking_no_superelevation(tmp_path):
    _, _, road = build(l_shape(), tmp_path, DesignParams(banking=False))
    assert road.road.findall("lateralProfile/superelevation") == []
    assert road.superelevation(100.0) == 0.0


def _t_junction(z_east: float) -> RoadGraph:
    """A T at node 1, heights rising to the east."""
    g = RoadGraph()
    pts = {1: (0.0, 0.0, 0.0), 2: (200.0, 0.0, z_east), 3: (-200.0, 0.0, -z_east), 4: (0.0, 200.0, 0.0)}
    for nid, (x, y, h) in pts.items():
        g.add_node(Node(nid, x, y, z=h))
    for eid, (a, b) in enumerate([(1, 2), (3, 1), (1, 4)], 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    return g


def test_junction_plane_follows_the_approaches_and_is_capped(tmp_path):
    g = _t_junction(4.0)  # 2 % along x
    b, _, _ = build(g, tmp_path)
    plane = junction_planes(g, b.alignment, b.design)[1]
    assert plane.gx == pytest.approx(0.02, abs=2e-3) and abs(plane.gy) < 2e-3
    steep = _t_junction(40.0)  # 20 %: capped at 4 %
    plane = junction_planes(steep, _na(steep), DesignParams())[1]
    assert plane.grade == pytest.approx(0.04)
    flat = junction_planes(g, _na(g), DesignParams(junction_max_grade=0.0))[1]
    assert flat.grade == 0.0


def _na(g):
    from roadgen.geometry.alignment import network_alignment

    return network_alignment(g)


def _esmini_available() -> bool:
    try:
        from roadgen.sim.esmini import esmini_library

        esmini_library("esminiRMLib")
        return True
    except (FileNotFoundError, RuntimeError):
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found")
def test_esmini_drives_banked_lanes_on_our_centre_line(tmp_path):
    from roadgen.sim.drive_check import drive_lanes, drive_movements

    g = l_shape(z=(0.0, 6.0, 2.0))
    g.add_node(Node(4, 400.0, 600.0, z=0.0))
    g.add_node(Node(5, 600.0, 400.0, z=4.0))
    g.add_edge(Edge(3, 3, 4, "street", 1, 1))
    g.add_edge(Edge(4, 3, 5, "street", 1, 1))
    _, path, _ = build(g, tmp_path)
    lanes = drive_lanes(path)
    assert lanes and all(r.ok for r in lanes), [r.describe() for r in lanes]
    assert all(r.ok for r in drive_movements(path))
