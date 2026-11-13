"""Polish V1: the check after every pipeline stage (roadgen.validate.stages) catches what
its stage could break, and the pipeline reports every stage."""

from __future__ import annotations

import copy
import logging
import re

import numpy as np
import pytest

from cli import main
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.model import LAND, WATER, GridSpec, MapLayer, save_npz
from roadgen.validate import stages
from tests.grids import ON_MAPS, grid_cli

GRID = GridSpec((0.0, 0.0), (100.0, 50.0), 10.0)  # 10 x 5 cells
WET = np.zeros(GRID.shape, bool)
WET[:, :3] = True  # a lake on the west side


def _terrain() -> MapLayer:
    return MapLayer("terrain", GRID, np.where(WET, WATER, LAND).astype(np.uint8))


def _layer(kind: str, data, source: bool = False) -> MapLayer:
    return MapLayer(kind, GRID, np.asarray(data, np.float32), {"source": "f.npz"} if source else {})


def _graph() -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (200.0, 0.0), 4: (100.0, 100.0)}.items():
        g.add_node(Node(nid, x, y))
    for eid, (a, b) in enumerate(((1, 2), (2, 3), (2, 4)), 1):
        g.add_edge(Edge(eid, a, b, "street", 1, 1))
    return g


# --- maps ----------------------------------------------------------------------------------------------


def test_map_checks():
    assert stages.check_map(_terrain()).ok
    bad = MapLayer("terrain", GRID, np.full(GRID.shape, 7, np.uint8))
    assert any("unknown classes [7]" in e for e in stages.check_map(bad).errors)
    assert stages.check_map(MapLayer("terrain", GRID, np.zeros(GRID.shape, np.uint8))).warnings  # all water
    nan = np.zeros(GRID.shape)
    nan[0, 0] = np.nan
    assert any("not finite" in e for e in stages.check_map(_layer("elevation", nan)).errors)


def test_elevation_keeps_its_water_at_the_bottom():
    up = {"terrain": _terrain()}
    good = np.where(WET, 0.0, 2.0)
    assert stages.check_map(_layer("elevation", good), up).ok
    high_water = np.where(WET, 5.0, 2.0)
    r = stages.check_map(_layer("elevation", high_water), up)
    assert r.stage == "map elevation" and any("above the lowest land" in e for e in r.errors)
    # a loaded file is used as it is: a warning only
    loaded = stages.check_map(_layer("elevation", high_water, source=True), up)
    assert loaded.ok and loaded.warnings


def test_population_is_zero_on_water_and_never_negative():
    up = {"terrain": _terrain()}
    assert stages.check_map(_layer("population", np.where(WET, 0.0, 500.0)), up).ok
    assert any("water cells have people" in e for e in stages.check_map(_layer("population", np.full(GRID.shape, 9.0)),
                                                                          up).errors)
    assert any("negative" in e for e in stages.check_map(_layer("population", np.full(GRID.shape, -1.0))).errors)


def test_maps_on_different_grids_are_a_warning():
    other = MapLayer("elevation", GridSpec((0.0, 0.0), (100.0, 50.0), 5.0), np.zeros((10, 20), np.float32))
    assert stages.check_maps({"terrain": _terrain(), "elevation": other}).warnings
    assert not stages.check_maps({"terrain": _terrain(), "elevation": _layer("elevation", np.zeros(GRID.shape))}).warnings


# --- graph stages --------------------------------------------------------------------------------------


def test_generate_check():
    g = _graph()
    assert stages.check_generate(g).ok
    g.add_node(Node(9, 50.0, 50.0))
    r = stages.check_generate(g, {"terrain": _terrain()})
    assert any("nodes without a road: [9]" in e for e in r.errors)
    assert any("outside the maps" in w for w in r.warnings)  # nodes at x = 200 and y = 100


def test_align_may_only_add_radius_caps():
    before = _graph()
    after = copy.deepcopy(before)
    after.nodes[2].radius_cap = 40.0
    assert stages.check_align(before, after).ok
    after.nodes[3].x += 1.0
    assert any("nodes changed position" in e for e in stages.check_align(before, after).errors)
    after = copy.deepcopy(before)
    after.nodes[2].radius_cap = 0.0
    assert any("radius caps" in e for e in stages.check_align(before, after).errors)


def test_drape_check():
    before = _graph()
    after = copy.deepcopy(before)
    after.nodes[3].z = 30.0  # 30 m over the 100 m edge 2: 30 %
    r = stages.check_drape(before, after, max_grade=0.15)
    assert r.ok and any("steeper than 15%" in w for w in r.warnings)
    after.edges[1].bridges = [(10.0, 40.0), (30.0, 60.0)]
    assert any("overlap" in e for e in stages.check_drape(before, after).errors)
    after = copy.deepcopy(before)
    after.nodes[1].z = float("nan")
    assert any("not finite" in e for e in stages.check_drape(before, after).errors)
    after = copy.deepcopy(before)
    after.edges[3].lanes_forward = 2
    assert any("edges changed" in e for e in stages.check_drape(before, after).errors)


def _crossing() -> RoadGraph:
    g = RoadGraph()
    for nid, (x, y) in {1: (-200.0, 0.0), 2: (200.0, 0.0), 3: (0.0, -200.0), 4: (0.0, 200.0)}.items():
        g.add_node(Node(nid, x, y))
    g.add_edge(Edge(1, 1, 2, "street", 1, 1))
    g.add_edge(Edge(2, 3, 4, "street", 1, 1))
    return g


def test_separate_needs_the_clearance_at_every_crossing():
    from roadgen.geometry.grade_separation import separate

    before = _crossing()
    assert stages.check_separate(before, separate(before), 6.0).ok
    flat = copy.deepcopy(before)  # "separated" without raising anything
    r = stages.check_separate(before, flat, 6.0)
    assert r.stage == "separate" and any("apart in height" in e for e in r.errors)


def test_annotate_check():
    before = _graph()
    after = copy.deepcopy(before)
    after.edges[1].cross_section = "urban"
    after.nodes[2].control = "signal"
    assert stages.check_annotate(before, after).ok
    after.edges[2].cross_section = "downtown"
    after.nodes[2].control = "roundabout_please"
    errors = stages.check_annotate(before, after).errors
    assert any("profiles" in e for e in errors) and any("controls" in e for e in errors)


def test_fit_check_finds_edges_too_short_for_their_junctions():
    from roadgen.geometry.cross_section import CrossSectionParams
    from roadgen.geometry.design import DesignParams
    from tests.test_repair import two_junctions

    assert stages.check_fit(two_junctions(60.0), DesignParams(), 2.0, CrossSectionParams()).ok
    r = stages.check_fit(two_junctions(6.0), DesignParams(), 2.0, CrossSectionParams())
    assert any("too short" in e for e in r.errors)


def test_summary():
    reports = [stages.StageReport("map terrain"), stages.StageReport("drape", warnings=["w"]),
               stages.StageReport("fit", errors=["e", "f"])]
    assert stages.summary(reports) == "map terrain ok; drape 1 warnings; fit 2 errors"


# --- CLI -----------------------------------------------------------------------------------------------


def test_pipeline_reports_every_stage(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, "--maps", "--ground",
                 "-o", str(out)]) == 0
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("validation: ")]
    names = [part.rsplit(" ", 1)[0] for part in line[len("validation: "):].split("; ")]
    assert names[:5] == ["map terrain", "map elevation", "map population", "maps", "generate"]
    assert {"align", "drape", "annotate", "fit", "xodr", "scene"} <= set(names)
    assert all(part.endswith(" ok") for part in line[len("validation: "):].split("; ")), line


def test_a_failing_map_stops_the_pipeline_and_names_its_stage(tmp_path):
    bad = tmp_path / "terrain.npz"
    g = GridSpec((-400.0, -400.0), (1400.0, 1400.0), 20.0)
    save_npz(MapLayer("terrain", g, np.full(g.shape, 9, np.uint8)), bad)
    with pytest.raises(SystemExit, match=re.escape("stage map terrain: unknown classes [9]")):
        main(["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, f"--map-file=terrain={bad}",
              "-o", str(tmp_path / "net")])
