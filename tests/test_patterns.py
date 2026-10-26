"""Pattern fields (stage C2): tensor blending, skew, organic knob, and lsystem's field goal."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cli import main
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.generators.patterns import (
    CONTOUR,
    GRID,
    MAJOR,
    MINOR,
    ORGANIC,
    RADIAL,
    SUBURB,
    FieldParams,
    StreetField,
)
from roadgen.graph.io import load_json
from roadgen.metrics.network import network_metrics


def field(weights=(1, 0, 0, 0, 0), seed: int = 0, gradient=None, **kw) -> StreetField:
    return StreetField(FieldParams(weights=tuple(weights), **kw), np.random.default_rng(seed),
                       gradient_at=gradient)


def same_line(a: float, b: float) -> bool:
    """Whether two headings lie on the same line (either way)."""
    return abs(math.sin(a - b)) < 1e-9


# --- field maths ----------------------------------------------------------------------


def test_grid_directions():
    f = field(grid_angle=math.radians(30))
    assert same_line(f.direction((5, 5), MAJOR, 0.0), math.radians(30))
    assert same_line(f.direction((5, 5), MINOR, 0.0), math.radians(120))
    # The closer of the two ways along the line.
    assert f.direction((5, 5), MAJOR, math.radians(200)) == pytest.approx(math.radians(210) - 2 * math.pi)


def test_radial_directions_and_degenerate_centre():
    f = field((0, 1, 0, 0, 0), radial_centers=((0.0, 0.0),))
    assert same_line(f.direction((100, 0), MAJOR, 0.0), 0.0)  # spoke: along the radius
    assert same_line(f.direction((100, 0), MINOR, 0.0), math.pi / 2)  # ring: around the centre
    assert same_line(f.direction((0, 50), MAJOR, 0.0), math.pi / 2)
    assert f.major_angle((0.0, 0.0)) is None
    assert f.direction((0.0, 0.0), MAJOR, 1.234) == 1.234  # keeps its heading


def test_blend_lies_between_patterns():
    f = field((1, 1, 0, 0, 0), grid_angle=math.pi / 2, radial_centers=((0.0, 0.0),))
    p = (100.0, 100.0)  # radial 45 deg, grid 90 deg
    assert same_line(f.major_angle(p), math.radians(67.5))
    w = f.weights(p)
    assert sum(w) == pytest.approx(1.0) and w[GRID] == w[RADIAL] == pytest.approx(0.5)


def test_radial_weight_falls_off():
    f = field((0.2, 1, 0, 0, 0), radial_centers=((0.0, 0.0),), radial_radius=500.0)
    near, far = f.weights((10.0, 0.0)), f.weights((1500.0, 0.0))
    assert near[RADIAL] > 0.8 and far[RADIAL] < 0.01


def test_contour_follows_the_slope_and_grid_on_flat_ground():
    slope = field((0, 0, 0, 1, 0), gradient=lambda p: (0.2, 0.0))  # rises towards +x
    assert same_line(slope.major_angle((0, 0)), math.pi / 2)  # contour runs along y
    flat = field((0, 0, 0, 1, 0), gradient=lambda p: (0.0, 0.0), grid_angle=0.3)
    assert same_line(flat.major_angle((0, 0)), 0.3)
    assert CONTOUR == 3


def test_skew_turns_streets_by_up_to_skew():
    f = field(skew=math.radians(30), skew_scale=300.0)
    turns = []
    for x in range(0, 3000, 97):
        p = (float(x), 40.0)
        turn = (f.direction(p, MINOR, math.pi / 2) - f.direction(p, MAJOR, 0.0)) % math.pi
        turns.append(math.degrees(turn) - 90.0)
    assert min(turns) >= -1e-9 and max(turns) <= 30.0 + 1e-9
    assert max(turns) - min(turns) > 5.0  # it varies in patches


def test_organic_knob_moves_grid_weight_and_snaps():
    assert field(organic=0.0).weights((0, 0))[ORGANIC] == 0.0
    w = field(organic=0.7).weights((0, 0))
    assert w[GRID] == pytest.approx(0.3) and w[ORGANIC] == pytest.approx(0.7)
    # organic 0: always exactly the grid; organic 1: free, some headings off the grid
    plain = field(organic=0.0)
    free = field(organic=1.0, organic_scale=200.0)
    pts = [(float(x), float(y)) for x in range(0, 2000, 150) for y in range(0, 2000, 150)]
    assert all(same_line(plain.direction(p, MAJOR, 0.0), 0.0) for p in pts)
    assert sum(not same_line(free.direction(p, MAJOR, 0.0), 0.0) for p in pts) > len(pts) / 2
    # in between, near-grid headings snap back to the grid
    snappy = field(organic=0.5, organic_scale=200.0, snap=math.radians(30))
    assert sum(same_line(snappy.direction(p, MAJOR, 0.0), 0.0) for p in pts) > \
        sum(same_line(free.direction(p, MAJOR, 0.0), 0.0) for p in pts)


def test_suburb_lengths_and_branching():
    f = field((0, 0, 0, 0, 1), block=(250.0, 80.0), suburb_block=(120.0, 60.0),
              branch_prob=1.0, suburb_branch_prob=0.4)
    assert f.length((0, 0), MAJOR) == 60.0 and f.length((0, 0), MINOR) == 120.0
    assert f.branch_prob((0, 0)) == pytest.approx(0.4)
    assert SUBURB == 4


def test_closest_role():
    f = field(grid_angle=0.0)
    assert f.closest_role((0, 0), 0.1) == MAJOR
    assert f.closest_role((0, 0), math.pi / 2 + 0.2) == MINOR


def test_field_is_deterministic_for_its_random_generator():
    a, b = field(organic=1.0, seed=3), field(organic=1.0, seed=3)
    pts = [(float(x), 17.0) for x in range(0, 1000, 77)]
    assert [a.direction(p, MAJOR, 0) for p in pts] == [b.direction(p, MAJOR, 0) for p in pts]


# --- lsystem field goal ---------------------------------------------------------------


def grow(seed: int = 1, preset=None, **kw):
    base = {"global_goal": "field", "extent": (1500.0, 1200.0), "block_size": (200.0, 120.0),
            "snap_radius": 20.0, "min_segment_length": 40.0, "min_angle_deg": 30.0}
    params = LSystemParams.preset(preset, **kw) if preset else LSystemParams(**{**base, **kw})
    return LSystemGenerator().generate(params, seed)


def test_field_grid_is_a_lattice():
    g = grow()
    m = network_metrics(g)
    assert g.validate() == []
    assert m["skew_deg"]["max"] == pytest.approx(0.0, abs=1e-6)
    assert m["orientation_order"] > 0.95 and m["legs"]["4"] > m["legs"]["3"]


def test_orientation_entropy_rises_with_organic():
    entropy = [np.mean([network_metrics(grow(seed, organic=o))["orientation_entropy"] for seed in (1, 2)])
               for o in (0.0, 0.5, 1.0)]
    assert entropy[0] < entropy[1] < entropy[2]


def test_paris_has_a_star_and_rings():
    p = LSystemParams.preset("paris", extent=(1200.0, 1000.0))
    g = LSystemGenerator().generate(p, 0)
    centre = (p.extent[0] / 2, p.extent[1] / 2)
    star = min(g.nodes.values(), key=lambda n: math.dist((n.x, n.y), centre))
    assert math.dist((star.x, star.y), centre) < 1e-6
    assert g.degree(star.id) == p.radial_spokes
    # Rings: many roads run around the centre, not towards it.
    around = 0
    for e in g.edges.values():
        a, b = g.nodes[e.from_node], g.nodes[e.to_node]
        mx, my = (a.x + b.x) / 2 - centre[0], (a.y + b.y) / 2 - centre[1]
        if math.hypot(mx, my) < 500 and abs(math.cos(math.atan2(b.y - a.y, b.x - a.x) - math.atan2(my, mx))) < 0.3:
            around += 1
    assert around >= 10


def test_skewed_grid_has_skewed_junctions():
    m = network_metrics(LSystemGenerator().generate(LSystemParams.preset("skewed_grid"), 0))
    assert m["skewed_share"] > 0.3


@pytest.mark.parametrize("kw", [
    {"pattern_weights": (1, 0, 0)},
    {"pattern_weights": (0, 0, 0, 0, 0)},
    {"pattern_weights": (1, -1, 0, 0, 0)},
    {"organic": 1.5},
    {"radial_spokes": 1},
    {"global_goal": "field", "curve_mode": "drift"},
])
def test_invalid_field_params(kw):
    with pytest.raises(ValueError):
        LSystemParams(**kw)


def test_field_growth_is_deterministic():
    a, b = grow(4, organic=0.6, skew_deg=10.0), grow(4, organic=0.6, skew_deg=10.0)
    assert [(n.x, n.y) for n in a.nodes.values()] == [(n.x, n.y) for n in b.nodes.values()]


def test_cli_pipeline_organic_town(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    prefix = tmp_path / "town"
    assert main(["pipeline", "lsystem", "--preset", "organic_town", "--set", "extent=[900, 700]",
                 "--seed", "2", "--allow-traps", "-o", str(prefix)]) == 0
    assert load_json(prefix.with_suffix(".json")).metadata["params"]["global_goal"] == "field"
