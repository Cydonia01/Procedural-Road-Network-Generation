from __future__ import annotations

import json
import math
import shutil

import pytest

from cli import main
from roadgen.metrics.hdmap import hdmap_metrics, summary_lines
from roadgen.sim.screenshot import network_bounds, still_xosc, top_camera


def _lane(lid: int, kind: str, width: float, mark: str = "") -> str:
    m = f'<roadMark sOffset="0" type="{mark}" color="standard" width="0.15"/>' if mark else ""
    return f'<lane id="{lid}" type="{kind}"><link/><width sOffset="0" a="{width}" b="0" c="0" d="0"/>{m}</lane>'


def _xodr(tmp_path, sidecar=None):
    """A 200 m road (two lane sections: 1+1 driving with sidewalks, then without), a
    connecting road with one mark, 3 signals (one light), 2 objects, a controller."""
    road = (
        '<road id="1" length="200" junction="-1"><link/><planView><geometry s="0" x="0" y="0" hdg="0" '
        'length="200"><line/></geometry></planView><lanes>'
        '<laneSection s="0"><left>' + _lane(2, "sidewalk", 2.0) + _lane(1, "driving", 3.5, "broken")
        + '</left><center><lane id="0" type="none"><roadMark sOffset="0" type="solid" color="standard"/></lane></center>'
        '<right>' + _lane(-1, "driving", 3.5, "solid") + _lane(-2, "sidewalk", 2.0) + '</right></laneSection>'
        '<laneSection s="150"><left>' + _lane(1, "driving", 3.5) + '</left><center><lane id="0" type="none"/>'
        '</center><right>' + _lane(-1, "driving", 3.5) + '</right></laneSection></lanes>'
        '<objects><object id="1" s="10" t="0" type="crosswalk"/><object id="2" s="20" t="0" type="roadMark"/>'
        '<bridge id="3" s="0" length="10" type="concrete"/></objects>'
        '<signals><signal id="10" s="5" t="-5" country="DE" type="206" subtype="-1" dynamic="no"/>'
        '<signal id="11" s="5" t="-5" country="DE" type="274" subtype="" value="50" dynamic="no"/>'
        '<signal id="12" s="5" t="-5" country="OpenDRIVE" type="1000001" subtype="-1" dynamic="yes"/></signals></road>'
    )
    conn = ('<road id="2" length="10" junction="5"><link/><planView><geometry s="0" x="200" y="0" hdg="0" '
            'length="10"><line/></geometry></planView><lanes><laneSection s="0"><center><lane id="0" type="none"/>'
            '</center><right>' + _lane(-1, "driving", 3.5, "solid") + '</right></laneSection></lanes></road>')
    body = (road + conn + '<controller id="1"><control signalId="12" type="0"/></controller>'
            '<junction id="5"><connection id="0" incomingRoad="1" connectingRoad="2" contactPoint="start"/>'
            '<priority high="2" low="2"/><controller id="1"/></junction>')
    path = tmp_path / "t.xodr"
    path.write_text(f'<?xml version="1.0"?><OpenDRIVE><header revMajor="1" revMinor="6"/>{body}</OpenDRIVE>')
    if sidecar is not None:
        path.with_suffix(".roadmap.json").write_text(json.dumps(sidecar))
    return path


def test_counts(tmp_path):
    m = hdmap_metrics(_xodr(tmp_path))
    assert m["counts"] == {"roads": 1, "connecting_roads": 1, "junctions": 1, "road_length_km": 0.2}
    # driving: 2 lanes over 200 m; sidewalk: 2 lanes over the first 150 m
    assert m["lane_km_per_road_km"] == {"driving": 2.0, "sidewalk": 1.5}
    assert m["road_marks"] == {"broken/standard": 1, "solid/standard": 2}
    assert m["connecting_road_marks"] == 1
    assert m["signals"]["total"] == 3 and m["signals"]["dynamic"] == 1
    assert m["signals"]["by_type"] == {"DE:206": 1, "DE:274": 1, "OpenDRIVE:1000001": 1}
    assert m["signals"]["per_km"] == 15.0
    assert m["objects"]["by_type"] == {"crosswalk": 1, "roadMark": 1}  # the bridge is not an object
    assert (m["controllers"], m["controls"], m["junction_controllers"], m["junction_priorities"]) == (1, 1, 1, 1)
    assert "control_mix" not in m
    assert summary_lines(m)[0].startswith("t.xodr: 0.2 km of road")


def test_sidecar_parts(tmp_path):
    sidecar = {"junctions": [{"control": "signal"}], "signal_plans": [{"phases": [{}, {}]}],
               "lane_use": [{}, {}, {}], "banned": [{}]}
    m = hdmap_metrics(_xodr(tmp_path, sidecar))
    assert m["control_mix"] == {"signal": 1.0}
    assert m["signal_plans"] == {"junctions": 1, "phases": 2}
    assert (m["lane_use_records"], m["banned_movements"]) == (3, 1)


def test_metrics_cli_hd(tmp_path):
    path = _xodr(tmp_path)
    out = tmp_path / "m.json"
    assert main(["--no-history", "metrics", str(path), "--hd", "--json", str(out)]) == 0
    assert json.loads(out.read_text())["networks"][0]["hdmap"]["signals"]["total"] == 3


def test_top_camera_covers_bounds(tmp_path):
    bounds = network_bounds(_xodr(tmp_path))
    assert bounds == pytest.approx((0.0, 210.0, 0.0, 0.0))
    x, y, z, hdg, pitch = top_camera(bounds, (1280, 800))
    assert (x, y, hdg, pitch) == pytest.approx((105.0, 0.0, 0.0, math.pi / 2))
    assert z * 0.8 >= 210.0  # the view is wider than the network
    xosc = still_xosc(tmp_path / "t.xodr", tmp_path / "s.xosc", 0.5)
    assert 'SimulationTimeCondition value="0.5"' in xosc.read_text()


@pytest.mark.skipif(shutil.which("xvfb-run") is None, reason="needs xvfb-run (dev container)")
def test_view_screenshot(tmp_path):
    from PIL import Image

    xodr = tmp_path / "net.xodr"
    assert main(["--no-history", "build-xodr", "tests/fixtures/4way_avenue_x_street.json", str(xodr)]) == 0
    png = tmp_path / "net.png"
    assert main(["--no-history", "view", str(xodr), "--screenshot", str(png)]) == 0
    colors = Image.open(png).convert("RGB").getcolors(maxcolors=1 << 20)
    assert colors is not None and len(colors) > 10  # roads drawn, not just sky


def test_view_screenshot_bad_camera(tmp_path):
    with pytest.raises(SystemExit):
        main(["--no-history", "view", str(_xodr(tmp_path)), "--screenshot", str(tmp_path / "a.png"),
              "--camera", "1,2"])
