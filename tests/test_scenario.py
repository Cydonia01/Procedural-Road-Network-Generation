from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest
from lxml import etree

from cli import main
from roadgen.generators.manhattan import ManhattanGenerator, ManhattanParams
from roadgen.graph.io import save_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.scenario.xosc_builder import END_MARGIN, ScenarioBuilder, ScenarioParams
from roadgen.sim.runner import parse_log


@pytest.fixture
def grid(tmp_path) -> Path:
    """A 3x4 two-way grid written as .json/.xodr/.roadmap.json; returns the .xodr."""
    g = ManhattanGenerator().generate(ManhattanParams(3, 4), 0)
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
    {"n_vehicles": 1, "spawn_mode": "teleport"},
    {"n_vehicles": 1, "speed_range": (10.0, 5.0)},
    {"n_vehicles": 1, "min_spawn_gap": -1.0},
])
def test_invalid_params(kw):
    with pytest.raises(ValueError):
        ScenarioParams(**kw)


# --- structure ------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["random_lanes", "explicit_routes"])
def test_xosc_parses_with_vehicles_and_stop_trigger(grid, tmp_path, mode):
    xosc = write(grid, tmp_path / "s.xosc", spawn_mode=mode, sim_duration=42.0)
    root = etree.parse(str(xosc)).getroot()
    assert root.tag == "OpenSCENARIO"
    assert len(root.findall("Entities/ScenarioObject")) == 30
    assert root.find("RoadNetwork/LogicFile").get("filepath") == "grid.xodr"
    cond = root.find(".//StopTrigger//SimulationTimeCondition")
    assert float(cond.get("value")) == 42.0 and cond.get("rule") == "greaterThan"
    speeds = [float(a.get("value")) for a in root.iter("AbsoluteTargetSpeed")]
    assert len(speeds) == 30 and all(8.0 <= v <= 13.9 for v in speeds)
    has_routes = root.find(".//AssignRouteAction") is not None
    assert has_routes == (mode == "explicit_routes")


@pytest.mark.parametrize("mode", ["random_lanes", "explicit_routes"])
def test_spawns_on_valid_lanes_with_gaps(grid, tmp_path, mode):
    gap = 15.0
    xosc = write(grid, tmp_path / "s.xosc", n_vehicles=60, min_spawn_gap=gap, spawn_mode=mode)
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
    a = write(grid, tmp_path / "a.xosc", spawn_mode="explicit_routes")
    b = write(grid, tmp_path / "b.xosc", spawn_mode="explicit_routes")
    c = write(grid, tmp_path / "c.xosc", spawn_mode="explicit_routes", seed=2)
    assert a.read_bytes() == b.read_bytes()
    assert spawns(a) != spawns(c)


def test_explicit_routes_follow_movements(grid, tmp_path):
    builder = ScenarioBuilder.from_files(grid)
    plans = builder.plan(ScenarioParams(40, seed=3, spawn_mode="explicit_routes"))
    allowed = {
        ((m["from_road"], m["from_lane"]), (m["to_road"], m["to_lane"])) for m in builder.movements
    }
    normal = set(builder.edge_to_road.values())
    for p in plans:
        assert p.route[0] == (p.road, p.lane)
        assert len(p.route) >= 2
        assert all(road in normal for road, _ in p.route)
        assert all(step in allowed for step in zip(p.route, p.route[1:]))


def test_explicit_route_waypoints_in_xosc(grid, tmp_path):
    xosc = write(grid, tmp_path / "s.xosc", n_vehicles=5, spawn_mode="explicit_routes")
    root = etree.parse(str(xosc)).getroot()
    builder = ScenarioBuilder.from_files(grid)
    plans = builder.plan(ScenarioParams(5, seed=1, sim_duration=20.0, spawn_mode="explicit_routes"))
    for plan, route in zip(plans, root.iter("Route")):
        wps = [(int(lp.get("roadId")), int(lp.get("laneId"))) for lp in route.iter("LanePosition")]
        assert wps == plan.route


def test_too_many_vehicles(grid, tmp_path):
    with pytest.raises(ValueError, match="could only place"):
        write(grid, tmp_path / "s.xosc", n_vehicles=5000, min_spawn_gap=50.0)


def test_explicit_routes_need_movements(grid, tmp_path):
    builder = ScenarioBuilder(grid, None, ScenarioBuilder.from_files(grid).edge_to_road, movements=[])
    with pytest.raises(ValueError, match="movements"):
        builder.plan(ScenarioParams(3, spawn_mode="explicit_routes"))


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


# --- runner -----------------------------------------------------------------------------


def test_parse_log():
    text = "\n".join([
        "esmini GIT REV: v3.9.0",
        "[] [info] Loaded scenario.xosc",
        "[] [warn] 3D model car_red.osgb not located, using specified file path",
        "[] [warn] Something odd about the road",
        "[3.500] [error] Entity car1 left the road",
        "[120.050] [info] Closing",
    ])
    sim_time, errors, warnings, assets = parse_log(text)
    assert sim_time == pytest.approx(120.05)
    assert errors == ["[3.500] [error] Entity car1 left the road"]
    assert warnings == ["[] [warn] Something odd about the road"]
    assert assets == 1


def _esmini_available() -> bool:
    from roadgen.sim.esmini import esmini_binary

    try:
        esmini_binary("esmini")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found ($ESMINI_HOME unset)")
@pytest.mark.parametrize("mode", ["random_lanes", "explicit_routes"])
def test_tiny_scenario_runs_in_esmini(tmp_path, monkeypatch, mode):
    from roadgen.sim.runner import run_esmini

    g = ManhattanGenerator().generate(ManhattanParams(2, 2), 0)
    b = OpenDriveBuilder()
    b.build(g)
    b.write(tmp_path / "net" / "tiny.xodr")
    xosc = ScenarioBuilder.from_files(tmp_path / "net" / "tiny.xodr").write(
        ScenarioParams(5, seed=1, sim_duration=10.0, spawn_mode=mode), tmp_path / "scen" / "tiny.xosc"
    )
    monkeypatch.chdir(tmp_path)  # the .xodr path inside the .xosc must not depend on cwd
    result = run_esmini(xosc, timeout=120)
    assert result.returncode == 0, result.errors
    assert result.ok, result.errors
    assert result.n_vehicles == 5
    assert result.sim_time == pytest.approx(10.0, abs=0.2)
    assert result.record_path.is_file() and result.csv_path.is_file()
    assert result.real_time_factor > 1.0
