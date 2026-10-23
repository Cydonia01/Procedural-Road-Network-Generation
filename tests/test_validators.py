"""Stage A5 validators: lane sections, curvature continuity, lateral acceleration,
and esmini driving every lane of chain roads."""

from __future__ import annotations

import pytest
from lxml import etree

from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.continuity import check_continuity_tree, check_curvature_tree
from roadgen.validate.dynamics import check_dynamics_tree

W = 3.5


def lane(lid: int, a: float = W, b: float = 0.0, link: str = "") -> str:
    return (f'<lane id="{lid}" type="driving"><link>{link}</link>'
            f'<width sOffset="0" a="{a}" b="{b}" c="0" d="0"/></lane>')


def section(s: float, right: str) -> str:
    return f'<laneSection s="{s}"><center><lane id="0" type="none"/></center><right>{right}</right></laneSection>'


def road(geometry: str, sections: str, length: float = 50.0, speed: float = 10.0,
         rid: int = 1, link: str = "", junction: int = -1) -> str:
    return (f'<road id="{rid}" length="{length}" junction="{junction}"><link>{link}</link>'
            f'<type s="0" type="town"><speed max="{speed}" unit="m/s"/></type>'
            f'<planView>{geometry}</planView><lanes>{sections}</lanes></road>')


def tree(*roads: str) -> etree._ElementTree:
    xml = '<OpenDRIVE><header revMajor="1" revMinor="6"/>' + "".join(roads) + "</OpenDRIVE>"
    return etree.ElementTree(etree.fromstring(xml))


LINE = '<geometry s="0" x="0" y="0" hdg="0" length="50"><line/></geometry>'


def test_lane_sections_must_link_with_matching_widths():
    good = road(LINE, section(0, lane(-1, link='<successor id="-1"/>') + lane(-2, b=-W / 20))
                + section(20, lane(-1, link='<predecessor id="-1"/>')))
    assert check_continuity_tree(tree(good)) == []
    abrupt = road(LINE, section(0, lane(-1, link='<successor id="-1"/>') + lane(-2))
                  + section(20, lane(-1, link='<predecessor id="-1"/>')))
    (err,) = check_continuity_tree(tree(abrupt))
    assert "lane -2 ends 3.500 m wide without a successor" in err
    jump = road(LINE, section(0, lane(-1, link='<successor id="-1"/>'))
                + section(20, lane(-1, a=3.0, link='<predecessor id="-1"/>')))
    (err,) = check_continuity_tree(tree(jump))
    assert "width jumps 3.500 -> 3.000" in err
    no_back = road(LINE, section(0, lane(-1, link='<successor id="-1"/>')) + section(20, lane(-1)))
    errors = check_continuity_tree(tree(no_back))
    assert any("does not link back" in e for e in errors)
    assert any("starts 3.500 m wide without a predecessor" in e for e in errors)


def test_curvature_jump_inside_a_road_is_an_error():
    geoms = ('<geometry s="0" x="0" y="0" hdg="0" length="20"><line/></geometry>'
             '<geometry s="20" x="20" y="0" hdg="0" length="30"><arc curvature="0.01"/></geometry>')
    errors, warnings = check_curvature_tree(tree(road(geoms, section(0, lane(-1)))))
    assert len(errors) == 1 and "curvature jumps by 0.0100" in errors[0] and not warnings
    smooth = ('<geometry s="0" x="0" y="0" hdg="0" length="20"><line/></geometry>'
              '<geometry s="20" x="20" y="0" hdg="0" length="30"><spiral curvStart="0" curvEnd="0.01"/></geometry>')
    assert check_curvature_tree(tree(road(smooth, section(0, lane(-1))))) == ([], [])


def test_curvature_jump_at_a_parampoly3_is_a_warning():
    poly = ('<geometry s="0" x="0" y="0" hdg="0" length="20"><line/></geometry>'
            '<geometry s="20" x="20" y="0" hdg="0" length="30"><paramPoly3 aU="0" bU="30" cU="-5" dU="0" '
            'aV="0" bV="0" cV="6" dV="0" pRange="normalized"/></geometry>')
    errors, warnings = check_curvature_tree(tree(road(poly, section(0, lane(-1)))))
    assert not errors and len(warnings) == 1


def test_dynamics_flags_a_curve_too_tight_for_its_speed():
    arc = '<geometry s="0" x="0" y="0" hdg="0" length="50"><arc curvature="0.05"/></geometry>'
    fast = road(arc, section(0, lane(-1)), speed=13.9)
    (err,) = check_dynamics_tree(tree(fast))
    assert "lateral acceleration" in err
    assert check_dynamics_tree(tree(road(arc, section(0, lane(-1)), speed=7.0))) == []
    assert check_dynamics_tree(tree(road(arc, section(0, lane(-1)), speed=13.9, junction=5)),
                               connecting=False) == []


def _bend_network() -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (300.0, 0.0), 3: (300.0, 300.0), 4: (600.0, 300.0)}.items():
        g.add_node(Node(nid, x, y, z=0.02 * x))
    g.add_edge(Edge(1, 1, 2, "street", 2, 2))
    g.add_edge(Edge(2, 2, 3, "street", 1, 1))
    g.add_edge(Edge(3, 3, 4, "street", 1, 1))
    return g


def _esmini_available() -> bool:
    try:
        from roadgen.sim.esmini import esmini_library

        esmini_library("esminiRMLib")
        return True
    except (FileNotFoundError, RuntimeError):
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found")
def test_esmini_drives_every_chain_lane_on_our_centre_line(tmp_path):
    from roadgen.sim.drive_check import drive_lanes

    b = OpenDriveBuilder()
    b.build(_bend_network())
    path = tmp_path / "bends.xodr"
    b.write(path)
    results = drive_lanes(path)
    assert {(r.road, r.lane) for r in results} == {(1, -1), (1, 1)}  # lanes -2/2 are dropped
    assert all(r.ok for r in results), [r.describe() for r in results]
