"""Style maps (stage C3): district classes, grid angles, blending in lsystem's field goal."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cli import main
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams, _MapView
from roadgen.generators.patterns import PATTERNS
from roadgen.graph.io import load_json
from roadgen.maps.base import make_map
from roadgen.maps.model import (
    MAP_KINDS,
    STYLE_CLASSES,
    STYLE_CONTOUR,
    STYLE_GRID,
    STYLE_RADIAL,
    STYLE_SUBURB,
    GridSpec,
    MapLayer,
    MapSet,
    load_npz,
)
from roadgen.metrics.network import network_metrics
from tests.lsystem_identity import fingerprint

GRID = GridSpec((0.0, 0.0), (3000.0, 2400.0), 20.0)
X, Y = GRID.cell_centers()


def zones(upstream: MapSet | None = None, seed: int = 0, preset: str | None = None, **kw) -> MapLayer:
    return make_map("style", "zones", GRID, seed, preset=preset, overrides=kw, upstream=upstream)


def population(values, centers=None) -> MapLayer:
    meta = {"centers": centers} if centers else {}
    return MapLayer("population", GRID, np.broadcast_to(values, GRID.shape).astype(np.float32), meta)


def test_classes_follow_the_patterns():
    assert list(STYLE_CLASSES.values()) == list(PATTERNS)
    assert MAP_KINDS[-1] == "style"  # built last, on all the others


def test_zones_are_deterministic_and_have_sites():
    a, b = zones(seed=3), zones(seed=3)
    assert np.array_equal(a.data, b.data) and a.metadata["sites"] == b.metadata["sites"]
    assert not np.array_equal(a.data, zones(seed=4).data) or a.metadata["sites"] != zones(seed=4).metadata["sites"]
    assert len(a.metadata["sites"]) == round(3000 / 800) * round(2400 / 800)


def test_shares_without_population():
    assert np.all(zones(shares=(1, 0, 0, 0, 0)).data == STYLE_GRID)
    assert np.all(zones(shares=(0, 0, 0, 0, 1)).data == STYLE_SUBURB)
    mixed = zones(shares=(1, 0, 1, 0, 1), zone_size=400.0)
    assert len(np.unique(mixed.data)) >= 2


def test_uniform_dense_population_is_one_grid():
    style = zones(MapSet(population=population(20000.0)), radial_radius=0.0)
    assert np.all(style.data == STYLE_GRID)


def test_density_bands_and_radial_centre():
    centre = (1500.0, 1200.0)
    density = 15000.0 * np.exp(-np.hypot(X - centre[0], Y - centre[1]) / 700.0)
    style = zones(MapSet(population=population(density, [[*centre, 1.0]])), zone_size=500.0)
    assert style.sample(*centre) == STYLE_RADIAL
    assert style.metadata["radial_centers"] == [list(centre)]
    assert style.sample(50.0, 50.0) == STYLE_SUBURB  # far corner, sparse
    assert {"grid", "suburb", "radial"} <= {STYLE_CLASSES[int(k)] for k in np.unique(style.data)}


def test_steep_districts_follow_contours():
    elevation = MapLayer("elevation", GRID, (0.3 * X).astype(np.float32))  # 30 % grade
    style = zones(MapSet(elevation=elevation), shares=(1, 0, 0, 0, 0))
    assert np.all(style.data == STYLE_CONTOUR)


def test_district_angles_spread():
    planned = zones(preset="planned")
    assert len({a for *_, a in planned.metadata["sites"]}) == 1
    old = zones(angle_spread_deg=35.0)
    assert len({a for *_, a in old.metadata["sites"]}) > 1


def test_image_style(tmp_path):
    img = np.zeros((10, 20, 3))
    img[:, :10] = (0.30, 0.47, 0.66)  # grid on the west
    img[:, 10:] = (0.93, 0.79, 0.23)  # suburb on the east
    path = tmp_path / "style.npy"
    np.save(path, img)
    style = make_map("style", "image", GRID, 0, overrides={"path": str(path)})
    assert style.sample(500.0, 1000.0) == STYLE_GRID and style.sample(2500.0, 1000.0) == STYLE_SUBURB


# --- lsystem ------------------------------------------------------------------------------


def field_params(**kw) -> LSystemParams:
    return LSystemParams(global_goal="field", extent=(3000.0, 2400.0), block_size=(180.0, 100.0),
                         snap_radius=20.0, min_segment_length=40.0, min_angle_deg=30.0, **kw)


def test_blend_weights_sum_to_one_and_blur_borders():
    data = np.where(X < 1500, STYLE_GRID, STYLE_SUBURB).astype(np.uint8)
    view = _MapView(MapSet(style=MapLayer("style", GRID, data)), field_params(style_blend_width=400.0))
    w = view.style_weights()
    for x in (100.0, 1400.0, 1500.0, 1600.0, 2900.0):
        assert float(np.sum(w((x, 1200.0)))) == pytest.approx(1.0)
    assert w((100.0, 1200.0))[STYLE_GRID] == pytest.approx(1.0)
    assert 0.3 < w((1500.0, 1200.0))[STYLE_GRID] < 0.7  # blended at the border
    assert w((2900.0, 1200.0))[STYLE_SUBURB] == pytest.approx(1.0)


def test_grid_goal_ignores_the_style_map():
    style = zones(shares=(0, 0, 0, 0, 1))
    p = LSystemParams.preset("grid_organic")
    plain = LSystemGenerator().generate(p, 2)
    with_style = LSystemGenerator().generate(p, 2, MapSet(style=style))
    assert fingerprint(plain) == fingerprint(with_style)
    assert LSystemGenerator().map_kinds(p) == ("terrain", "elevation", "population")
    assert "style" in LSystemGenerator().map_kinds(field_params())


def test_field_goal_follows_the_style_map():
    # Suburb on the east half: shorter blocks, so more nodes per km there.
    data = np.where(X < 1500, STYLE_GRID, STYLE_SUBURB).astype(np.uint8)
    g = LSystemGenerator().generate(field_params(), 1, MapSet(style=MapLayer("style", GRID, data)))
    west = sum(1 for n in g.nodes.values() if n.x < 1400)
    east = sum(1 for n in g.nodes.values() if n.x > 1600)
    assert east > 1.5 * west


def test_district_angles_turn_the_grid():
    style = zones(shares=(1, 0, 0, 0, 0), angle_spread_deg=0.0)
    angle = style.metadata["sites"][0][3]
    g = LSystemGenerator().generate(field_params(), 1, MapSet(style=style))
    turns = {round(math.degrees(g.edge_heading(e)) % 90.0, 1) for e in g.edges}
    expected = round((90.0 + angle) % 90.0, 1)
    assert turns == {expected}


def test_cli_pipeline_builds_style_only_for_the_field_goal(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    small = ["--set", "extent=[1200, 900]", "--allow-traps", "--maps"]
    assert main(["pipeline", "lsystem", "--preset", "mixed_city", *small, "--seed", "2",
                 "-o", str(tmp_path / "mixed")]) == 0
    graph = load_json(tmp_path / "mixed.json")
    assert set(graph.metadata["maps"]) == {"terrain", "elevation", "population", "style"}
    assert load_npz(tmp_path / "mixed_style.npz").kind == "style"
    assert (tmp_path / "mixed_style.png").is_file()
    assert main(["pipeline", "lsystem", "--preset", "grid_organic", *small, "-o", str(tmp_path / "g")]) == 0
    assert not (tmp_path / "g_style.npz").exists()


def test_cli_map_style(tmp_path):
    assert main(["map", "style", "zones", "--size", "1600", "1200", "--seed", "5",
                 "--set", "angle_spread_deg=10", "-o", str(tmp_path / "s")]) == 0
    assert load_npz(tmp_path / "s.npz").metadata["params"]["angle_spread_deg"] == 10


def test_mixed_city_is_more_varied_than_a_grid(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert main(["pipeline", "lsystem", "--preset", "mixed_city", "--maps", "--seed", "3",
                 "--allow-traps", "-o", str(tmp_path / "m")]) == 0
    mixed = network_metrics(load_json(tmp_path / "m.json"))
    grid = network_metrics(LSystemGenerator().generate(LSystemParams.preset("grid"), 0))
    assert mixed["orientation_entropy"] > grid["orientation_entropy"] + 1.0
