"""Stage B6: traffic lights, controllers, fixed-time plans, lights in OpenSCENARIO."""

from __future__ import annotations

import dataclasses
import json
import math
import re
import subprocess

import pytest
from lxml import etree

from roadgen.graph.io import load_json
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.opendrive.control import Approach
from roadgen.opendrive.hdmap import HdMapParams
from roadgen.opendrive.lights import (
    GREEN,
    RED,
    YELLOW,
    TrafficLightParams,
    phase_groups,
    signal_plan,
    state_changes,
)
from roadgen.scenario.xosc_builder import ScenarioBuilder, ScenarioParams
from roadgen.validate.hdmap import check_hdmap, check_hdmap_tree

HD = HdMapParams(enabled=True)
P = TrafficLightParams()


def _approaches(angles_deg, cls="avenue", speed=13.9) -> list[Approach]:
    return [Approach((k, "end"), k, cls, 4, speed, math.radians(a) + math.pi) for k, a in enumerate(angles_deg, 1)]


def _groups(angles):
    return [{k for k, _ in g} for g in phase_groups(_approaches(angles))]


def test_phase_groups_pair_opposite_arms():
    assert sorted(map(sorted, _groups([0, 90, 180, 270]))) == [[1, 3], [2, 4]]
    assert sorted(map(sorted, _groups([0, 90, 180]))) == [[1, 3], [2]]  # a T: the stem alone
    five = _groups([0, 72, 180, 216, 288])
    assert {1, 3} in five and len(five) == 4
    skewed = _groups([0, 90, 180, 230])  # 90 and 230 are 40 deg off opposite
    assert {1, 3} in skewed and len(skewed) == 3


def test_yellow_and_cycle_timings():
    assert P.yellow(13.9) == 3.3 and P.yellow(8.0) == 3.0 and P.yellow(22.2) == 4.7
    approaches = _approaches([0, 90, 180, 270])
    lanes = {(1, "end"): 2, (3, "end"): 2, (2, "end"): 1, (4, "end"): 1}
    lights = {a.key: [str(k)] for k, a in enumerate(approaches, 1)}
    plan = signal_plan(9, 3, approaches, lanes, lights, P, seed=0)
    greens = [p["duration"] for p in plan.phases if p["kind"] == "green"]
    assert len(greens) == 2 and greens[0] == pytest.approx(2 * greens[1], abs=1.0)  # split by incoming lanes
    assert min(greens) >= P.min_green
    assert plan.cycle == pytest.approx(sum(p["duration"] for p in plan.phases))
    assert plan.cycle == pytest.approx(60.0)
    assert [p["kind"] for p in plan.phases] == ["green", "yellow", "all_red"] * 2
    assert all(set(p["states"].values()) == {RED} for p in plan.phases if p["kind"] == "all_red")
    assert 0 <= plan.offset < plan.cycle and plan.offset == int(plan.offset)
    assert signal_plan(9, 3, approaches, lanes, lights, P, seed=0) == plan  # seeded


def test_min_green_stretches_the_cycle():
    approaches = _approaches([0, 72, 144, 216, 288])  # five arms, no opposite pairs
    lights = {a.key: [str(k)] for k, a in enumerate(approaches, 1)}
    plan = signal_plan(9, 3, approaches, {}, lights, dataclasses.replace(P, min_green=15.0), seed=0)
    assert len(plan.groups) == 5
    assert all(p["duration"] >= 15.0 for p in plan.phases if p["kind"] == "green")
    assert plan.cycle > P.cycle[1]


def test_state_changes_follow_the_plan():
    approaches = _approaches([0, 90, 180, 270])
    lights = {a.key: [str(k)] for k, a in enumerate(approaches, 1)}
    plan = signal_plan(9, 3, approaches, {}, lights, P, seed=1)
    changes = state_changes(plan, 200.0)
    assert changes[0][0] == 0.0 and set(changes[0][1]) == {"1", "2", "3", "4"}
    times = [t for t, _ in changes]
    assert times == sorted(times) and times[-1] <= 200.0
    state = {}
    for t, st in changes:
        state.update(st)
        assert set(state.values()) <= {RED, YELLOW, GREEN}
        assert sum(v == GREEN for v in state.values()) in (0, 2)  # one opposite pair at a time
    seen = {s for _, st in changes for s, v in st.items() if v == GREEN}
    assert seen == {"1", "2", "3", "4"}


def _signal_fixture():
    g = load_json("tests/fixtures/4way_avenue_x_street.json")
    for e in g.edges.values():
        e.road_class, e.lanes_forward, e.lanes_backward, e.lane_width = "avenue", 2, 2, 3.5
    return g


def _build(graph, tmp_path, hd=HD):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "net.xodr"
    b = OpenDriveBuilder(hd=hd)
    b.build(graph)
    b.write(path)
    return path, etree.parse(str(path)).getroot(), json.loads(path.with_suffix(".roadmap.json").read_text())


def test_lights_controllers_and_plans_in_the_xodr(tmp_path):
    path, root, sidecar = _build(_signal_fixture(), tmp_path)
    assert check_hdmap(path) == []
    lights = [s for s in root.iter("signal") if s.get("dynamic") == "yes"]
    assert len(lights) == 4 and {s.get("type") for s in lights} == {"1000001"}
    assert {s.get("country") for s in lights} == {"OpenDRIVE"}
    controllers = root.findall("controller")
    assert len(controllers) == 1
    assert {c.get("signalId") for c in controllers[0].findall("control")} == {s.get("id") for s in lights}
    # controllers sit between the roads and the junctions
    tags = [el.tag for el in root]
    assert tags.index("controller") > max(i for i, t in enumerate(tags) if t == "road")
    assert tags.index("controller") < tags.index("junction")
    junction = next(j for j in root.findall("junction") if j.find("controller") is not None)
    assert junction.find("controller").get("id") == controllers[0].get("id")
    (plan,) = sidecar["signal_plans"]
    assert plan["junction"] == int(junction.get("id")) and len(plan["groups"]) == 2
    assert set(plan["lights"]) == {s.get("id") for s in lights}


def test_wide_approaches_get_an_overhead_light(tmp_path):
    g = _signal_fixture()
    for e in g.edges.values():
        e.lanes_forward = e.lanes_backward = 3
    _, root, _ = _build(g, tmp_path)
    lights = [s for s in root.iter("signal") if s.get("dynamic") == "yes"]
    over = [s for s in lights if float(s.get("zOffset")) > 5.0]
    assert len(lights) == 8 and len(over) == 4


def test_validator_catches_broken_lights(tmp_path):
    path, _, sidecar = _build(_signal_fixture(), tmp_path)

    def errors(edit_xodr=None, edit_plan=None) -> list[str]:
        tree = etree.parse(str(path))
        side = json.loads(json.dumps(sidecar))
        if edit_xodr:
            edit_xodr(tree.getroot())
        if edit_plan:
            edit_plan(side["signal_plans"][0])
        return check_hdmap_tree(tree, side)

    def uncontrolled(root):
        ctl = root.find("controller/control")
        ctl.getparent().remove(ctl)

    def all_green(plan):
        for sid in plan["phases"][0]["states"]:
            plan["phases"][0]["states"][sid] = GREEN

    def short_green(plan):
        plan["phases"][0]["duration"] = 4.0

    def never_green(plan):
        sid = next(iter(plan["lights"]))
        for p in plan["phases"]:
            p["states"][sid] = RED

    def no_light(root):
        for s in list(root.iter("signal")):
            s.getparent().remove(s)
        for c in list(root.iter("controller")):
            c.getparent().remove(c)

    assert errors() == []
    assert any("in 0 controllers" in e for e in errors(uncontrolled))
    assert any("conflicting movements" in e for e in errors(edit_plan=all_green))
    assert any("below min_green" in e for e in errors(edit_plan=short_green))
    assert any("never turn green" in e for e in errors(edit_plan=never_green))
    assert any("no facing light" in e for e in errors(no_light))


def test_scenario_writes_controllers_and_light_events(tmp_path):
    path, _, sidecar = _build(_signal_fixture(), tmp_path)
    xosc = tmp_path / "net.xosc"
    ScenarioBuilder.from_files(path).write(ScenarioParams(2, sim_duration=120.0), xosc)
    root = etree.parse(str(xosc)).getroot()
    (ctrl,) = root.findall("RoadNetwork/TrafficSignals/TrafficSignalController")
    plan = sidecar["signal_plans"][0]
    assert len(ctrl.findall("Phase")) == len(plan["phases"])
    assert float(ctrl.get("delay")) == pytest.approx((plan["cycle"] - plan["offset"]) % plan["cycle"])
    actions = root.findall(".//TrafficSignalStateAction")
    assert {a.get("name") for a in actions} == set(plan["lights"])
    times = [float(c.get("value")) for c in root.findall(".//Event//SimulationTimeCondition")]
    assert min(times) == 0.0 and max(times) <= 120.0 and len(times) >= 2 * len(plan["phases"])
    off = tmp_path / "off.xosc"
    ScenarioBuilder.from_files(path).write(ScenarioParams(2, lights=False), off)
    off_root = etree.parse(str(off)).getroot()
    assert not off_root.findall(".//TrafficSignalStateAction") and not off_root.findall(".//TrafficSignalController")


def _esmini_available() -> bool:
    try:
        from roadgen.sim.esmini import esmini_binary

        esmini_binary("esmini")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _esmini_available(), reason="esmini not found")
def test_esmini_switches_the_lights(tmp_path):
    from roadgen.sim.esmini import esmini_binary

    path, _, sidecar = _build(_signal_fixture(), tmp_path)
    xosc = tmp_path / "net.xosc"
    ScenarioBuilder.from_files(path).write(ScenarioParams(0, sim_duration=40.0), xosc)
    log = tmp_path / "esmini.log"
    subprocess.run([str(esmini_binary("esmini")), "--osc", str(xosc), "--headless", "--fixed_timestep", "0.1",
                    "--logfile_path", str(log)], capture_output=True, check=True, timeout=120)
    text = log.read_text()
    jid = sidecar["signal_plans"][0]["junction"]
    fired = re.findall(rf"\[(\d+\.\d+)\] \[info\] j{jid}_(\d+) standbyState -> startTransition -> runningState", text)
    plan = sidecar["signal_plans"][0]
    from roadgen.opendrive.lights import SignalPlan

    expected = state_changes(SignalPlan(jid, plan["cycle"], plan["offset"], (), tuple(plan["phases"])), 40.0)
    assert len(fired) == len(expected) >= 3  # the initial states and at least two changes
    for (at, k), (want, _) in zip(sorted(fired, key=lambda f: int(f[1])), expected):
        assert float(at) == pytest.approx(want, abs=0.11)
    assert "error" not in text.lower()


def test_hd_off_has_no_lights(tmp_path):
    _, root, sidecar = _build(_signal_fixture(), tmp_path, HdMapParams(enabled=False))
    assert not root.findall("controller") and "signal_plans" not in sidecar


@pytest.mark.parametrize("kw", [{"min_green": 0.0}, {"cycle": (90.0, 60.0)}, {"overhead_min_lanes": 0},
                                {"all_red": -1.0}])
def test_light_params_are_checked(kw):
    with pytest.raises(ValueError):
        TrafficLightParams(**kw)


def test_ids_stay_the_same_when_a_process_builds_twice(tmp_path):
    # scenariogeneration remembers used signal and object ids for the whole process
    first = _build(_signal_fixture(), tmp_path / "a")
    second = _build(_signal_fixture(), tmp_path / "b")
    for root in (first[1], second[1]):
        assert [s.get("id") for s in root.iter("signal")] == ["1", "2", "3", "4"]
    assert ([o.get("id") for o in first[1].iter("object")] == [o.get("id") for o in second[1].iter("object")])
    assert check_hdmap(second[0]) == []


def test_clashing_opposite_arms_get_their_own_phases():
    approaches = _approaches([0, 90, 180, 270])
    clash = {frozenset(((1, "end"), (3, "end")))}  # e.g. two crossing left turns at a skewed junction
    groups = [{k for k, _ in g} for g in phase_groups(approaches, clash)]
    assert {1, 3} not in groups and {2, 4} in groups and len(groups) == 3


def _avenue_cross(arm: float = 200.0):
    from roadgen.graph.model import Edge, Node, RoadGraph

    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (-arm, 0.0), 3: (arm, 0.0), 4: (0.0, arm), 5: (0.0, -arm)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((2, 1), (1, 3), (1, 4), (5, 1)), 1):
        g.add_edge(Edge(eid, a, b, "avenue", 2, 2, speed_limit=13.9))
    return g


def test_pockets_get_their_own_lights_and_protected_left_phases(tmp_path):
    path, _, sidecar = _build(_avenue_cross(), tmp_path)
    assert check_hdmap(path) == []
    (plan,) = sidecar["signal_plans"]
    pockets = {(u["road"], u["contact"]): u["pockets"] for u in sidecar["lane_use"]}
    assert all(len(p) == 1 for p in pockets.values()) and len(pockets) == 4
    left = [s for s, lanes in plan["light_lanes"].items()
            if lanes == pockets[tuple(plan["lights"][s])]]
    assert len(left) == 4
    turns = [(p["turn"], p["kind"]) for p in plan["phases"]]
    assert turns.count(("left", "green")) == 2 and turns.count(("through", "green")) == 2
    for p in plan["phases"]:  # a pocket light and a through light are never green together
        green = {s for s, st in p["states"].items() if st == GREEN}
        assert not (green & set(left)) or green <= set(left)
