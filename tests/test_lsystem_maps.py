"""lsystem growth on input maps: highways, bridges, pruning, density and grade."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cli import main
from roadgen.config import DEFAULTS, section
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.io import load_json
from roadgen.graph.model import RoadGraph
from roadgen.maps.base import make_map
from roadgen.maps.model import (
    LAND,
    VEGETATION,
    WATER,
    GridSpec,
    MapLayer,
    MapSet,
    load_npz,
    save_npz,
)
from roadgen.opendrive.builder import OpenDriveBuilder
from roadgen.validate.continuity import check_continuity
from roadgen.validate.topology import check_topology

GRID = GridSpec((-50.0, -50.0), (1100.0, 700.0), 10.0)  # covers the default 1000 x 600 area
X, Y = GRID.cell_centers()
BASE = {"extent": (1000.0, 600.0), "block_size": (100.0, 60.0), "start": (0.5, 0.5),
        "highway_length": 100.0, "highway_ray_length": 400.0}


def terrain(mask_water=None, mask_veg=None) -> MapLayer:
    data = np.full(GRID.shape, LAND, dtype=np.uint8)
    if mask_veg is not None:
        data[mask_veg] = VEGETATION
    if mask_water is not None:
        data[mask_water] = WATER
    return MapLayer("terrain", GRID, data)


def population(values) -> MapLayer:
    return MapLayer("population", GRID, np.broadcast_to(values, GRID.shape).astype(np.float32))


def elevation(values) -> MapLayer:
    return MapLayer("elevation", GRID, np.broadcast_to(values, GRID.shape).astype(np.float32))


def grow(maps: MapSet | None, seed: int = 0, **kw) -> RoadGraph:
    return LSystemGenerator().generate(LSystemParams(**{**BASE, **kw}), seed, maps)


def edge_samples(g: RoadGraph, eid: int, step: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    e = g.edges[eid]
    a, b = g.nodes[e.from_node], g.nodes[e.to_node]
    u = np.linspace(0.0, 1.0, max(2, int(g.edge_length(eid) / step)))
    return a.x + u * (b.x - a.x), a.y + u * (b.y - a.y)


def classes(g: RoadGraph, *names: str) -> list[int]:
    return [eid for eid, e in g.edges.items() if e.road_class in names]


# --- without maps nothing changes --------------------------------------------------------


def test_empty_mapset_is_plain_growth():
    plain = grow(None)
    assert grow(MapSet()).edges == plain.edges
    assert "bridges" not in plain.metadata


def test_highways_need_a_population_map():
    g = grow(MapSet(terrain=terrain()))
    assert not classes(g, "highway")
    g = grow(MapSet(population=population(5000.0)), highways=False)
    assert not classes(g, "highway")
    assert classes(grow(MapSet(population=population(5000.0))), "highway")


# --- terrain ----------------------------------------------------------------------------------


def test_streets_stay_out_of_water_and_vegetation():
    water = (X > 700.0)  # a sea on the right, far wider than max_bridge_length
    veg = (np.hypot(X - 300.0, Y - 300.0) < 80.0)  # a round park left of the seed
    t = terrain(water, veg)
    g = grow(MapSet(terrain=t))
    assert len(g.edges) > 30 and g.validate() == []
    for eid in classes(g, "street", "avenue"):
        cls = t.sample(*edge_samples(g, eid))
        assert not np.any(cls == WATER), eid
        assert not np.any(cls == VEGETATION), eid
    # Streets were pruned at the shore, so some end close to it.
    assert max(n.x for n in g.nodes.values()) > 650.0


def test_vegetation_allowed_when_not_avoided():
    veg = (np.hypot(X - 500.0, Y - 300.0) < 150.0)  # around the seed
    t = terrain(mask_veg=veg)
    g = grow(MapSet(terrain=t), avoid_vegetation=False)
    assert any(np.any(t.sample(*edge_samples(g, e)) == VEGETATION) for e in g.edges)


def test_seed_on_water_moves_to_land():
    t = terrain(np.hypot(X - 500.0, Y - 300.0) < 60.0)
    g = grow(MapSet(terrain=t))
    assert g.edges
    assert all(t.sample(n.x, n.y) != WATER for n in g.nodes.values())


# --- highways ---------------------------------------------------------------------------------


def peak(cx: float, cy: float, base: float = 2000.0, top: float = 20000.0, b: float = 250.0):
    return base + top * np.exp(-np.hypot(X - cx, Y - cy) / b)


def test_highways_grow_towards_the_population_peak():
    g = grow(MapSet(population=population(peak(900.0, 500.0))), start=(0.15, 0.2))
    highway_nodes = {n for eid in classes(g, "highway")
                     for n in (g.edges[eid].from_node, g.edges[eid].to_node)}
    assert min(math.hypot(g.nodes[n].x - 900.0, g.nodes[n].y - 500.0) for n in highway_nodes) < 120.0
    assert all(e.lanes_forward == 2 and e.speed_limit == pytest.approx(22.2)
               for e in g.edges.values() if e.road_class == "highway")


def test_highways_bridge_narrow_water_only():
    river = np.abs(X - 650.0) < 30.0  # 60 m wide, between the seed and the peak
    t = terrain(river)
    maps = MapSet(terrain=t, population=population(peak(900.0, 300.0)))
    g = grow(maps, start=(0.3, 0.5))
    bridges = set(g.metadata["bridges"])
    assert bridges and bridges <= set(classes(g, "highway"))
    for eid in g.edges:
        wet = np.any(t.sample(*edge_samples(g, eid)) == WATER)
        assert wet == (eid in bridges), eid
    # Wider than max_bridge_length: nothing crosses.
    g = grow(maps, start=(0.3, 0.5), max_bridge_length=40.0)
    assert g.metadata["bridges"] == []
    assert max(n.x for n in g.nodes.values()) < 650.0


def test_highways_stop_below_their_min_density():
    pop = np.where(X < 600.0, 5000.0, 100.0)
    g = grow(MapSet(population=population(pop)), highway_min_density=1000.0,
             street_min_density=0.0)
    snap = LSystemParams().snap_radius  # an end may snap a little past the boundary
    for eid in classes(g, "highway"):
        e = g.edges[eid]
        assert max(g.nodes[e.from_node].x, g.nodes[e.to_node].x) < 600.0 + snap


# --- population and elevation ---------------------------------------------------------------


def test_streets_stop_where_nobody_lives():
    pop = np.where(X >= 400.0, 5000.0, 0.0)
    g = grow(MapSet(population=population(pop)), highways=False, street_min_density=100.0)
    assert len(g.edges) > 20
    assert min(n.x for n in g.nodes.values()) >= 400.0 - 1e-6


def test_roads_respect_max_grade():
    ramp = 0.3 * Y  # 30% uphill along y, flat along x
    elev = elevation(ramp)
    g = grow(MapSet(elevation=elev), max_grade=0.15)
    assert g.edges and not classes(g, "avenue")  # avenues run along y
    for eid, e in g.edges.items():
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        grade = abs(elev.sample(b.x, b.y) - elev.sample(a.x, a.y)) / g.edge_length(eid)
        assert grade <= 0.15 + 1e-6
    assert classes(grow(MapSet(elevation=elev), max_grade=0.0), "avenue")


# --- whole networks -------------------------------------------------------------------------------


def generated_maps(params: LSystemParams, seed: int) -> MapSet:
    """terrain -> elevation -> population, each on the maps before it."""
    grid = GridSpec.from_bounds(LSystemGenerator().map_bounds(params), 200.0, 10.0)
    maps = MapSet()
    for kind, name in (("terrain", "noise"), ("elevation", "fbm"), ("population", "centers")):
        layer = make_map(kind, name, grid, seed, defaults=section(DEFAULTS, "maps", kind, name),
                         upstream=maps)
        maps = MapSet(**{**maps.layers(), kind: layer})
    return maps


@pytest.mark.parametrize("seed", range(2))
def test_city_on_generated_maps_builds_valid_xodr(tmp_path, seed):
    p = LSystemParams.preset("city")
    maps = generated_maps(p, seed)
    g = LSystemGenerator().generate(p, seed, maps)
    assert g.validate() == []
    assert classes(g, "highway") and len(g.edges) > 100
    assert g == LSystemGenerator().generate(p, seed, maps)  # deterministic
    b = OpenDriveBuilder()
    b.build(g)
    xodr = tmp_path / "city.xodr"
    b.write(xodr)
    assert check_topology(xodr) == []
    assert check_continuity(xodr) == []


def test_snapped_streets_do_not_cross_water():
    """A street end snapped onto an existing node must not jump a narrow strip of
    water (found with these maps: drape made a bridge on an avenue)."""
    from roadgen.maps.drape import drape

    grid = GridSpec((0.0, 0.0), (3000.0, 3000.0), 10.0)
    maps = MapSet()
    for kind, name, preset in (("terrain", "noise", "coastal"), ("elevation", "fbm", None),
                               ("population", "centers", None)):
        layer = make_map(kind, name, grid, 0, preset=preset,
                         defaults=section(DEFAULTS, "maps", kind, name), upstream=maps)
        maps = MapSet(**{**maps.layers(), kind: layer})
    p = LSystemParams.preset("city", highways=False)
    g = drape(LSystemGenerator().generate(p, 0, maps), maps.elevation, maps.terrain)
    assert len(g.edges) > 100
    assert [eid for eid, e in g.edges.items() if e.bridges] == []


def test_map_bounds_cover_the_rotated_area():
    p = LSystemParams(extent=(1000.0, 500.0), origin=(100.0, 200.0), rotation_deg=90.0)
    x0, y0, x1, y1 = LSystemGenerator().map_bounds(p)
    assert (x0, y0, x1, y1) == pytest.approx((-400.0, 200.0, 100.0, 1200.0))


def test_new_params_are_validated():
    with pytest.raises(ValueError):
        LSystemParams(highway_max_turn_deg=70.0, highway_max_detour_deg=60.0)
    with pytest.raises(ValueError):
        LSystemParams(prune_factor=1.5)
    with pytest.raises(ValueError):
        LSystemParams(highway_lanes=(0, 0))


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_pipeline_grows_lsystem_on_its_maps(tmp_path):
    prefix = tmp_path / "city"
    assert main(["pipeline", "lsystem", "--preset", "grid_organic", "--allow-traps", "--maps",
                 "--seed", "3", "-o", str(prefix)]) == 0
    graph = load_json(prefix.with_suffix(".json"))
    assert "bridges" in graph.metadata
    assert set(graph.metadata["maps"]) == {"terrain", "elevation", "population"}
    params = LSystemParams.preset("grid_organic")
    for kind in ("terrain", "elevation", "population"):
        layer = load_npz(tmp_path / f"city_{kind}.npz")
        assert layer.grid.covers(LSystemGenerator().map_bounds(params))
    # The same maps from files give the same network.
    out = tmp_path / "again.json"
    files = [f"--map-file={k}={tmp_path / f'city_{k}.npz'}"
             for k in ("terrain", "elevation", "population")]
    assert main(["generate", "lsystem", "--preset", "grid_organic", "--seed", "3", *files,
                 "-o", str(out)]) == 0
    again = load_json(out)
    assert again.edges == graph.edges
    assert again.metadata["maps"]["terrain"]["source"].endswith("city_terrain.npz")


def test_cli_map_file_errors(tmp_path):
    with pytest.raises(SystemExit, match="map-file"):
        main(["generate", "lsystem", "--map-file", "rain=x.npz", "-o", str(tmp_path / "g.json")])
    elev = make_map("elevation", "fbm", GRID, 0, defaults=section(DEFAULTS, "maps", "elevation", "fbm"))
    save_npz(elev, tmp_path / "e.npz")
    with pytest.raises(SystemExit, match="holds a elevation map"):
        main(["generate", "lsystem", "--map-file", f"terrain={tmp_path / 'e.npz'}",
              "-o", str(tmp_path / "g.json")])
