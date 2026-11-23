"""Signs and traffic lights of a written .xodr as simple 3D models, for the Blender
export (kadmos.scene.gltf).

Every <signal> stands where the .xodr puts it: at (s, t) on its road, on the
banked road surface there, facing the traffic it is for (orientation "+":
drivers going in +s, so its face looks towards -s; "-" the other way;
hOffset turns it further).

- **Signs.** A post from below the surface to the plate's top, a plate of
  the signal's width and height from zOffset up, in the sign's shape
  (octagon, triangle, circle, diamond, square: SIGNS), with a rim and a face
  in the sign's colours, and a grey back. The face's material is named after
  the sign (sign_<country>_<type>[_<subtype>][_<value>], e.g. sign_DE_274_80)
  and has UVs (0..1 across the plate's square), so an image of the real sign
  can replace its colour in Blender.
- **Lights** (dynamic signals, OpenDRIVE 1000001): a post up to zOffset, a
  dark housing of the signal's width and height, and three lamps (red,
  amber, green; two for other light types) on its front.

Nothing here simulates: lamps are plain colours, not states.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from lxml import etree

from kadmos.scene.gltf import Material, Primitive
from kadmos.scene.roads import frame
from kadmos.validate.continuity import RoadEval

log = logging.getLogger(__name__)

RGB = tuple[float, float, float]
RED, WHITE, BLUE, YELLOW, GREY = (0.75, 0.06, 0.06), (0.93, 0.93, 0.92), (0.05, 0.3, 0.7), (0.98, 0.78, 0.1), \
    (0.55, 0.55, 0.55)
# Sign type (German StVO numbers, as the builder writes them) -> shape, rim colour, face colour.
SIGNS: dict[str, tuple[str, RGB, RGB]] = {
    "206": ("octagon", WHITE, RED),  # stop
    "205": ("triangle_down", RED, WHITE),  # give way
    "301": ("triangle_up", RED, WHITE),  # priority at the next junction
    "103": ("triangle_up", RED, WHITE),  # bend
    "105": ("triangle_up", RED, WHITE),  # double bend
    "306": ("diamond", WHITE, YELLOW),  # priority road
    "307": ("diamond", WHITE, YELLOW),  # end of priority road
    "274": ("circle", RED, WHITE),  # speed limit
    "209": ("circle", WHITE, BLUE),  # mandatory direction
    "214": ("circle", WHITE, BLUE),  # mandatory directions
    "215": ("circle", WHITE, BLUE),  # roundabout
    "350": ("square", WHITE, BLUE),  # pedestrian crossing
}
OTHER_SIGN = ("square", GREY, WHITE)
MATERIALS: dict[str, tuple[RGB, float, float]] = {  # name -> colour, roughness, metallic
    "pole": ((0.6, 0.6, 0.6), 0.5, 0.6),
    "sign_back": ((0.6, 0.6, 0.62), 0.6, 0.5),
    "light_housing": ((0.08, 0.08, 0.08), 0.7, 0.0),
    "light_red": ((0.9, 0.05, 0.05), 0.3, 0.0),
    "light_amber": ((0.95, 0.6, 0.05), 0.3, 0.0),
    "light_green": ((0.05, 0.8, 0.2), 0.3, 0.0),
    "sign_rim_red": (RED, 0.5, 0.0), "sign_rim_white": (WHITE, 0.5, 0.0), "sign_rim_grey": (GREY, 0.5, 0.0),
}
POLE_RADIUS = 0.04  # [m]
POLE_BELOW = 0.3  # posts start this far under the road surface [m]
PLATE_FORWARD = 0.06  # plate in front of the post's axis [m]
FACE_SCALE = 0.8  # the face inside the rim, share of the plate
HOUSING_DEPTH = 0.25  # [m]


def shape(name: str) -> np.ndarray:
    """Outline (n, 2) of a sign shape in the unit square [-0.5, 0.5]^2, counter-clockwise."""
    if name == "octagon":
        a = np.pi / 8 + np.arange(8) * np.pi / 4
        return 0.5 / np.cos(np.pi / 8) * np.column_stack([np.cos(a), np.sin(a)])
    if name == "circle":
        a = np.arange(24) * 2 * np.pi / 24
        return 0.5 * np.column_stack([np.cos(a), np.sin(a)])
    if name == "triangle_up":
        return np.array([[-0.5, -0.5], [0.5, -0.5], [0.0, 0.5]])
    if name == "triangle_down":
        return np.array([[0.0, -0.5], [0.5, 0.5], [-0.5, 0.5]])
    if name == "diamond":
        return np.array([[0.0, -0.5], [0.5, 0.0], [0.0, 0.5], [-0.5, 0.0]])
    return np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]])


def sign_name(signal: etree._Element) -> str:
    """Material name of a sign's face: sign_<country>_<type>[_<subtype>][_<value>]."""
    parts = [signal.get("country") or "XX", signal.get("type") or "unknown"]
    if signal.get("subtype") not in (None, "", "-1"):
        parts.append(signal.get("subtype"))
    if signal.get("value") not in (None, ""):
        parts.append(f"{float(signal.get('value')):g}")
    return "sign_" + "_".join(parts)


class _Soup:
    """Triangles by material: positions, normals and UVs (one per corner)."""

    def __init__(self) -> None:
        self.parts: dict[str, list[list[np.ndarray]]] = {}

    def add(self, material: str, tris: np.ndarray, normals: np.ndarray, uvs: Optional[np.ndarray] = None) -> None:
        """tris (m, 3, 3), normals (m, 3) (one per triangle), uvs (m, 3, 2)."""
        p = self.parts.setdefault(material, [[], [], []])
        p[0].append(tris.reshape(-1, 3))
        p[1].append(np.repeat(normals, 3, axis=0))
        p[2].append(np.zeros((len(tris) * 3, 2)) if uvs is None else uvs.reshape(-1, 2))

    def primitives(self) -> list[Primitive]:
        out = []
        for name, (pos, nrm, uv) in sorted(self.parts.items()):
            p = np.vstack(pos)
            out.append(Primitive(name, p, np.vstack(nrm), np.arange(len(p), dtype=np.uint32), np.vstack(uv)))
        return out


def _polygon(soup: _Soup, material: str, outline: np.ndarray, centre: np.ndarray, across: np.ndarray,
             up: np.ndarray, normal: np.ndarray, size: tuple[float, float], uv_scale: float = 1.0) -> None:
    """A flat convex polygon (outline in the unit square, counter-clockwise seen from the
    front) at centre, spanned by across x width and up x height, facing normal; fan
    triangles from the centre. UVs: the unit-square coordinates x uv_scale + 0.5."""
    pts = centre + outline[:, :1] * size[0] * across + outline[:, 1:] * size[1] * up
    nxt = np.roll(np.arange(len(pts)), -1)
    tris = np.stack([np.broadcast_to(centre, pts.shape), pts, pts[nxt]], axis=1)
    uv2 = outline * uv_scale + 0.5
    uvs = np.stack([np.full_like(uv2, 0.5), uv2, uv2[nxt]], axis=1)
    if np.dot(np.cross(across, up), normal) < 0:  # mirrored frame: keep the front counter-clockwise
        tris, uvs = tris[:, ::-1], uvs[:, ::-1]
    soup.add(material, tris, np.tile(normal, (len(tris), 1)), uvs)


def _prism(soup: _Soup, material: str, base: np.ndarray, top: float, radius: float, sides: int = 8) -> None:
    """A vertical post with radius from base up to height top (z), sides and a cap."""
    a = np.arange(sides) * 2 * np.pi / sides
    ring = np.column_stack([np.cos(a), np.sin(a), np.zeros(sides)]) * radius
    lo, hi = base + ring, base + ring + [0.0, 0.0, top - base[2]]
    nxt = np.roll(np.arange(sides), -1)
    side_tris = np.concatenate([np.stack([lo, lo[nxt], hi[nxt]], axis=1), np.stack([lo, hi[nxt], hi], axis=1)])
    mid = np.column_stack([np.cos(a + np.pi / sides), np.sin(a + np.pi / sides), np.zeros(sides)])
    soup.add(material, side_tris, np.vstack([mid, mid]))
    cap = np.stack([np.broadcast_to(hi.mean(axis=0), hi.shape), hi, hi[nxt]], axis=1)
    soup.add(material, cap, np.tile([0.0, 0.0, 1.0], (sides, 1)))


def _box(soup: _Soup, material: str, centre: np.ndarray, axes: list[np.ndarray], half: tuple[float, ...]) -> None:
    """A box at centre with orthonormal axes (3) and half sizes (3), faces outward."""
    for k in range(3):
        for sign in (-1.0, 1.0):
            n = sign * axes[k]
            u, v = axes[(k + 1) % 3] * half[(k + 1) % 3], axes[(k + 2) % 3] * half[(k + 2) % 3]
            c = centre + n * half[k]
            quad = np.array([c - u - v, c + u - v, c + u + v, c - u + v])
            tris = np.stack([quad[[0, 1, 2]], quad[[0, 2, 3]]])
            if np.dot(np.cross(quad[1] - quad[0], quad[2] - quad[0]), n) < 0:
                tris = tris[:, ::-1]
            soup.add(material, tris, np.tile(n, (2, 1)))


def _place(ev: RoadEval, signal: etree._Element) -> tuple[np.ndarray, np.ndarray]:
    """(base point on the road surface (3,), unit normal the face looks along (3,))."""
    s = float(np.clip(float(signal.get("s", 0)), 0.0, ev.length))
    fr = frame(ev, np.array([s]))
    base = fr.at(np.array([float(signal.get("t", 0))]))[0]
    hdg = float(fr.hdg[0]) + (np.pi if signal.get("orientation", "+") != "-" else 0.0)
    hdg += float(signal.get("hOffset", 0) or 0)
    return base, np.array([np.cos(hdg), np.sin(hdg), 0.0])


def is_light(signal: etree._Element) -> bool:
    return signal.get("dynamic") == "yes"


def add_sign(soup: _Soup, ev: RoadEval, signal: etree._Element) -> str:
    """A sign's post, plate (rim, face) and back; returns the face's material name."""
    base, n = _place(ev, signal)
    up = np.array([0.0, 0.0, 1.0])
    across = np.array([-n[1], n[0], 0.0])
    w, h = float(signal.get("width", 0.6) or 0.6), float(signal.get("height", 0.6) or 0.6)
    z0 = float(signal.get("zOffset", 2.0) or 0.0)
    form, rim, _ = SIGNS.get(signal.get("type", ""), OTHER_SIGN)
    outline = shape(form)
    centre = base + up * (z0 + h / 2) + n * PLATE_FORWARD
    face = sign_name(signal)
    rim_material = {RED: "sign_rim_red", WHITE: "sign_rim_white"}.get(rim, "sign_rim_grey")
    _prism(soup, "pole", base - up * POLE_BELOW, base[2] + z0 + h, POLE_RADIUS)
    _polygon(soup, rim_material, outline, centre, across, up, n, (w, h))
    _polygon(soup, face, outline * FACE_SCALE, centre + n * 0.005, across, up, n, (w, h))
    _polygon(soup, "sign_back", outline, centre - n * 0.005, -across, up, -n, (w, h))
    return face


def add_light(soup: _Soup, ev: RoadEval, signal: etree._Element) -> None:
    """A light's post, housing and lamps."""
    base, n = _place(ev, signal)
    up = np.array([0.0, 0.0, 1.0])
    across = np.array([-n[1], n[0], 0.0])
    w, h = float(signal.get("width", 0.3) or 0.3), float(signal.get("height", 0.8) or 0.8)
    z0 = float(signal.get("zOffset", 3.0) or 0.0)
    _prism(soup, "pole", base - up * POLE_BELOW, base[2] + z0, 2 * POLE_RADIUS)
    centre = base + up * (z0 + h / 2) + n * (HOUSING_DEPTH / 2)
    _box(soup, "light_housing", centre, [across, up, n], (w / 2, h / 2, HOUSING_DEPTH / 2))
    lamps = ["light_red", "light_amber", "light_green"] if signal.get("type") == "1000001" else \
        ["light_red", "light_green"]
    r = min(w * 0.35, h / (2 * len(lamps) + 1))
    for k, lamp in enumerate(lamps):
        y = h / 2 - (k + 0.5) * h / len(lamps)
        _polygon(soup, lamp, shape("circle"), centre + n * (HOUSING_DEPTH / 2 + 0.005) + up * y, across, up, n,
                 (2 * r, 2 * r))


def signal_primitives(root: etree._Element, evals: Optional[dict[str, RoadEval]] = None
                      ) -> tuple[list[Primitive], list[Material]]:
    """Every signal of the .xodr as primitives (one per material), and those materials."""
    soup = _Soup()
    faces: dict[str, RGB] = {}
    signs = lights = 0
    for road in root.findall("road"):
        signals = road.findall("signals/signal")
        if not signals:
            continue
        ev = evals.get(road.get("id")) if evals is not None else None
        ev = ev if ev is not None else RoadEval(road)
        if ev.length <= 1e-6:
            continue
        for signal in signals:
            if is_light(signal):
                add_light(soup, ev, signal)
                lights += 1
            else:
                faces[add_sign(soup, ev, signal)] = SIGNS.get(signal.get("type", ""), OTHER_SIGN)[2]
                signs += 1
    prims = soup.primitives()
    used = {p.material for p in prims}
    materials = [Material(name, color, rough, metal) for name, (color, rough, metal) in MATERIALS.items()
                 if name in used]
    materials += [Material(name, color, 0.5) for name, color in sorted(faces.items())]
    log.info("signals: %d signs (%d kinds), %d lights", signs, len(faces), lights)
    return prims, materials
