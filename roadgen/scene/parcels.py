"""Parcels and one building per parcel (stage R6).

A block's buildable area (roadgen.scene.buildings.footprints: the face minus
the roads with their sidewalks and a setback) is split into parcels by
recursive oriented-box splits (Vanegas et al. 2012, "Procedural generation of
parcels in urban modeling", OBB subdivision):

1. Take the minimum rotated rectangle of the piece.
2. While its short side is deeper than a parcel (and halves stay at least
   the least depth), cut it lengthwise in the middle (two rows back to back).
3. While its long side is wider than max frontage, cut it across, at a
   seeded point between 40 % and 60 %.

Parcels that do not touch the outline of their area (no street frontage)
stay empty, as courtyards. Each parcel gets one building, inset by
``inset`` on every side (front and side setbacks), with sharp corners.
Its height grows with the population density under it: min_height at no
people, max_height at ``height_density`` and above, with seeded log-normal
spread; without a population map it is uniform between them. Parcels on
water or in parks / forest (terrain map) get no building.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from shapely import minimum_rotated_rectangle
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from roadgen.maps.model import VEGETATION, WATER, MapLayer

MAX_DEPTH_SPLITS = 12  # recursion guard


def _axes(poly: Polygon) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """Centre, long axis, short axis (unit vectors) and their lengths of the OBB."""
    rect = minimum_rotated_rectangle(poly)
    pts = np.array(rect.exterior.coords)[:4]
    e0, e1 = pts[1] - pts[0], pts[2] - pts[1]
    l0, l1 = float(np.hypot(*e0)), float(np.hypot(*e1))
    centre = pts.mean(axis=0)
    if l0 >= l1:
        return centre, e0 / (l0 or 1.0), e1 / (l1 or 1.0), l0, l1
    return centre, e1 / (l1 or 1.0), e0 / (l0 or 1.0), l1, l0


def _cut(poly: Polygon, point: np.ndarray, normal: np.ndarray) -> list[Polygon]:
    """The two sides of poly split by the line through point perpendicular to normal."""
    big = 1e4
    tangent = np.array([-normal[1], normal[0]])
    out = []
    for sign in (1.0, -1.0):
        a = point + tangent * big
        b = point - tangent * big
        half = Polygon([a, b, b + sign * normal * big, a + sign * normal * big])
        piece = poly.intersection(half)
        out += [g for g in getattr(piece, "geoms", [piece]) if isinstance(g, Polygon) and g.area > 1e-6]
    return out


def split_parcels(area: Polygon, rng: np.random.Generator, frontage: tuple[float, float],
                  depth: tuple[float, float], level: int = 0) -> list[Polygon]:
    """Parcels of an area (see the module docstring), in a deterministic order."""
    if area.is_empty or area.area < 1.0:
        return []
    centre, long_axis, short_axis, length, width = _axes(area)
    if level < MAX_DEPTH_SPLITS and width > depth[1] and width / 2 >= depth[0]:
        pieces = _cut(area, centre, short_axis)  # two rows, back to back
    elif level < MAX_DEPTH_SPLITS and length > frontage[1]:
        at = centre + long_axis * length * (rng.uniform(0.4, 0.6) - 0.5)
        pieces = _cut(area, at, long_axis)
    else:
        return [area]
    if len(pieces) < 2:
        return [area]
    out: list[Polygon] = []
    for piece in sorted(pieces, key=lambda p: (round(p.centroid.x, 3), round(p.centroid.y, 3))):
        out += split_parcels(piece, rng, frontage, depth, level + 1)
    return out


def with_frontage(parcels: list[Polygon], area: Polygon, min_frontage: float) -> list[Polygon]:
    """Parcels whose outline runs along the area's outline for at least min_frontage."""
    edge = area.exterior.buffer(0.05)
    return [p for p in parcels if p.exterior.intersection(edge).length >= min_frontage]


def building_footprint(parcel: Polygon, inset: float, min_area: float) -> Optional[Polygon]:
    """The parcel inset on every side (sharp corners), or None if too small."""
    shrunk = parcel.buffer(-inset, join_style="mitre")
    parts = [g for g in getattr(shrunk, "geoms", [shrunk]) if isinstance(g, Polygon) and not g.is_empty]
    if not parts:
        return None
    best = max(parts, key=lambda g: g.area)
    return orient(best.simplify(0.05), 1.0) if best.area >= min_area else None


def on_open_land(poly: Polygon, terrain: Optional[MapLayer]) -> bool:
    """False if any corner or the inside point of poly is water, park or forest."""
    if terrain is None:
        return True
    xs, ys = np.array(poly.exterior.coords).T
    p = poly.representative_point()
    cls = terrain.sample(np.append(xs, p.x), np.append(ys, p.y))
    return not np.any((cls == WATER) | (cls == VEGETATION))


def building_height(poly: Polygon, rng: np.random.Generator, min_height: float, max_height: float,
                    population: Optional[MapLayer], height_density: float) -> float:
    """Height [m]: by the density under the footprint, with spread; uniform without a map."""
    if population is None or height_density <= 0:
        return float(rng.uniform(min_height, max_height))
    p = poly.representative_point()
    share = min(1.0, max(0.0, float(population.sample(np.array([p.x]), np.array([p.y]))[0]) / height_density))
    h = (min_height + (max_height - min_height) * share) * math.exp(rng.normal(0.0, 0.25))
    return float(min(max_height, max(min_height, h)))
