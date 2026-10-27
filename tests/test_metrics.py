from __future__ import annotations

import json
import math

import pytest

from cli import main
from roadgen.metrics.geometry import (
    GeometryMetricsParams,
    geometry_metrics,
    speed_records,
)
from roadgen.metrics.sweep import build_one
from roadgen.validate.continuity import RoadEval

K = 0.01  # arc curvature [1/m]
W = 3.5  # lane width [m]


def _lanes(left: bool = True, link: str = "") -> str:
    left_xml = (f'<left><lane id="1" type="driving"><link>{link.replace("ID", "1")}</link>'
                f'<width sOffset="0" a="{W}" b="0" c="0" d="0"/></lane></left>') if left else ""
    return (f'<lanes><laneSection s="0">{left_xml}<center><lane id="0" type="none"/></center>'
            f'<right><lane id="-1" type="driving"><link>{link.replace("ID", "-1")}</link>'
            f'<width sOffset="0" a="{W}" b="0" c="0" d="0"/></lane></right></laneSection></lanes>')


def _road(rid: int, length: float, geometry: str, speed: str = '<speed max="10" unit="m/s"/>',
          link: str = "", lane_link: str = "", junction: int = -1) -> str:
    return (f'<road id="{rid}" length="{length}" junction="{junction}"><link>{link}</link>'
            f'<type s="0" type="town">{speed}</type><planView>{geometry}</planView>'
            f'{_lanes(link=lane_link)}</road>')


def _write(tmp_path, body: str):
    path = tmp_path / "t.xodr"
    path.write_text(f'<?xml version="1.0"?><OpenDRIVE><header revMajor="1" revMinor="6"/>{body}</OpenDRIVE>')
    return path


ARC = f'<geometry s="0" x="0" y="0" hdg="0" length="50"><arc curvature="{K}"/></geometry>'
LINE = '<geometry s="0" x="0" y="0" hdg="0" length="50"><line/></geometry>'


def test_lane_curvature_of_an_arc(tmp_path):
    from lxml import etree

    road = RoadEval(etree.parse(str(_write(tmp_path, _road(1, 50, ARC)))).getroot().find("road"))
    # Right lane centre at t = -1.75 m drives along +s; left lane at +1.75 m drives along -s.
    assert road.lane_curvature(-1, 10.0) == pytest.approx(K / (1 + K * W / 2))
    assert road.lane_curvature(1, 10.0) == pytest.approx(-K / (1 - K * W / 2))


def test_lateral_acceleration_uses_peak_lane_curvature_and_speed(tmp_path):
    m = geometry_metrics(_write(tmp_path, _road(1, 50, ARC)))
    d = m["lateral_acceleration"]["road"]
    assert d["n"] == 1
    assert d["max"] == pytest.approx(100 * K / (1 - K * W / 2), abs=1e-4)
    assert m["min_radius"]["road"]["max"] == pytest.approx((1 - K * W / 2) / K, abs=1e-3)


def test_speed_units(tmp_path):
    from lxml import etree

    path = _write(tmp_path, _road(1, 50, LINE, speed='<speed max="36" unit="km/h"/>'))
    assert speed_records(etree.parse(str(path)).getroot().find("road")) == [(0.0, pytest.approx(10.0))]


def test_curvature_jump_at_a_geometry_join(tmp_path):
    two = ('<geometry s="0" x="0" y="0" hdg="0" length="20"><line/></geometry>'
           f'<geometry s="20" x="20" y="0" hdg="0" length="30"><arc curvature="{K}"/></geometry>')
    m = geometry_metrics(_write(tmp_path, _road(1, 50, two)))
    joins = m["curvature_jumps"]["joins"]
    assert joins["n"] == 1 and joins["over_limit"] == 1
    assert joins["max"] == pytest.approx(K)


def test_curvature_jump_at_a_road_link_and_two_road_junction(tmp_path):
    # Road 1 (straight) -> connecting road 3 (arc) in junction 2 -> road 4 (straight).
    arc_end = (math.sin(K * 50) / K, (1 - math.cos(K * 50)) / K, K * 50)
    road4 = (f'<geometry s="0" x="{arc_end[0]}" y="{arc_end[1]}" hdg="{arc_end[2]}" length="50">'
             '<line/></geometry>')
    body = (
        _road(1, 50, '<geometry s="0" x="-50" y="0" hdg="0" length="50"><line/></geometry>',
              link='<successor elementType="junction" elementId="2"/>')
        + _road(3, 50, ARC, junction=2, lane_link='<predecessor id="ID"/><successor id="ID"/>',
                link='<predecessor elementType="road" elementId="1" contactPoint="end"/>'
                     '<successor elementType="road" elementId="4" contactPoint="start"/>')
        + _road(4, 50, road4, link='<predecessor elementType="junction" elementId="2"/>')
        + '<junction id="2" name="j"/>'
    )
    m = geometry_metrics(_write(tmp_path, body))
    assert m["counts"]["junctions"] == 1
    assert m["counts"]["two_road_junctions"] == 1
    assert m["lateral_acceleration"]["bend"]["n"] == 1
    links = m["curvature_jumps"]["links"]
    assert links["n"] == 4  # two lanes, at both ends of the connecting road
    assert links["over_limit"] == 4


def test_params_validation():
    with pytest.raises(ValueError):
        GeometryMetricsParams(sample_step=0)


def test_build_one_reports_success():
    r = build_one("lsystem", "grid", 0)
    assert r.ok and r.edges > 0
    assert r.network["legs"]["4"] > 0 and 0 < r.network["cnr"] <= 1


def test_cli_metrics_writes_json(tmp_path):
    xodr = _write(tmp_path, _road(1, 50, ARC))
    out = tmp_path / "m.json"
    assert main(["metrics", str(xodr), "--json", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["networks"][0]["counts"]["roads"] == 1
    with pytest.raises(SystemExit):
        main(["metrics"])
