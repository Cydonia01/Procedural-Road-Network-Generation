"""Bridges and tunnels in the scene (polish R4d): esmini draws a bridge as a
road surface in the air and a tunnel as a road under the ground, nothing more.

- **Bridge deck.** Under each bridge stretch of a road (an <objects><bridge>
  record), a deck ``deck_depth`` thick, its sides rising ``parapet`` above
  the road's outer edges.
- **Piers.** Every ``pier_spacing`` along a bridge longer than that, a pier
  ``pier_size`` thick and half the deck wide, from below the ground to the
  deck; none where it would stand on another road or where the deck is lower
  than a metre above the ground.
- **Tunnel portals.** At both ends of each tunnel stretch, a frame round the
  road: two walls beside it and a lintel ``portal_clearance`` above it, each
  ``portal_frame`` thick.

Heights come from the .xodr (roadgen.scene.surface), the ground's from the
ground mesh. Concrete grey; nothing in the .xodr reads them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import shapely

from roadgen.config import DEFAULTS, section
from roadgen.scene.osgt import Mesh
from roadgen.scene.surface import RoadSurfaces, Span

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "structures")
Heights = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class StructureParams:
    enabled: bool = _D["enabled"]  # draw bridge decks, piers and tunnel portals
    deck_depth: float = _D["deck_depth"]  # deck below the road surface [m]
    parapet: float = _D["parapet"]  # deck sides above the road's outer edges [m]
    pier_spacing: float = _D["pier_spacing"]  # distance between piers along a bridge [m]
    pier_size: float = _D["pier_size"]  # pier thickness along the bridge [m]
    portal_clearance: float = _D["portal_clearance"]  # lintel above the road at a tunnel portal [m]
    portal_frame: float = _D["portal_frame"]  # thickness of a portal's walls and lintel [m]
    color: tuple[float, float, float] = _D["color"]  # RGB in [0, 1]

    def __post_init__(self) -> None:
        if (min(self.deck_depth, self.pier_spacing, self.pier_size, self.portal_clearance, self.portal_frame) <= 0
                or self.parapet < 0 or not all(0.0 <= c <= 1.0 for c in self.color)):
            raise ValueError("need positive deck_depth, pier_spacing, pier_size, portal_clearance and "
                             "portal_frame, parapet >= 0 and color in [0, 1]")


def structure_mesh(surfaces: RoadSurfaces, ground: Heights,
                   params: StructureParams = StructureParams()) -> tuple[Mesh, dict[str, int]]:
    """The decks, piers and portals of every bridge and tunnel, and how many of each."""
    tris: list = []
    roads = shapely.STRtree(surfaces.parts) if surfaces.parts else None
    counts = {"decks": 0, "piers": 0, "portals": 0}
    for sp in surfaces.spans:
        if sp.kind == "bridge":
            tris += _deck(sp, params)
            counts["decks"] += 1
            for pier in _piers(sp, ground, roads, params):
                tris += pier
                counts["piers"] += 1
        else:
            for end in (0, -1):
                tris += _portal(sp, end, params)
                counts["portals"] += 1
    normals = [_normal(t) for t in tris]
    log.info("structures: %(decks)d bridge decks, %(piers)d piers, %(portals)d tunnel portals", counts)
    return Mesh("structures", [tuple(map(tuple, t)) for t in tris], normals, tuple(params.color)), counts


def _deck(sp: Span, p: StructureParams) -> list[np.ndarray]:
    """Two sides (parapet to deck bottom) and the underside, as triangles."""
    up, down = np.array([0, 0, p.parapet]), np.array([0, 0, -p.deck_depth])
    out = []
    for edge, outward in ((sp.left, 1.0), (sp.right, -1.0)):
        out += _strip(edge + down, edge + up, flip=outward > 0)  # facing away from the road
    out += _strip(sp.right + down, sp.left + down, flip=True)  # facing down
    return out


def _strip(a: np.ndarray, b: np.ndarray, flip: bool) -> list[np.ndarray]:
    """Triangles between two polylines of the same length."""
    out = []
    for k in range(len(a) - 1):
        t1, t2 = np.array([a[k], a[k + 1], b[k + 1]]), np.array([a[k], b[k + 1], b[k]])
        out += [t1[::-1], t2[::-1]] if flip else [t1, t2]
    return out


def _piers(sp: Span, ground: Heights, roads, p: StructureParams) -> list[list[np.ndarray]]:
    length = float(np.sum(np.hypot(*np.diff(sp.centre[:, :2], axis=0).T)))
    count = int(length // p.pier_spacing)
    if count < 1:
        return []
    along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(sp.centre[:, :2], axis=0).T))])
    out = []
    for d in (np.arange(1, count + 1) * length / (count + 1)):
        k = int(np.clip(np.searchsorted(along, d), 0, len(along) - 1))
        cx, cy, cz = sp.centre[k]
        h = sp.hdg[k]
        u, v = np.array([np.cos(h), np.sin(h)]), np.array([-np.sin(h), np.cos(h)])
        half = (p.pier_size / 2, sp.half_width / 2)
        corners = [np.array([cx, cy]) + su * half[0] * u + sv * half[1] * v
                   for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        if roads is not None and len(roads.query(shapely.Polygon(corners), predicate="intersects")):
            continue
        gz = ground(np.array([c[0] for c in corners]), np.array([c[1] for c in corners]))
        if not np.isfinite(gz).all():
            continue
        top = cz - p.deck_depth
        if top - gz.max() < 1.0:
            continue
        out.append(_box(corners, float(gz.min()) - 1.0, top))
    return out


def _portal(sp: Span, end: int, p: StructureParams) -> list[np.ndarray]:
    """Two walls beside the road and a lintel over it, across the road at one end."""
    h = sp.hdg[end]
    u, v = np.array([np.cos(h), np.sin(h)]), np.array([-np.sin(h), np.cos(h)])
    c = sp.centre[end]
    zl, zr = sp.left[end, 2], sp.right[end, 2]
    w = sp.half_width
    base = min(zl, zr, c[2]) - 0.5
    top = max(zl, zr, c[2]) + p.portal_clearance
    out = []
    for t0, t1 in ((w, w + p.portal_frame), (-w - p.portal_frame, -w)):  # the walls
        out += _box(_rect(c[:2], u, v, -p.portal_frame / 2, p.portal_frame / 2, t0, t1), base, top + p.portal_frame)
    out += _box(_rect(c[:2], u, v, -p.portal_frame / 2, p.portal_frame / 2, -w, w), top, top + p.portal_frame)
    return out


def _rect(c: np.ndarray, u: np.ndarray, v: np.ndarray, s0: float, s1: float, t0: float, t1: float) -> list:
    return [c + s * u + t * v for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1))]


def _box(corners: list, z0: float, z1: float) -> list[np.ndarray]:
    """Triangles of the four sides and the top of a prism over a convex quadrilateral,
    facing outward."""
    lo = [np.array([x, y, z0]) for x, y in corners]
    hi = [np.array([x, y, z1]) for x, y in corners]
    centre = np.mean(lo + hi, axis=0)
    faces = [[lo[k], lo[(k + 1) % 4], hi[(k + 1) % 4], hi[k]] for k in range(4)] + [hi]
    out = []
    for f in faces:
        for t in (np.array([f[0], f[1], f[2]]), np.array([f[0], f[2], f[3]])):
            n = np.cross(t[1] - t[0], t[2] - t[0])
            out.append(t if np.dot(n, t.mean(axis=0) - centre) >= 0 else t[::-1])
    return out


def _normal(t: np.ndarray) -> tuple[float, float, float]:
    n = np.cross(t[1] - t[0], t[2] - t[0])
    n = n / (np.linalg.norm(n) or 1.0)
    return float(n[0]), float(n[1]), float(n[2])
