from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.integrate import quad

from roadgen.geometry import design
from roadgen.geometry.clothoid import g2_hermite
from roadgen.geometry.design import DesignParams
from roadgen.geometry.planar import Pose
from roadgen.geometry.primitives import Alignment, Piece, chain

KMH = 1 / 3.6


# --- design-speed rules ------------------------------------------------------------

def test_min_radius_matches_the_plan():
    # e + f: 0.04 + 0.195 at 50 km/h, 0.06 + 0.14 at 80 km/h.
    assert design.min_radius(50 * KMH, 0.04) == pytest.approx(83.8, abs=0.5)
    assert design.min_radius(80 * KMH, 0.06) == pytest.approx(251.7, abs=0.5)


def test_side_friction_is_capped_by_comfort():
    # The table allows 0.2825 at 30 km/h; with e = 0.04, g (e + f) would be 3.16 m/s^2.
    f = design.side_friction(30 * KMH, 0.04)
    assert design.G * (0.04 + f) == pytest.approx(3.0)
    assert design.side_friction(80 * KMH, 0.0) == pytest.approx(0.14)


def test_max_speed_inverts_min_radius():
    for kmh in (30, 50, 80, 110):
        v = kmh * KMH
        r = design.min_radius(v, 0.04)
        assert design.max_speed(r, 0.04) == pytest.approx(v, rel=1e-6)
    assert design.max_speed(math.inf, 0.0) == design.MAX_SPEED


def test_advisory_speed_rounds_down_to_5_kmh():
    r = design.min_radius(47 * KMH, 0.0)
    assert design.advisory_speed(r, 0.0) == pytest.approx(45 * KMH)
    assert design.advisory_speed(0.5, 0.0) == pytest.approx(5 * KMH)  # never below 5 km/h


def test_superelevation_off_without_banking_and_method_5_with_it():
    assert design.superelevation(100.0, 50 * KMH, "street") == 0.0
    p = DesignParams(banking=True)
    r_min = design.min_radius(50 * KMH, 0.04, p)
    assert design.superelevation(r_min, 50 * KMH, "street", p) == pytest.approx(0.04)
    assert design.superelevation(2 * r_min, 50 * KMH, "street", p) == pytest.approx(0.04 * 0.75)
    assert design.superelevation(math.inf, 50 * KMH, "street", p) == 0.0


def test_spiral_length_aashto_rules():
    v = 50 * KMH
    # Comfort (C = 1.2 m/s^3) decides at the design radius: about 26.6 m.
    assert design.spiral_length(v, 84.0, 0.0, 3.5) == pytest.approx(v ** 3 / (1.2 * 84.0))
    # A gentle curve: the minimum shift of 0.2 m decides.
    assert design.spiral_length(v, 1000.0, 0.0, 3.5) == pytest.approx(math.sqrt(24 * 0.2 * 1000.0))
    # A tight curve: comfort would want more than the maximum shift of 1 m allows.
    assert design.spiral_length(v, 20.0, 0.0, 3.5) == pytest.approx(math.sqrt(24 * 1.0 * 20.0))
    # Banked: the superelevation runoff decides.
    assert design.spiral_length(v, 200.0, 0.04, 7.0) == pytest.approx(7.0 * 0.04 / 0.0065)
    assert design.spiral_length(v, math.inf, 0.0, 3.5) == 0.0


def test_taper_length_mutcd():
    assert design.taper_length(50 * KMH, 3.5) == pytest.approx(3.5 * 2500 / 155)
    assert design.taper_length(80 * KMH, 3.5) == pytest.approx(3.5 * 80 / 1.6)


def test_design_params_validation():
    with pytest.raises(ValueError):
        DesignParams(e_max_highway=0.4)
    with pytest.raises(ValueError):
        DesignParams(jerk_rate=0.0)
    with pytest.raises(ValueError):
        DesignParams(spiral_min_shift=2.0, spiral_max_shift=1.0)


# --- pieces --------------------------------------------------------------------------

def _integrated_end(piece: Piece) -> tuple[float, float]:
    x = quad(lambda u: math.cos(piece.heading(u)), 0, piece.length, epsabs=1e-12, limit=200)[0]
    y = quad(lambda u: math.sin(piece.heading(u)), 0, piece.length, epsabs=1e-12, limit=200)[0]
    return piece.start.x + x, piece.start.y + y


@pytest.mark.parametrize("k0, k1, length", [
    (0.0, 0.02, 40.0),       # spiral into a curve
    (0.02, 0.0, 40.0),       # spiral out
    (-0.01, 0.03, 70.0),     # through zero curvature
    (0.05, 0.05 + 1e-8, 30.0),  # nearly constant curvature: Fresnel at large arguments
    (0.0, -0.2, 15.0),       # tight right-hand spiral
    (0.01, 0.01, 50.0),      # arc
    (0.0, 0.0, 25.0),        # line
])
def test_piece_end_matches_numerical_integration(k0, k1, length):
    piece = Piece(Pose(3.0, -2.0, 0.7), length, k0, k1)
    x, y = _integrated_end(piece)
    end = piece.end
    assert math.hypot(end.x - x, end.y - y) < 1e-6


def test_piece_kinds_and_turn():
    start = Pose(0.0, 0.0, 0.0)
    assert Piece(start, 10.0).kind == "line"
    assert Piece(start, 10.0, 0.1, 0.1).kind == "arc"
    assert Piece(start, 10.0, 0.0, 0.1).kind == "spiral"
    assert Piece(start, 10.0, 0.0, 0.1).turn == pytest.approx(0.5)
    with pytest.raises(ValueError):
        Piece(start, 0.0)


def test_alignment_is_continuous_and_samples_cover_it():
    # Line - spiral - arc - spiral - line: a 90 degree corner, R = 50 m.
    r, ls = 50.0, 20.0
    arc_len = r * (math.pi / 2 - ls / r)
    pieces = chain(Pose(0.0, 0.0, 0.0), [(30.0, 0, 0), (ls, 0, 1 / r), (arc_len, 1 / r, 1 / r),
                                          (ls, 1 / r, 0), (30.0, 0, 0)])
    al = Alignment(pieces)
    assert al.end.hdg == pytest.approx(math.pi / 2)
    for gap, dh, dk in al.joint_errors():
        assert gap < 1e-9 and dh < 1e-12 and dk < 1e-12
    smp = al.sample(1.0)
    assert smp.s[0] == 0.0 and smp.s[-1] == pytest.approx(al.length)
    assert np.all(np.diff(smp.s) <= 1.0 + 1e-9)
    assert smp.curvature.max() == pytest.approx(1 / r)
    assert al.pose(al.length).x == pytest.approx(al.end.x)
    seg = np.hypot(np.diff(smp.x), np.diff(smp.y))
    assert seg.sum() == pytest.approx(al.length, rel=1e-3)


# --- G2 clothoid interpolation -------------------------------------------------------

def test_g2_hermite_right_angle_turn():
    pieces = g2_hermite(Pose(0, 0, 0), 0.0, Pose(30, 30, math.pi / 2), 0.0)
    assert pieces is not None and len(pieces) == 3
    assert pieces[0].k0 == pytest.approx(0.0, abs=1e-9)
    assert pieces[-1].k1 == pytest.approx(0.0, abs=1e-9)
    end = pieces[-1].end
    assert math.hypot(end.x - 30, end.y - 30) < 1e-3
    al = Alignment(pieces)
    assert max(dk for _, _, dk in al.joint_errors()) < 1e-5


def test_g2_hermite_matches_end_curvature():
    pieces = g2_hermite(Pose(0, 0, 0), 0.01, Pose(40, 10, 0.4), -0.02)
    assert pieces is not None
    assert pieces[0].k0 == pytest.approx(0.01, abs=1e-6)
    assert pieces[-1].k1 == pytest.approx(-0.02, abs=1e-6)


def test_g2_hermite_rejects_degenerate_and_limited_cases():
    assert g2_hermite(Pose(0, 0, 0), 0.0, Pose(0, 0, 0), 0.0) is None
    # A near U-turn in a short distance: the solution exceeds a tight curvature limit.
    assert g2_hermite(Pose(0, 0, 0), 0.0, Pose(-5, 1, 3.1), 0.0, k_limit=0.2) is None
