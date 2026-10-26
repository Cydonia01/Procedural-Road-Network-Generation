"""Street pattern fields for lsystem growth (stage C2), after Chen et al. (2008),
"Interactive Procedural Street Modeling", sec. 5, and Parish & Müller's pattern
rules (2001, sec. 3.2.2).

Each pattern gives, at a point, a direction field as a symmetric traceless
tensor, stored as the vector (cos 2t, sin 2t) times a strength: t is the
*major* direction (the avenues), the minor direction (streets) is t + 90 deg.
Tensors don't care whether a road runs one way or the other, nor whether it
is the major or minor road of a grid, so patterns blend without wrap-around
artefacts: the blended field is the weighted sum of the pattern tensors
(Chen eq. 3, with the weights of Parish & Müller's control maps instead of a
radial decay). Patterns:

- grid: one direction everywhere (New York rule);
- radial: major along the line to the nearest centre, minor around it (Paris);
- organic: the grid rotated by smooth Perlin noise (Chen sec. 5.3);
- contour: major along the terrain contour, weighted by the slope, the grid
  on flat ground (San Francisco rule; Chen's height-field field);
- suburb: like organic but curvier, with shorter blocks and fewer branches
  (Teng & Bidarra's suburban patches).

On top: a skew field (Chen's rotation field R3, one-sided like his R2
example in [0, 20 deg]) turns the minor direction away from the major by 0
to skew, in patches of skew_scale, so streets cross avenues at other angles
than 90 deg (Y and K junctions); the organic knob in [0, 1] moves
weight from the grid to the organic pattern, and headings within
(1 - organic) * snap_deg of the un-noised field snap to it (TownSim's snap
knob, Song & Whitehead 2019 sec. 3.3), so 0 keeps exact grids.

Pure functions of the point and the field's parameters; noise comes from a
random generator handed in, so a field is deterministic for a seed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

from roadgen.maps import noise

Point = tuple[float, float]
PATTERNS = ("grid", "radial", "organic", "contour", "suburb")
GRID, RADIAL, ORGANIC, CONTOUR, SUBURB = range(len(PATTERNS))
MAJOR, MINOR = 0, 1  # road roles: avenues follow the major direction, streets the minor
CONTOUR_SLOPE = 0.08  # terrain grade at which contour roads follow the contour fully
SUBURB_CURL = 1.5  # suburb noise turns this much more than organic noise, at half its scale
DEGENERATE = 1e-6  # tensors weaker than this have no direction
NOISE_OCTAVES = 2


@dataclass(frozen=True)
class FieldParams:
    grid_angle: float = 0.0  # direction of the grid's major (avenue) axis [rad]
    block: tuple[float, float] = (250.0, 80.0)  # segment length along (minor, major) roads [m]
    suburb_block: tuple[float, float] = (120.0, 60.0)  # the same in suburbs [m]
    branch_prob: float = 1.0
    suburb_branch_prob: float = 0.6
    radial_centers: tuple[Point, ...] = ((0.0, 0.0),)
    radial_radius: float = 0.0  # [m] Gaussian falloff of the radial weight; 0 = none
    organic: float = 0.0  # grid-to-organic knob, [0, 1]
    organic_max: float = math.radians(45.0)  # largest rotation of the organic pattern [rad]
    organic_scale: float = 600.0  # [m] size of the organic pattern's bends
    skew: float = 0.0  # largest rotation of the minor direction, in [0, skew] [rad]
    skew_scale: float = 1500.0  # [m] size of the skew field's patches
    snap: float = math.radians(15.0)  # snap angle at organic 0 [rad]
    weights: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 0.0)  # per pattern, without a control map


@dataclass
class StreetField:
    """The blended field of a growth area (local frame).

    weights_at: per-pattern weights at a point (a control map); None uses
    params.weights, with the radial weight falling off around the centres.
    gradient_at: terrain gradient (dz/dx, dz/dy) at a point, for contour.
    angle_at: the grid's major direction at a point (districts with their own
    grid angle); None uses params.grid_angle everywhere.
    """

    params: FieldParams
    rng: np.random.Generator
    weights_at: Optional[Callable[[Point], Sequence[float]]] = None
    gradient_at: Optional[Callable[[Point], tuple[float, float]]] = None
    angle_at: Optional[Callable[[Point], float]] = None
    _noise: list = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        # Three independent noise fields: organic, suburb, skew; each a permutation and offsets.
        self._noise = [(noise.permutation(self.rng), self.rng.uniform(0, noise.PERIOD, (NOISE_OCTAVES, 2)))
                       for _ in range(3)]

    # --- inputs ---------------------------------------------------------------------------

    def _smooth(self, k: int, p: Point, scale: float) -> float:
        """Smooth noise in about [-1, 1] with features of size scale [m]."""
        perm, offsets = self._noise[k]
        total, amp, norm = 0.0, 1.0, 0.0
        for octave in range(NOISE_OCTAVES):
            f = 2.0 ** octave / scale
            total += amp * float(noise.perlin(np.array(p[0] * f + offsets[octave, 0]),
                                              np.array(p[1] * f + offsets[octave, 1]), perm))
            norm += amp
            amp *= 0.5
        return max(-1.0, min(1.0, total / norm))

    def weights(self, p: Point, knob: bool = True) -> list[float]:
        """Per-pattern weights at p, summing to 1; knob: after the organic knob."""
        fp = self.params
        if self.weights_at is not None:
            w = [float(v) for v in self.weights_at(p)]
        else:
            w = list(fp.weights)
            if fp.radial_radius > 0:
                d = min(math.dist(p, c) for c in fp.radial_centers)
                w[RADIAL] *= math.exp(-(d / fp.radial_radius) ** 2)
        if knob:
            moved = w[GRID] * fp.organic
            w[GRID] -= moved
            w[ORGANIC] += moved
        total = sum(w)
        return [v / total for v in w] if total > 0 else [1.0] + [0.0] * (len(PATTERNS) - 1)

    # --- tensors ----------------------------------------------------------------------------

    def _pattern(self, k: int, p: Point) -> tuple[float, float]:
        """Tensor (strength * cos 2t, strength * sin 2t) of pattern k at p."""
        fp = self.params
        g = self.angle_at(p) if self.angle_at is not None else fp.grid_angle
        if k == GRID:
            angle = g
        elif k == ORGANIC:
            angle = g + fp.organic_max * self._smooth(0, p, fp.organic_scale)
        elif k == SUBURB:
            angle = g + SUBURB_CURL * fp.organic_max * self._smooth(1, p, fp.organic_scale / 2)
        elif k == RADIAL:
            c = min(fp.radial_centers, key=lambda c: math.dist(p, c))
            dx, dy = p[0] - c[0], p[1] - c[1]
            if math.hypot(dx, dy) < 1.0:
                return 0.0, 0.0  # no direction at the centre
            angle = math.atan2(dy, dx)
        else:  # CONTOUR
            gx, gy = self.gradient_at(p) if self.gradient_at else (0.0, 0.0)
            slope = math.hypot(gx, gy)
            share = min(1.0, slope / CONTOUR_SLOPE)
            contour = math.atan2(gx, -gy)  # perpendicular to the gradient
            return ((1 - share) * math.cos(2 * g) + share * math.cos(2 * contour),
                    (1 - share) * math.sin(2 * g) + share * math.sin(2 * contour))
        return math.cos(2 * angle), math.sin(2 * angle)

    def tensor(self, p: Point, plain: bool = False) -> tuple[float, float]:
        """Blended tensor at p; plain: without the organic knob (the field snapped to)."""
        w = self.weights(p, knob=not plain)
        a = b = 0.0
        for k, wk in enumerate(w):
            if wk > 0:
                ta, tb = self._pattern(k, p)
                a, b = a + wk * ta, b + wk * tb
        return a, b

    def major_angle(self, p: Point, plain: bool = False) -> Optional[float]:
        """Major direction [rad] at p, or None at a degenerate point."""
        a, b = self.tensor(p, plain=plain)
        if math.hypot(a, b) < DEGENERATE:
            return None
        return 0.5 * math.atan2(b, a)

    # --- what growth asks for ----------------------------------------------------------

    def direction(self, p: Point, role: int, hint: float) -> float:
        """Heading [rad] for a road of role at p: the field direction closest to hint.

        The minor direction is turned by the skew field. Near the un-noised
        field's direction the heading snaps to it (organic knob). At a
        degenerate point the road keeps hint.
        """
        fp = self.params
        major = self.major_angle(p)
        if major is None:
            return hint
        heading = _closest(self._role_angle(p, major, role), hint)
        snap = fp.snap * (1.0 - fp.organic)
        if fp.organic > 0 and snap > 0:
            plain = self.major_angle(p, plain=True)
            if plain is not None:
                straight = _closest(self._role_angle(p, plain, role), heading)
                if abs(_wrap(heading - straight)) < snap:
                    heading = straight
        return heading

    def _role_angle(self, p: Point, major: float, role: int) -> float:
        if role == MAJOR:
            return major
        skew = self.params.skew * (0.5 + 0.5 * self._smooth(2, p, self.params.skew_scale)) \
            if self.params.skew else 0.0
        return major + math.pi / 2 + skew

    def closest_role(self, p: Point, heading: float) -> int:
        """MAJOR or MINOR, whichever direction at p lies closer to heading."""
        major = self.major_angle(p)
        if major is None:
            return MINOR
        d_major = abs(_wrap(_closest(major, heading) - heading))
        d_minor = abs(_wrap(_closest(self._role_angle(p, major, MINOR), heading) - heading))
        return MAJOR if d_major <= d_minor else MINOR

    def length(self, p: Point, role: int) -> float:
        """Segment length [m] of a road of role at p: the patterns' block sizes, weighted."""
        w = self.weights(p)
        normal = self.params.block[1 - role]  # block = (minor, major)
        suburb = self.params.suburb_block[1 - role]
        return (1.0 - w[SUBURB]) * normal + w[SUBURB] * suburb

    def branch_prob(self, p: Point) -> float:
        w = self.weights(p)[SUBURB]
        return (1.0 - w) * self.params.branch_prob + w * self.params.suburb_branch_prob


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _closest(angle: float, hint: float) -> float:
    """angle or angle + pi, whichever lies closer to hint (roads run either way)."""
    return angle if abs(_wrap(angle - hint)) <= math.pi / 2 else _wrap(angle + math.pi)
