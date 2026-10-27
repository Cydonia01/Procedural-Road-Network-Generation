from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np
import pytest
from matplotlib.image import imsave

from cli import main
from roadgen.config import DEFAULTS, section
from roadgen.graph.io import load_json
from roadgen.graph.model import Node, RoadGraph
from roadgen.maps import base
from roadgen.maps.base import (
    MapGenerator,
    get_map_generator,
    make_map,
    map_generator_names,
    register_map,
)
from roadgen.maps.elevation import (
    FbmElevationParams,
    ImageElevationParams,
    thermal_erosion,
)
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
from roadgen.maps.noise import (
    distance_to,
    fbm,
    majority_filter,
    perlin,
    permutation,
    slope_deg,
)
from roadgen.maps.plot import plot_map
from roadgen.maps.population import CentersPopulationParams, ImagePopulationParams
from roadgen.maps.terrain import ImageTerrainParams, NoiseTerrainParams
from tests.grids import ON_MAPS, grid_cli

GRID = GridSpec((0.0, 0.0), (2000.0, 1500.0), 20.0)
BUILTIN = [("terrain", "noise"), ("elevation", "fbm"), ("population", "centers")]


def gen(kind: str, name: str, grid: GridSpec = GRID, seed: int = 0,
        upstream: MapSet | None = None, **values) -> MapLayer:
    return make_map(kind, name, grid, seed, overrides=values,
                    defaults=section(DEFAULTS, "maps", kind, name), upstream=upstream)


# --- grid and layer model ---------------------------------------------------------------


def test_grid_shape_bounds_and_centers():
    g = GridSpec((100.0, -50.0), (95.0, 40.0), 10.0)
    assert g.shape == (4, 10)  # size rounded up to whole cells
    assert g.bounds == (100.0, 200.0, -50.0, -10.0)
    x, y = g.cell_centers()
    assert x.shape == y.shape == g.shape
    assert (x[0, 0], y[0, 0]) == (105.0, -45.0)
    assert y[-1, 0] > y[0, 0]  # row 0 is the south edge


def test_grid_rejects_bad_sizes():
    with pytest.raises(ValueError):
        GridSpec((0, 0), (100, 100), 0.0)
    with pytest.raises(ValueError, match="cell_size"):
        GridSpec((0, 0), (1e6, 1e6), 1.0)


def test_grid_around_graph():
    graph = RoadGraph()
    graph.add_node(Node(1, 13.0, 7.0))
    graph.add_node(Node(2, 487.0, 233.0))
    g = GridSpec.around(graph, margin=50.0, cell_size=10.0)
    x0, x1, y0, y1 = g.bounds
    assert (x0, y0) == (-40.0, -50.0)
    assert x1 >= 537.0 and y1 >= 283.0
    with pytest.raises(ValueError):
        GridSpec.around(RoadGraph(), 10.0, 10.0)


def test_sample_bilinear_and_nearest():
    g = GridSpec((0.0, 0.0), (30.0, 20.0), 10.0)
    data = np.array([[0.0, 10.0, 20.0], [30.0, 40.0, 50.0]])
    layer = MapLayer("elevation", g, data)
    assert layer.sample(15.0, 5.0) == pytest.approx(10.0)  # a cell center
    assert layer.sample(10.0, 10.0) == pytest.approx(20.0)  # between four centers
    assert layer.sample(-100.0, 100.0) == pytest.approx(30.0)  # clamped to the edge
    terrain = MapLayer("terrain", g, np.array([[0, 1, 2], [2, 1, 0]], dtype=np.uint8))
    assert list(terrain.sample([1.0, 29.0, 16.0], [1.0, 19.0, 4.0])) == [0, 0, 1]


def test_layer_rejects_bad_shape_and_kind():
    with pytest.raises(ValueError, match="shape"):
        MapLayer("elevation", GRID, np.zeros((3, 3)))
    with pytest.raises(ValueError, match="kind"):
        MapLayer("rainfall", GRID, np.zeros(GRID.shape))  # type: ignore[arg-type]


def test_npz_roundtrip(tmp_path):
    layer = gen("terrain", "noise", seed=3)
    save_npz(layer, tmp_path / "t.npz")
    back = load_npz(tmp_path / "t.npz")
    assert back.kind == "terrain" and back.grid == layer.grid
    assert np.array_equal(back.data, layer.data) and back.data.dtype == np.uint8
    assert back.metadata["params"]["water_fraction"] == layer.metadata["params"]["water_fraction"]


# --- noise ----------------------------------------------------------------------------------


def test_perlin_range_and_lattice_zeros():
    rng = np.random.default_rng(0)
    perm = permutation(rng)
    x, y = np.meshgrid(np.linspace(0, 20, 300), np.linspace(0, 20, 300))
    n = perlin(x, y, perm)
    assert -1.0 <= n.min() < -0.3 and 0.3 < n.max() <= 1.0
    ix, iy = np.meshgrid(np.arange(5.0), np.arange(5.0))
    assert np.allclose(perlin(ix, iy, perm), 0.0)  # gradient noise is 0 on the lattice


def test_fbm_is_deterministic_and_seed_dependent():
    x, y = np.meshgrid(np.linspace(0, 5, 50), np.linspace(0, 5, 50))
    a = fbm(x, y, np.random.default_rng(1))
    assert np.array_equal(a, fbm(x, y, np.random.default_rng(1)))
    assert not np.allclose(a, fbm(x, y, np.random.default_rng(2)))


def test_majority_filter_removes_speckles():
    mask = np.zeros((9, 9), dtype=bool)
    mask[4, 4] = True  # a lone cell
    mask[:, :3] = True  # a solid band
    out = majority_filter(mask, 1)
    assert not out[4, 4] and out[:, :2].all()


# --- every built-in generator --------------------------------------------------------------


@pytest.mark.parametrize("kind, name", BUILTIN)
def test_deterministic_and_seeded(kind, name):
    a, b, c = gen(kind, name, seed=5), gen(kind, name, seed=5), gen(kind, name, seed=6)
    assert a.kind == kind and a.data.shape == GRID.shape
    assert np.array_equal(a.data, b.data)
    assert not np.array_equal(a.data, c.data)
    assert a.metadata["generator"] == name and a.metadata["seed"] == 5


def test_kinds_draw_unrelated_randomness():
    seqs = {get_map_generator(k, n).seed_sequence(0).generate_state(4).tobytes() for k, n in BUILTIN}
    assert len(seqs) == len(BUILTIN)


@pytest.mark.parametrize("kind, name, params_type", [
    ("terrain", "noise", NoiseTerrainParams),
    ("terrain", "image", ImageTerrainParams),
    ("elevation", "fbm", FbmElevationParams),
    ("elevation", "image", ImageElevationParams),
    ("population", "centers", CentersPopulationParams),
    ("population", "image", ImagePopulationParams),
])
def test_dataclass_defaults_come_from_defaults_yaml(kind, name, params_type):
    assert get_map_generator(kind, name).params_type is params_type
    defaults = section(DEFAULTS, "maps", kind, name)
    for f in dataclasses.fields(params_type):
        if f.default is not dataclasses.MISSING:
            assert f.default == defaults[f.name], f.name
    assert set(defaults) <= {f.name for f in dataclasses.fields(params_type)}


@pytest.mark.parametrize("kind, name", BUILTIN)
def test_presets_build(kind, name):
    params_type = get_map_generator(kind, name).params_type
    for preset in params_type.PRESETS:
        layer = make_map(kind, name, GRID, 0, preset=preset,
                         defaults=section(DEFAULTS, "maps", kind, name))
        assert np.isfinite(layer.data.astype(float)).all(), preset


def test_shape_does_not_depend_on_cell_size():
    # Noise is sampled in world coordinates: a finer grid shows the same hills.
    fine = gen("elevation", "fbm", GridSpec((0, 0), (2000, 2000), 10.0), erosion_iterations=0)
    coarse = gen("elevation", "fbm", GridSpec((0, 0), (2000, 2000), 20.0), erosion_iterations=0)
    x, y = coarse.grid.cell_centers()
    diff = np.abs(fine.sample(x, y) - coarse.data)
    assert diff.mean() < 0.03 * (coarse.data.max() - coarse.data.min())


# --- terrain ------------------------------------------------------------------------------------


def test_water_fraction_is_met_without_rivers_or_smoothing():
    t = gen("terrain", "noise", rivers=0, smoothing=0, water_fraction=0.25)
    assert t.fractions()["water"] == pytest.approx(0.25, abs=0.005)
    assert set(np.unique(t.data)) <= {WATER, LAND, VEGETATION}


def test_vegetation_is_a_share_of_the_land():
    t = gen("terrain", "noise", vegetation_fraction=0.3)
    f = t.fractions()
    assert f["vegetation"] / (f["vegetation"] + f["land"]) == pytest.approx(0.3, abs=0.01)


def test_inland_has_no_water():
    t = make_map("terrain", "noise", GRID, 0, preset="inland",
                 defaults=section(DEFAULTS, "maps", "terrain", "noise"))
    assert t.fractions()["water"] == 0.0


def test_rivers_add_water_and_leave_lakes_alone():
    lakes = gen("terrain", "noise", rivers=0)
    both = gen("terrain", "noise", rivers=2)
    assert both.fractions()["water"] > lakes.fractions()["water"]
    assert np.all(both.data[lakes.data == WATER] == WATER)


def test_river_crosses_the_map():
    t = gen("terrain", "noise", water_fraction=0.0, rivers=1, river_width=60.0)
    water = t.data == WATER
    # A river through the middle half enters and leaves through two different edges.
    edges = [water[0].any(), water[-1].any(), water[:, 0].any(), water[:, -1].any()]
    assert sum(edges) >= 2


def test_coast_puts_the_sea_on_one_side():
    t = gen("terrain", "noise", rivers=0, water_fraction=0.3, coast_strength=0.9,
            coast_angle_deg=0.0)  # sea towards +x
    x, _ = t.grid.cell_centers()
    assert x[t.data == WATER].mean() > x.mean() + 300.0


# --- elevation -----------------------------------------------------------------------------------


def test_elevation_spans_the_range_without_erosion():
    e = gen("elevation", "fbm", min_elevation=10.0, max_elevation=90.0, erosion_iterations=0)
    assert e.data.min() == pytest.approx(10.0) and e.data.max() == pytest.approx(90.0)
    assert e.data.dtype == np.float32


def test_thermal_erosion_keeps_mass_and_flattens_steep_slopes():
    z = np.zeros((20, 20))
    z[10, 10] = 100.0  # a spike
    out = thermal_erosion(z, cell_size=10.0, iterations=50, talus_deg=30.0, rate=0.5)
    assert out.sum() == pytest.approx(z.sum())
    assert out.max() < 50.0 and out.min() >= 0.0


def test_ridge_and_erosion_change_the_terrain():
    plain = gen("elevation", "fbm", erosion_iterations=0, max_elevation=400.0)
    ridged = gen("elevation", "fbm", erosion_iterations=0, max_elevation=400.0, ridge=1.0)
    eroded = gen("elevation", "fbm", erosion_iterations=40, max_elevation=400.0)
    assert not np.allclose(plain.data, ridged.data)
    slope = lambda d: np.abs(np.diff(d.astype(float), axis=1)).max()
    assert slope(eroded.data) < slope(plain.data)


# --- population --------------------------------------------------------------------------------


def test_population_peaks_at_the_main_center():
    p = gen("population", "centers", n_centers=1, noise_amount=0.0, base_density=100.0,
            peak_density=10000.0)
    (cx, cy, w), = p.metadata["centers"]
    assert w == 1.0
    assert p.sample(cx, cy) == pytest.approx(10100.0, rel=0.02)
    assert p.data.min() >= 100.0
    assert p.sample(cx + 500.0, cy) < p.sample(cx + 100.0, cy)


def test_centers_respect_distance_and_margin():
    p = gen("population", "centers", n_centers=4, min_center_distance=400.0, center_margin=0.2)
    centers = np.array(p.metadata["centers"])[:, :2]
    assert len(centers) == 4
    d = np.hypot(*(centers[:, None, :] - centers[None, :, :]).transpose(2, 0, 1))
    assert d[~np.eye(4, dtype=bool)].min() >= 400.0
    assert centers[:, 0].min() >= 400.0 and centers[:, 0].max() <= 1600.0


def test_no_centers_is_the_base_density():
    p = gen("population", "centers", n_centers=0, base_density=42.0)
    assert np.allclose(p.data, 42.0)


# --- the pipeline: terrain -> elevation -> population -------------------------------------------


def chain(seed: int = 0, **terrain) -> MapSet:
    t = gen("terrain", "noise", seed=seed, **terrain)
    e = gen("elevation", "fbm", seed=seed, upstream=MapSet(terrain=t))
    p = gen("population", "centers", seed=seed, upstream=MapSet(terrain=t, elevation=e))
    return MapSet(t, e, p)


def test_distance_to_and_slope():
    mask = np.zeros((20, 20), dtype=bool)
    mask[10, 10] = True
    d = distance_to(mask, 10.0, 60.0)
    assert d[10, 10] == 0.0 and d[10, 13] == pytest.approx(30.0)
    assert d[13, 14] == pytest.approx(50.0, rel=0.1)  # euclidean 50
    assert d[0, 0] == 60.0  # capped
    assert np.all(distance_to(np.zeros((3, 3), bool), 10.0, 60.0) == 60.0)
    _, x = np.mgrid[0:5, 0:6] * 10.0
    assert np.allclose(slope_deg(0.1 * x, 10.0), np.degrees(np.arctan(0.1)))


def test_on_grid_resamples_other_grids():
    t = gen("terrain", "noise")
    assert t.on_grid(GRID) is t.data
    fine = GridSpec((0.0, 0.0), (2000.0, 1500.0), 10.0)
    assert t.on_grid(fine).shape == fine.shape
    assert np.mean(t.on_grid(fine)[::2, ::2] == t.data) > 0.95


@pytest.mark.parametrize("kind, name", BUILTIN)
def test_empty_upstream_is_the_standalone_map(kind, name):
    assert np.array_equal(gen(kind, name, upstream=MapSet()).data, gen(kind, name).data)


def test_make_map_only_passes_earlier_kinds():
    maps = chain()
    alone = gen("terrain", "noise", upstream=maps)  # terrain is first: reads nothing
    assert np.array_equal(alone.data, maps.terrain.data)
    e = gen("elevation", "fbm", upstream=MapSet(terrain=maps.terrain, population=maps.population))
    assert np.array_equal(e.data, maps.elevation.data)


def test_elevation_puts_water_in_the_valleys():
    t, e, _ = chain(rivers=1, water_fraction=0.15).layers().values()
    p = FbmElevationParams()
    water = t.data == WATER
    assert np.all(e.data[water] == p.min_elevation)
    assert e.data[~water].min() >= p.min_elevation + p.shore_height - 1e-4
    near = distance_to(water, GRID.cell_size, 2 * GRID.cell_size) <= GRID.cell_size * 1.5
    assert e.data[near & ~water].mean() < e.data[~water].mean()
    assert e.metadata["upstream"] == ["terrain"]
    # Without water the terrain changes nothing.
    inland = gen("terrain", "noise", water_fraction=0.0, rivers=0)
    assert np.array_equal(gen("elevation", "fbm", upstream=MapSet(terrain=inland)).data,
                          gen("elevation", "fbm").data)


def test_population_lives_on_habitable_land():
    t, _, pop = chain(water_fraction=0.3, coast_strength=0.8).layers().values()
    assert np.all(pop.data[t.data == WATER] == 0.0)
    assert pop.data[t.data == LAND].mean() > pop.data[t.data == VEGETATION].mean()
    for cx, cy, _ in pop.metadata["centers"]:
        assert t.sample(cx, cy) == LAND
    assert pop.metadata["upstream"] == ["terrain", "elevation"]


def test_population_avoids_steep_slopes():
    x, _ = GRID.cell_centers()
    cliff = MapLayer("elevation", GRID, np.where(x > 1000.0, (x - 1000.0) * 1.0, 0.0).astype(np.float32))
    pop = gen("population", "centers", n_centers=0, upstream=MapSet(elevation=cliff))
    assert np.all(pop.data[x < 980.0] == pytest.approx(50.0))  # flat: base density
    assert np.all(pop.data[x > 1020.0] == 0.0)  # 45 deg > max_slope_deg
    flat = gen("population", "centers", n_centers=0, max_slope_deg=0.0,
               upstream=MapSet(elevation=cliff))
    assert np.allclose(flat.data, 50.0)


# --- image generators -----------------------------------------------------------------------------


def test_image_elevation_north_is_the_first_row(tmp_path):
    img = np.tile(np.linspace(1.0, 0.0, 30)[:, None], (1, 40))  # white top (north)
    imsave(tmp_path / "h.png", img, cmap="gray", vmin=0, vmax=1)
    e = make_map("elevation", "image", GRID, 0, overrides={"path": str(tmp_path / "h.png")},
                 defaults={"min_elevation": 0.0, "max_elevation": 100.0})
    assert e.data[-1].mean() > 95.0 and e.data[0].mean() < 5.0  # last row = north


def test_image_population_from_npy(tmp_path):
    np.save(tmp_path / "p.npy", np.full((10, 10), 0.5))
    p = make_map("population", "image", GRID, 0, overrides={"path": str(tmp_path / "p.npy")},
                 defaults=section(DEFAULTS, "maps", "population", "image"))
    assert np.allclose(p.data, 10000.0)


def test_image_terrain_color_and_mask(tmp_path):
    defaults = section(DEFAULTS, "maps", "terrain", "image")
    rgb = np.zeros((10, 30, 3))
    rgb[:, :10] = defaults["water_color"]
    rgb[:, 10:20] = defaults["land_color"]
    rgb[:, 20:] = defaults["vegetation_color"]
    imsave(tmp_path / "t.png", rgb)
    t = make_map("terrain", "image", GridSpec((0, 0), (300, 100), 10), 0,
                 overrides={"path": str(tmp_path / "t.png")}, defaults=defaults)
    assert list(t.data[5, [2, 15, 28]]) == [WATER, LAND, VEGETATION]

    mask = np.ones((10, 10))
    mask[:, :5] = 0.0  # dark = water
    imsave(tmp_path / "m.png", mask, cmap="gray", vmin=0, vmax=1)
    t = make_map("terrain", "image", GridSpec((0, 0), (100, 100), 10), 0,
                 overrides={"path": str(tmp_path / "m.png")}, defaults=defaults)
    assert (t.data[:, :5] == WATER).all() and (t.data[:, 5:] == LAND).all()


def test_image_missing_file():
    with pytest.raises(FileNotFoundError):
        make_map("elevation", "image", GRID, 0, overrides={"path": "nope.png"})


# --- registry ----------------------------------------------------------------------------------------


def test_registry_lists_builtins_and_rejects_unknown():
    for kind, name in BUILTIN:
        assert {name, "image"} <= set(map_generator_names(kind))
    with pytest.raises(ValueError, match="unknown terrain map generator"):
        get_map_generator("terrain", "voronoi")
    with pytest.raises(ValueError, match="unknown map kind"):
        map_generator_names("rainfall")


def test_register_map_decorator(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {k: dict(v) for k, v in base._REGISTRY.items()})

    @dataclass(frozen=True)
    class FlatParams:
        height: float = 7.0

    @register_map("elevation", "flat")
    class FlatGenerator(MapGenerator[FlatParams]):
        params_type = FlatParams

        def generate(self, params, grid, seed, upstream=None):
            return MapLayer("elevation", grid, np.full(grid.shape, params.height, np.float32))

    assert "flat" in map_generator_names("elevation")
    assert np.all(make_map("elevation", "flat", GRID, 0).data == 7.0)
    with pytest.raises(ValueError, match="already registered"):
        register_map("elevation", "flat")(type("Other", (FlatGenerator,), {}))


# --- plotting and CLI -----------------------------------------------------------------------------


@pytest.mark.parametrize("kind, name", BUILTIN)
def test_plot_map_with_graph(tmp_path, kind, name):
    graph = RoadGraph()
    graph.add_node(Node(1, 100.0, 100.0))
    out = tmp_path / f"{kind}.png"
    plot_map(gen(kind, name), out, graph)
    assert out.stat().st_size > 0


def test_cli_map_and_plot_map(tmp_path):
    prefix = tmp_path / "terrain"
    argv = ["map", "terrain", "noise", "--preset", "coastal", "--seed", "4",
            "--size", "800", "600", "--cell-size", "20", "--set", "rivers=1", "-o", str(prefix)]
    assert main(argv) == 0
    layer = load_npz(prefix.with_suffix(".npz"))
    assert layer.grid.shape == (30, 40) and layer.metadata["params"]["rivers"] == 1
    assert layer.metadata["params"]["coast_strength"] == 0.8  # from the preset
    assert prefix.with_suffix(".png").is_file()
    assert main(["plot-map", str(prefix.with_suffix(".npz")), str(tmp_path / "again.png")]) == 0
    assert main(["maps"]) == 0


def test_cli_image_map_needs_a_path():
    with pytest.raises(SystemExit, match="path"):
        main(["map", "elevation", "image"])


def test_cli_pipeline_maps(tmp_path):
    prefix = tmp_path / "net"
    argv = ["pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, "--maps",
            "--population", "centers", "--map-set", "terrain.rivers=0", "-o", str(prefix)]
    assert main(argv) == 0
    graph = load_json(prefix.with_suffix(".json"))
    assert set(graph.metadata["maps"]) == {"terrain", "elevation", "population"}
    for kind in ("terrain", "elevation", "population"):
        layer = load_npz(tmp_path / f"net_{kind}.npz")
        x0, x1, y0, y1 = layer.grid.bounds
        assert all(x0 < n.x < x1 and y0 < n.y < y1 for n in graph.nodes.values())
        assert (tmp_path / f"net_{kind}.png").is_file()
    assert load_npz(tmp_path / "net_terrain.npz").metadata["params"]["rivers"] == 0


def test_cli_pipeline_without_maps_writes_none(tmp_path):
    prefix = tmp_path / "net"
    assert main(["pipeline", "lsystem", *grid_cli(2, 2), *ON_MAPS,
                 "--elevation", "fbm", "-o", str(prefix)]) == 0
    assert (tmp_path / "net_elevation.npz").is_file()
    assert not (tmp_path / "net_terrain.npz").exists()


def test_cli_map_on_earlier_maps(tmp_path):
    size = ["--size", "800", "600", "--cell-size", "20"]
    assert main(["map", "terrain", "noise", "--preset", "lakes", *size, "-o", str(tmp_path / "t")]) == 0
    assert main(["map", "elevation", "fbm", *size, "--on", f"terrain={tmp_path / 't.npz'}",
                 "-o", str(tmp_path / "e")]) == 0
    t, e = load_npz(tmp_path / "t.npz"), load_npz(tmp_path / "e.npz")
    assert np.all(e.data[t.data == WATER] == 0.0) and e.metadata["upstream"] == ["terrain"]
    with pytest.raises(SystemExit, match="--on"):
        main(["map", "elevation", "fbm", "--on", f"population={tmp_path / 't.npz'}"])
    with pytest.raises(SystemExit, match="holds a terrain map"):
        main(["map", "population", "centers", "--on", f"elevation={tmp_path / 't.npz'}"])


def test_cli_pipeline_maps_are_consistent(tmp_path):
    prefix = tmp_path / "net"
    assert main(["pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, "--maps",
                 "--map-set", "terrain.water_fraction=0.3", "-o", str(prefix)]) == 0
    t, e, p = (load_npz(tmp_path / f"net_{k}.npz") for k in ("terrain", "elevation", "population"))
    water = t.data == WATER
    assert water.any() and np.all(e.data[water] == 0.0) and np.all(p.data[water] == 0.0)


def test_cli_map_set_needs_a_kind(tmp_path):
    with pytest.raises(SystemExit, match="map-set"):
        main(["pipeline", "lsystem", *grid_cli(2, 2), *ON_MAPS, "--maps",
              "--map-set", "rivers=2", "-o", str(tmp_path / "n")])
