"""Planar geometry helpers for straight OpenDRIVE road segments."""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

Point = tuple[float, float]


class Pose(NamedTuple):
    x: float
    y: float
    hdg: float


def segment_start(start: Point, end: Point) -> tuple[Pose, float]:
    """Return the start pose (heading towards end) and length of start->end."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        raise ValueError(f"zero-length segment at {start}")
    return Pose(start[0], start[1], math.atan2(dy, dx)), length


def trim(
    start: Point, end: Point, trim_start: float, trim_end: float
) -> tuple[Point, Point]:
    """Shorten start->end by trim_start at the start and trim_end at the end.

    Raises ValueError if the trims are negative or leave no positive length.
    """
    if trim_start < 0 or trim_end < 0:
        raise ValueError("trim distances must be non-negative")
    pose, length = segment_start(start, end)
    if trim_start + trim_end >= length:
        raise ValueError(
            f"trims {trim_start} + {trim_end} exceed segment length {length}"
        )
    ux, uy = math.cos(pose.hdg), math.sin(pose.hdg)
    new_start = (start[0] + ux * trim_start, start[1] + uy * trim_start)
    new_end = (end[0] - ux * trim_end, end[1] - uy * trim_end)
    return new_start, new_end


def wrap_angle(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    a = math.fmod(a + math.pi, 2 * math.pi)
    if a <= 0:
        a += 2 * math.pi
    return a - math.pi


def offset_point(pose: Pose, t: float) -> Point:
    """Point at lateral offset t from pose (positive t = left of heading)."""
    return (pose.x - t * math.sin(pose.hdg), pose.y + t * math.cos(pose.hdg))


class Poly3Curve(NamedTuple):
    """paramPoly3 coefficients in the start pose's local frame, pRange normalized."""

    u: tuple[float, float, float, float]
    v: tuple[float, float, float, float]
    length: float


def hermite_poly3(start: Pose, end: Pose) -> Poly3Curve:
    """Cubic Hermite curve matching position and heading at both ends.

    Tangent magnitudes follow the cubic approximation of a circular arc
    turning by the heading change theta: k = chord * 2 tan(theta/4) / sin(theta/2).
    That is the chord itself for gentle turns and grows for sharp ones, which
    keeps the parameter speed (s -> position) nearly uniform even for hairpins.
    """
    dx, dy = end.x - start.x, end.y - start.y
    chord = math.hypot(dx, dy)
    if chord == 0.0:
        raise ValueError("start and end coincide")
    c, s = math.cos(start.hdg), math.sin(start.hdg)
    ue, ve = dx * c + dy * s, -dx * s + dy * c
    he = wrap_angle(end.hdg - start.hdg)
    half = abs(he) / 2
    k = chord if half < 1e-6 else chord * 2 * math.tan(half / 2) / math.sin(half)
    u = (0.0, k, 3 * ue - 2 * k - k * math.cos(he), -2 * ue + k + k * math.cos(he))
    v = (0.0, 0.0, 3 * ve - k * math.sin(he), -2 * ve + k * math.sin(he))
    return Poly3Curve(u, v, poly3_length(u, v))


# 16-point Gauss-Legendre nodes/weights mapped to [0, 1]; the integrand is a
# smooth sqrt of a quartic, so this is accurate far below a millimeter.
_GL_X, _GL_W = np.polynomial.legendre.leggauss(16)
_GL_P, _GL_W = (_GL_X + 1) / 2, _GL_W / 2


def poly3_length(u: tuple[float, ...], v: tuple[float, ...]) -> float:
    """Arc length of a paramPoly3 over p in [0, 1]."""
    p = _GL_P
    du = u[1] + 2 * u[2] * p + 3 * u[3] * p * p
    dv = v[1] + 2 * v[2] * p + 3 * v[3] * p * p
    return float(np.dot(_GL_W, np.hypot(du, dv)))
