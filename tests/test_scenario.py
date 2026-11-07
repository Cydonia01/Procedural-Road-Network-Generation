from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest
from lxml import etree

from cli import main
from roadgen.graph.io import save_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.scenario.xosc_builder import END_MARGIN, ScenarioBuilder, ScenarioParams
from tests.grids import small_grid


@pytest.fixture
def grid(tmp_path) -> Path:
    """A 3x4 two-way grid written as .json/.xodr/.roadmap.json; returns the .xodr."""
    g = small_grid(3, 4)
    save_json(g, tmp_path / "grid.json")
    b = OpenDriveBuilder()
    b.build(g)
    b.write(tmp_path / "grid.xodr")
    return tmp_path / "grid.xodr"


def write(xodr: Path, out: Path, **kw) -> Path:
    kw.setdefault("n_vehicles", 30)
    kw.setdefault("seed", 1)
    kw.setdefault("sim_duration", 20.0)
    return ScenarioBuilder.from_files(xodr).write(ScenarioParams(**kw), out)


def roads_by_id(xodr: Path) -> dict[int, etree._Element]:
    return {int(r.get("id")): r for r in etree.parse(str(xodr)).getroot().findall("road")}


def spawns(xosc: Path) -> dict[str, tuple[int, int, float]]:
    root = etree.parse(str(xosc)).getroot()
    out = {}
    for priv in root.iter("Private"):
        pos = priv.find(".//TeleportAction/Position/LanePosition")
        if pos is not None:
            out[priv.get("entityRef")] = (int(pos.get("roadId")), int(pos.get("laneId")), float(pos.get("s")))
    return out


# --- params -------------------------------------------------------------------------


@pytest.mark.parametrize("kw", [
    {"n_vehicles": -1},
    {"n_vehicles": 1, "sim_duration": 0},
    {"n_vehicles": 1, "speed_range": (10.0, 5.0)},
    {"n_vehicles": 1, "min_spawn_gap": -1.0},
])
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        ScenarioParams(**kw)


# --- structure ------------------------------------------------------------------------


def test_xosc_parses_with_vehicles_and_stop_trigger(grid, tmp_path):
    xosc = write(grid, tmp_path / "s.xosc", sim_duration=42.0)
    root = etree.parse(str(xosc)).getroot()
    assert root.tag == "OpenSCENARIO"
    assert len(root.findall("Entities/ScenarioObject")) == 30
    assert root.find("RoadNetwork/LogicFile").get("filepath") == "grid.xodr"
    cond = root.find(".//StopTrigger//SimulationTimeCondition")
    assert float(cond.get("value")) == 42.0 and cond.get("rule") == "greaterThan"
    speeds = [float(a.get("value")) for a in root.iter("AbsoluteTargetSpeed")]
    assert len(speeds) == 30 and all(8.0 <= v <= 13.9 for v in speeds)
    assert root.find(".//AssignRouteAction") is None  # no routes: random choices at junctions


def test_spawns_on_valid_lanes_with_gaps(grid, tmp_path):
    gap = 15.0
    xosc = write(grid, tmp_path / "s.xosc", n_vehicles=60, min_spawn_gap=gap)
    roads = roads_by_id(grid)
    lanes_used = defaultdict(list)
    for road_id, lane_id, s in spawns(xosc).values():
        road = roads[road_id]
        assert road.get("junction") == "-1", "never spawn inside a junction"
        lane = road.find(f"lanes/laneSection//lane[@id='{lane_id}']")
        assert lane is not None and lane.get("type") == "driving" and lane_id != 0
        assert END_MARGIN <= s <= float(road.get("length")) - END_MARGIN
        lanes_used[(road_id, lane_id)].append(s)
    assert sum(map(len, lanes_used.values())) == 60
    for ss in lanes_used.values():
        ss.sort()
        assert all(b - a >= gap for a, b in zip(ss, ss[1:]))


def test_deterministic(grid, tmp_path):
    a = write(grid, tmp_path / "a.xosc")
    b = write(grid, tmp_path / "b.xosc")
    c = write(grid, tmp_path / "c.xosc", seed=2)
    assert a.read_bytes() == b.read_bytes()
    assert spawns(a) != spawns(c)


def test_too_many_vehicles(grid, tmp_path):
    with pytest.raises(ValueError, match="could only place"):
        write(grid, tmp_path / "s.xosc", n_vehicles=5000, min_spawn_gap=50.0)


def test_graph_mapping_checked(grid):
    from roadgen.graph.io import load_json

    graph = load_json(grid.with_suffix(".json"))
    with pytest.raises(ValueError, match="edges without a road id"):
        ScenarioBuilder(grid, graph, {1: 1})


# --- CLI ------------------------------------------------------------------------------


def test_cli_scenario(grid, tmp_path):
    out = tmp_path / "cli.xosc"
    assert main(["scenario", str(grid), "--vehicles", "10", "--seed", "1", "-o", str(out)]) == 0
    assert len(spawns(out)) == 10


def test_cli_scenario_missing_sidecar(tmp_path):
    (tmp_path / "lonely.xodr").write_text("<OpenDRIVE/>")
    assert main(["scenario", str(tmp_path / "lonely.xodr"), "--vehicles", "1",
                 "-o", str(tmp_path / "x.xosc")]) == 1
