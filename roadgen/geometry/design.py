"""Design-speed rules for road geometry: radii, advisory speeds, spirals, tapers.

Based on the AASHTO Green Book (side friction, superelevation distribution,
superelevation runoff) and the MUTCD (lane-change tapers):

- min_radius(v, e) = v^2 / (g (e + f)), with f the AASHTO side-friction
  factor for v, capped so that g (e + f) never exceeds a_lat_max;
- advisory_speed(R, e): the fastest v for which R is the minimum radius,
  rounded down to a multiple of 5 km/h like a curve-warning sign;
- superelevation(R, v): AASHTO method 5 shape, e_max (2 R_min/R - (R_min/R)^2);
- spiral_length(v, R, e, width): the longest of the lateral-jerk comfort
  length v^3 / (C R) (Shortt), the superelevation runoff w e / delta(v) and
  the minimum-shift length, capped at the maximum-shift length;
- taper_length(v, w): MUTCD merging taper.

Speeds are in m/s at the interface; the tables are indexed in km/h.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from roadgen.config import DEFAULTS, section

_D = section(DEFAULTS, "design")
G = 9.81  # [m/s^2]
KMH = 1 / 3.6  # km/h -> m/s

# AASHTO maximum side friction f_max by design speed [km/h]: low-speed urban
# streets up to 72 km/h, high-speed values above.
F_MAX_TABLE = (
    (16, 0.38), (24, 0.32), (32, 0.27), (40, 0.23), (48, 0.20), (56, 0.18), (64, 0.16),
    (72, 0.15), (80, 0.14), (89, 0.13), (97, 0.12), (105, 0.11), (113, 0.10), (121, 0.09),
    (129, 0.08),
)
# AASHTO maximum relative gradient of the rotated edge during superelevation
# runoff, by design speed [km/h].
RELATIVE_GRADIENT_TABLE = (
    (20, 0.0080), (30, 0.0075), (40, 0.0070), (50, 0.0065), (60, 0.0060), (70, 0.0055),
    (80, 0.0050), (90, 0.0047), (100, 0.0044), (110, 0.0041), (120, 0.0038), (130, 0.0035),
)
ADVISORY_STEP = 5 * KMH  # advisory speeds are multiples of this [m/s]
MAX_SPEED = 200 * KMH  # upper bound for the advisory-speed search [m/s]


@dataclass(frozen=True)
class DesignParams:
    e_max_street: float = _D["e_max_street"]  # max superelevation, rise / run
    e_max_avenue: float = _D["e_max_avenue"]
    e_max_highway: float = _D["e_max_highway"]
    a_lat_max: float = _D["a_lat_max"]  # comfort cap on g (e + f) [m/s^2]
    jerk_rate: float = _D["jerk_rate"]  # rate of change of lateral acceleration in spirals [m/s^3]
    spiral_min_shift: float = _D["spiral_min_shift"]  # lateral shift of the curve by its spirals [m]
    spiral_max_shift: float = _D["spiral_max_shift"]
    banking: bool = _D["banking"]  # bank curves (superelevation); off = flat roads
    bank_ramp_length: float = _D["bank_ramp_length"]  # road end to junction cross slope [m]
    junction_max_grade: float = _D["junction_max_grade"]  # steepest junction plane; 0 = flat
    max_merge_deflection_deg: float = _D["max_merge_deflection_deg"]  # sharper bends stay junctions [deg]
    road_clearance: float = _D["road_clearance"]  # min gap between a curve and other roads [m]
    max_corner_offset: float = _D["max_corner_offset"]  # max corner cut-in [m]; 0 = no limit
    min_lane_radius: float = _D["min_lane_radius"]  # tighter bends stay junctions [m]

    def __post_init__(self) -> None:
        if min(self.e_max_street, self.e_max_avenue, self.e_max_highway) < 0:
            raise ValueError("e_max values must be non-negative")
        if self.a_lat_max <= 0 or self.jerk_rate <= 0:
            raise ValueError("need a_lat_max > 0 and jerk_rate > 0")
        if not 0 < self.spiral_min_shift <= self.spiral_max_shift:
            raise ValueError("need 0 < spiral_min_shift <= spiral_max_shift")
        if not 0 < self.max_merge_deflection_deg < 180:
            raise ValueError("max_merge_deflection_deg must be in (0, 180)")
        if self.bank_ramp_length <= 0 or self.junction_max_grade < 0:
            raise ValueError("need bank_ramp_length > 0 and junction_max_grade >= 0")
        if self.road_clearance < 0 or self.max_corner_offset < 0 or self.min_lane_radius < 0:
            raise ValueError("road_clearance and max_corner_offset must be non-negative")
        if max(self.e_max_street, self.e_max_avenue, self.e_max_highway) * G >= self.a_lat_max:
            raise ValueError("e_max must leave room for side friction under a_lat_max")


def _interp(table: tuple[tuple[float, float], ...], v: float) -> float:
    kmh, values = zip(*table)
    return float(np.interp(v * 3.6, kmh, values))


def f_max(v: float) -> float:
    """AASHTO maximum side-friction factor at speed v [m/s] (clamped to the table)."""
    return _interp(F_MAX_TABLE, v)


def side_friction(v: float, e: float, p: DesignParams = DesignParams()) -> float:
    """Usable side friction at v [m/s] on superelevation e, within the comfort cap."""
    return max(0.0, min(f_max(v), p.a_lat_max / G - e))


def e_max(road_class: str, p: DesignParams = DesignParams()) -> float:
    return {"street": p.e_max_street, "avenue": p.e_max_avenue, "highway": p.e_max_highway}[road_class]


def min_radius(v: float, e: float, p: DesignParams = DesignParams()) -> float:
    """Smallest curve radius [m] drivable at v [m/s] on superelevation e."""
    return v * v / (G * (e + side_friction(v, e, p)))


def max_speed(radius: float, e: float, p: DesignParams = DesignParams()) -> float:
    """Fastest v [m/s] for which radius [m] is still at least min_radius(v, e).

    v^2 / R - g (e + f(v)) increases with v (f falls with speed), so bisection
    finds the single crossing.
    """
    if math.isinf(radius):
        return MAX_SPEED
    lo, hi = 0.0, MAX_SPEED
    if hi * hi / radius <= G * (e + side_friction(hi, e, p)):
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if mid * mid / radius <= G * (e + side_friction(mid, e, p)):
            lo = mid
        else:
            hi = mid
    return lo


def advisory_speed(radius: float, e: float, p: DesignParams = DesignParams()) -> float:
    """max_speed rounded down to a multiple of 5 km/h, at least 5 km/h [m/s]."""
    v = max_speed(radius, e, p)
    return max(ADVISORY_STEP, math.floor(v / ADVISORY_STEP + 1e-9) * ADVISORY_STEP)


def superelevation(radius: float, v: float, road_class: str, p: DesignParams = DesignParams()) -> float:
    """Superelevation of a curve [rise / run]: 0 without banking or on a tangent."""
    if not p.banking or math.isinf(radius):
        return 0.0
    em = e_max(road_class, p)
    r_min = min_radius(v, em, p)
    q = min(1.0, r_min / radius)
    return em * (2 * q - q * q)


def relative_gradient(v: float) -> float:
    """AASHTO maximum relative gradient for superelevation runoff at v [m/s]."""
    return _interp(RELATIVE_GRADIENT_TABLE, v)


def spiral_length(v: float, radius: float, e: float, width: float,
                  p: DesignParams = DesignParams()) -> float:
    """Transition-spiral length [m] into a curve of the given radius (AASHTO).

    The longest of the lateral-jerk comfort length v^3 / (C R), the
    superelevation runoff w e / delta(v) and the length giving the minimum
    lateral shift, but at most the length giving the maximum shift
    (L = sqrt(24 p R) for a shift p; longer spirals look out of place).

    width: distance from the rotation axis (reference line) to the farthest
    rotated edge [m]; e: the curve's superelevation.
    """
    if math.isinf(radius):
        return 0.0
    comfort = v ** 3 / (p.jerk_rate * radius)
    runoff = width * e / relative_gradient(v)
    shortest = math.sqrt(24 * p.spiral_min_shift * radius)
    longest = math.sqrt(24 * p.spiral_max_shift * radius)
    return min(max(comfort, runoff, shortest), longest)


def taper_length(v: float, width: float) -> float:
    """MUTCD merging taper [m] for shifting traffic sideways by width [m] at v [m/s]."""
    s = v * 3.6
    return width * s * s / 155.0 if s < 70.0 else width * s / 1.6
