"""Road surface mesh of a written .xodr, for the Blender export (kadmos.scene.gltf).

esmini builds its road mesh from the .xodr itself; a .glb needs our own. Every
lane of every road and connecting road becomes a strip between its inner and
outer border, sampled every ``step`` along the road and at both ends of each
lane section, on the reference line, lane offset, widths, elevation,
superelevation and lane <height> as the .xodr gives them (the same evaluation
as kadmos.scene.surface). Lanes of type "none" (0 m wide in this project) are
left out. UVs are (s, t) in metres, so a tiling texture keeps its scale along
and across the road; the material is named after the lane type.

Nothing here reads the RoadGraph: the .xodr is the product, and the scene
follows it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
from lxml import etree

from kadmos.config import DEFAULTS, section
from kadmos.scene.gltf import Primitive
from kadmos.validate.continuity import Records, RoadEval

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "gltf")


@dataclass
class LaneStrip:
    """One lane of one lane section: its borders (n, 3) at s (n,), offsets t [m]."""

    road: str
    lane_type: str
    s: np.ndarray
    t_inner: np.ndarray
    t_outer: np.ndarray
    inner: np.ndarray
    outer: np.ndarray


def lane_strips(ev: RoadEval, step: float) -> list[LaneStrip]:
    """The lane strips of one road (lanes of type "none" and lanes 0 m wide throughout
    left out)."""
    out: list[LaneStrip] = []
    if ev.length <= 1e-6:
        return out
    sections = ev.sections
    bounds = [float(sec.get("s")) for sec in sections] + [ev.length]
    for k, sec in enumerate(sections):
        s0, s1 = bounds[k], bounds[k + 1]
        if s1 - s0 <= 1e-6:
            continue
        ss = np.linspace(s0, s1, max(2, int(np.ceil((s1 - s0) / step)) + 1))
        ref = ev.references(ss)
        frame = (ref[:, :2], np.column_stack([-np.sin(ref[:, 2]), np.cos(ref[:, 2])]),
                 ev.elevation_records.values(ss), np.sin(ev.superelevation_records.values(ss)))
        offset = ev.offset_records.values(ss)
        for side, sign in (("right", -1.0), ("left", 1.0)):
            t = offset.copy()
            for lane in sorted(sec.findall(f"{side}/lane"), key=lambda e: abs(int(e.get("id")))):
                w = Records(lane.findall("width"), "sOffset").values(ss - s0)
                t_in, t = t, t + sign * w
                kind = lane.get("type", "none")
                if kind == "none" or not np.any(w > 1e-6):
                    continue
                h_in, h_out = _heights(lane.findall("height"), ss - s0)
                out.append(LaneStrip(ev.id, kind, ss, t_in, t, _border(frame, t_in, h_in), _border(frame, t, h_out)))
    return out


def _border(frame: tuple[np.ndarray, ...], t: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Points (n, 3) at offset t from the reference line, on the banked surface, raised by h.
    frame: reference points (n, 2), left normals (n, 2), elevation (n,), sin(superelevation) (n,)."""
    xy, normal, z, bank = frame
    return np.column_stack([xy + normal * t[:, None], z + t * bank + h])


def _heights(records: list[etree._Element], ds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inner and outer lane <height> (piecewise constant) at every ds; zeros without records."""
    if not records:
        return np.zeros(len(ds)), np.zeros(len(ds))
    starts = np.array([float(r.get("sOffset", "0")) for r in records])
    k = np.maximum(np.searchsorted(starts, ds + 1e-9, side="right") - 1, 0)
    inner = np.array([float(r.get("inner", "0")) for r in records])
    outer = np.array([float(r.get("outer", "0")) for r in records])
    return inner[k], outer[k]


def strip_mesh(strips: list[LaneStrip]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(positions (n, 3), normals (n, 3), uvs (n, 2) = (s, t), indices (3 m,)) of the
    strips as one mesh, every triangle facing up, degenerate ones (tapers to 0 m) left out."""
    pos, uv, idx = [], [], []
    base = 0
    for st in strips:
        n = len(st.s)
        v = np.empty((2 * n, 3))
        v[0::2], v[1::2] = st.inner, st.outer
        tex = np.empty((2 * n, 2))
        tex[0::2, 0] = tex[1::2, 0] = st.s
        tex[0::2, 1], tex[1::2, 1] = st.t_inner, st.t_outer
        i = 2 * np.arange(n - 1)
        tris = np.concatenate([np.column_stack([i, i + 1, i + 3]), np.column_stack([i, i + 3, i + 2])])
        a, b, c = v[tris[:, 0]], v[tris[:, 1]], v[tris[:, 2]]
        cross = np.cross(b - a, c - a)
        keep = np.linalg.norm(cross, axis=1) > 1e-8
        tris, cross = tris[keep], cross[keep]
        if not len(tris):
            continue
        if cross[:, 2].sum() < 0:  # left lanes: borders run the other way round
            tris = tris[:, ::-1]
        pos.append(v)
        uv.append(tex)
        idx.append(tris + base)
        base += len(v)
    if not pos:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 2)), np.zeros(0, np.uint32)
    p, t = np.vstack(pos), np.vstack(uv)
    tris = np.vstack(idx)
    return p, vertex_normals(p, tris), t, tris.ravel().astype(np.uint32)


def vertex_normals(p: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Area-weighted mean of the faces' normals at every vertex; up where none."""
    face = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
    acc = np.zeros_like(p)
    for k in range(3):
        np.add.at(acc, tris[:, k], face)
    length = np.linalg.norm(acc, axis=1)
    acc[length < 1e-12] = (0.0, 0.0, 1.0)
    length[length < 1e-12] = 1.0
    return acc / length[:, None]


def lane_material(lane_type: str, known: set[str]) -> str:
    """Material name of a lane type: lane_<type>, or lane_other for types without a colour."""
    return f"lane_{lane_type}" if lane_type in known else "lane_other"


def road_primitives(xodr: Union[str, Path, etree._Element], step: float = _D["step"],
                    known: Optional[set[str]] = None,
                    evals: Optional[dict[str, RoadEval]] = None) -> list[Primitive]:
    """One Primitive per lane material, covering every lane of the .xodr (path or parsed
    root). known: lane types with a material of their own (default: gltf.lane_colors);
    evals: the roads' evaluators by id, if the caller has them."""
    root = xodr if isinstance(xodr, etree._Element) else etree.parse(str(xodr)).getroot()
    known = set(_D["lane_colors"]) if known is None else known
    by_material: dict[str, list[LaneStrip]] = {}
    for road in root.findall("road"):
        ev = evals.get(road.get("id")) if evals is not None else None
        for st in lane_strips(ev if ev is not None else RoadEval(road), step):
            by_material.setdefault(lane_material(st.lane_type, known), []).append(st)
    out = []
    for material, strips in sorted(by_material.items()):
        pos, nrm, uv, idx = strip_mesh(strips)
        if len(idx):
            out.append(Primitive(material, pos, nrm, idx, uv))
    log.info("road mesh: %s", ", ".join(f"{p.material} {len(p.indices) // 3} triangles" for p in out) or "empty")
    return out
