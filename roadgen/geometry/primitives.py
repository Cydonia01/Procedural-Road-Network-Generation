"""Curve pieces with linearly varying curvature, and alignments made of them.

A Piece is the OpenDRIVE line / arc / spiral family: curvature
k(ds) = k0 + (k1 - k0) ds / length, so a line has k0 = k1 = 0, an arc
k0 = k1 != 0 and a spiral (clothoid) k0 != k1. Positions along a spiral come
from the Fresnel integrals (scipy.special.fresnel): with
theta(u) = theta0 + k0 u + c u^2 / 2 and c = (k1 - k0) / length, completing
the square turns the position integral into differences of C(t) and S(t).

An Alignment is a chain of pieces, each starting where the previous one ends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal, Union

import numpy as np
from scipy.special import fresnel

from roadgen.geometry.planar import Pose, wrap_angle

PieceKind = Literal["line", "arc", "spiral"]
CURVATURE_EPS = 1e-12  # [1/m] curvature treated as zero
SHARPNESS_EPS = 1e-12  # [1/m^2] curvature rate treated as zero (piece is an arc)

ArrayLike = Union[float, np.ndarray]


@dataclass(frozen=True)
class Piece:
    start: Pose
    length: float  # [m]
    k0: float = 0.0  # curvature at the start [1/m], positive = turning left
    k1: float = 0.0  # curvature at the end [1/m]

    def __post_init__(self) -> None:
        if not self.length > 0:
            raise ValueError(f"piece length must be positive, got {self.length}")

    @property
    def kind(self) -> PieceKind:
        if abs(self.k1 - self.k0) > SHARPNESS_EPS * self.length:
            return "spiral"
        return "line" if abs(self.k0) < CURVATURE_EPS else "arc"

    @property
    def sharpness(self) -> float:
        """Curvature rate dk/ds [1/m^2]."""
        return (self.k1 - self.k0) / self.length

    def curvature(self, ds: ArrayLike) -> ArrayLike:
        return self.k0 + self.sharpness * ds

    def heading(self, ds: ArrayLike) -> ArrayLike:
        return self.start.hdg + self.k0 * ds + 0.5 * self.sharpness * ds * ds

    def xy(self, ds: ArrayLike) -> tuple[ArrayLike, ArrayLike]:
        """Position at distance ds [m] from the piece start (scalar or array)."""
        x0, y0, h0 = self.start
        kind = self.kind
        if kind == "line":
            return x0 + ds * math.cos(h0), y0 + ds * math.sin(h0)
        if kind == "arc":
            k = self.k0
            h = h0 + k * ds
            return x0 + (np.sin(h) - math.sin(h0)) / k, y0 - (np.cos(h) - math.cos(h0)) / k
        c = self.sharpness
        sigma = 1.0 if c > 0 else -1.0
        a = math.sqrt(abs(c) / math.pi)
        alpha = h0 - self.k0 ** 2 / (2 * c)
        s0, c0 = fresnel(a * self.k0 / c)
        s1, c1 = fresnel(a * (np.asarray(ds) + self.k0 / c))
        dc, dsn = c1 - c0, s1 - s0
        x = x0 + (math.cos(alpha) * dc - sigma * math.sin(alpha) * dsn) / a
        y = y0 + (math.sin(alpha) * dc + sigma * math.cos(alpha) * dsn) / a
        return (float(x), float(y)) if np.ndim(ds) == 0 else (x, y)

    def pose(self, ds: float) -> Pose:
        x, y = self.xy(ds)
        return Pose(float(x), float(y), wrap_angle(float(self.heading(ds))))

    @property
    def end(self) -> Pose:
        return self.pose(self.length)

    @property
    def turn(self) -> float:
        """Total heading change [rad], signed."""
        return 0.5 * (self.k0 + self.k1) * self.length


def chain(start: Pose, specs: list[tuple[float, float, float]]) -> list[Piece]:
    """Pieces from (length, k0, k1) specs, each starting at the previous end."""
    pieces: list[Piece] = []
    pose = start
    for length, k0, k1 in specs:
        piece = Piece(pose, length, k0, k1)
        pieces.append(piece)
        pose = piece.end
    return pieces


@dataclass(frozen=True)
class Sampled:
    """An alignment evaluated at stations s."""

    s: np.ndarray
    x: np.ndarray
    y: np.ndarray
    hdg: np.ndarray
    curvature: np.ndarray

    def xy(self) -> np.ndarray:
        return np.column_stack([self.x, self.y])


@dataclass(frozen=True)
class Alignment:
    """A reference line made of consecutive pieces; station s runs from 0 to length."""

    pieces: tuple[Piece, ...]
    stations: tuple[float, ...] = field(init=False)  # start station of each piece

    def __post_init__(self) -> None:
        if not self.pieces:
            raise ValueError("an alignment needs at least one piece")
        object.__setattr__(self, "pieces", tuple(self.pieces))
        object.__setattr__(self, "stations", tuple(np.concatenate([[0.0], np.cumsum(
            [p.length for p in self.pieces])[:-1]]).tolist()))

    @property
    def length(self) -> float:
        return self.stations[-1] + self.pieces[-1].length

    @property
    def start(self) -> Pose:
        return self.pieces[0].start

    @property
    def end(self) -> Pose:
        return self.pieces[-1].end

    def _locate(self, s: float) -> tuple[Piece, float]:
        i = max(0, int(np.searchsorted(self.stations, s, side="right")) - 1)
        return self.pieces[i], min(max(s - self.stations[i], 0.0), self.pieces[i].length)

    def pose(self, s: float) -> Pose:
        piece, ds = self._locate(s)
        return piece.pose(ds)

    def curvature(self, s: float) -> float:
        piece, ds = self._locate(s)
        return float(piece.curvature(ds))

    def points(self, s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(x, y, heading) at stations s (clipped to the alignment)."""
        s = np.clip(np.asarray(s, dtype=float), 0.0, self.length)
        idx = np.clip(np.searchsorted(self.stations, s, side="right") - 1, 0, len(self.pieces) - 1)
        x, y, h = np.empty_like(s), np.empty_like(s), np.empty_like(s)
        for i in np.unique(idx):
            sel = idx == i
            piece = self.pieces[i]
            ds = np.clip(s[sel] - self.stations[i], 0.0, piece.length)
            px, py = piece.xy(ds)
            x[sel], y[sel], h[sel] = px, py, piece.heading(ds)
        return x, y, h

    def sample(self, step: float) -> Sampled:
        """Every piece sampled at most step [m] apart, piece ends included once."""
        parts: list[tuple[np.ndarray, ...]] = []
        for k, (piece, s0) in enumerate(zip(self.pieces, self.stations)):
            n = max(1, math.ceil(piece.length / step))
            ds = np.linspace(0.0, piece.length, n + 1)
            if k < len(self.pieces) - 1:
                ds = ds[:-1]
            x, y = piece.xy(ds)
            parts.append((s0 + ds, np.asarray(x), np.asarray(y), piece.heading(ds),
                          np.asarray(piece.curvature(ds), dtype=float) * np.ones_like(ds)))
        s, x, y, h, k = (np.concatenate(col) for col in zip(*parts))
        return Sampled(s, x, y, h, k)

    def joint_errors(self) -> list[tuple[float, float, float]]:
        """(position gap [m], heading gap [rad], curvature jump [1/m]) at each joint."""
        out = []
        for a, b in pairwise(self.pieces):
            e = a.end
            out.append((math.hypot(e.x - b.start.x, e.y - b.start.y),
                        abs(wrap_angle(e.hdg - b.start.hdg)), abs(a.k1 - b.k0)))
        return out
