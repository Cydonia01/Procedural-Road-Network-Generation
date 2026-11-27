"""Road surface mesh of a written .xodr, for the Blender export (kadmos.scene.gltf).

esmini builds its road mesh from the .xodr itself; a .glb needs our own. Every
lane of every road and connecting road becomes a strip between its inner and
outer border, sampled every ``step`` along the road and at both ends of each
lane section, on the reference line, lane offset, widths, elevation,
superelevation and lane <height> as the .xodr gives them (the same evaluation
as kadmos.scene.surface). Lanes of type "none" (0 m wide in this project) are
left out. UVs are (s, t) in metres, so a tiling texture keeps its scale along
and across the road; the material is named after the lane type. A junction's
connecting roads overlap by design; their lanes are merged into one flat
surface on the junction's plane (UVs (x, y)), so no two surfaces coincide.

Nothing here reads the RoadGraph: the .xodr is the product, and the scene
follows it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import shapely
from lxml import etree
from shapely.geometry import Polygon

from kadmos.config import DEFAULTS, section
from kadmos.scene.gltf import Primitive
from kadmos.validate.continuity import Records, RoadEval

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "gltf")
PLANE_TOLERANCE = 0.05  # junction lanes further than this from one plane stay strips [m]
GAP_CLOSING = 0.05  # gaps between a junction's lanes narrower than twice this are closed [m]


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


@dataclass
class Frame:
    """The road at samples s (n,): reference points (n, 2), left normals (n, 2), headings
    (n,), elevation (n,), sin(superelevation) (n,)."""

    s: np.ndarray
    xy: np.ndarray
    normal: np.ndarray
    hdg: np.ndarray
    z: np.ndarray
    bank: np.ndarray

    def at(self, t: np.ndarray, h: Union[np.ndarray, float] = 0.0) -> np.ndarray:
        """Points (n, 3) at offset t from the reference line, on the banked surface, raised by h."""
        return np.column_stack([self.xy + self.normal * t[:, None], self.z + t * self.bank + h])


def frame(ev: RoadEval, s: np.ndarray) -> Frame:
    """The road's frame at every s."""
    ref = ev.references(s)
    return Frame(s, ref[:, :2], np.column_stack([-np.sin(ref[:, 2]), np.cos(ref[:, 2])]), ref[:, 2],
                 ev.elevation_records.values(s), np.sin(ev.superelevation_records.values(s)))


def section_bounds(ev: RoadEval) -> list[tuple[etree._Element, float, float]]:
    """(lane section, start s, end s) of every section longer than 0."""
    sections = ev.sections
    bounds = [float(sec.get("s")) for sec in sections] + [ev.length]
    return [(sec, bounds[k], bounds[k + 1]) for k, sec in enumerate(sections) if bounds[k + 1] - bounds[k] > 1e-6]


def lane_borders(ev: RoadEval, sec: etree._Element, s0: float, s: np.ndarray
                 ) -> dict[int, tuple[np.ndarray, np.ndarray, etree._Element]]:
    """Per lane id of a section starting at s0: (inner t, outer t, lane element) at every s;
    the centre lane 0 has both on the lane offset."""
    offset = ev.offset_records.values(s)
    centre = sec.find("center/lane")
    out = {0: (offset, offset, centre)} if centre is not None else {}
    for side, sign in (("right", -1.0), ("left", 1.0)):
        t = offset
        for lane in sorted(sec.findall(f"{side}/lane"), key=lambda e: abs(int(e.get("id")))):
            w = Records(lane.findall("width"), "sOffset").values(s - s0)
            t_in, t = t, t + sign * w
            out[int(lane.get("id"))] = (t_in, t, lane)
    return out


def samples(a: float, b: float, step: float) -> np.ndarray:
    """a to b, both ends included, at most step apart."""
    return np.linspace(a, b, max(2, int(np.ceil((b - a) / step)) + 1))


def lane_strips(ev: RoadEval, step: float) -> list[LaneStrip]:
    """The lane strips of one road (lanes of type "none" and lanes 0 m wide throughout
    left out)."""
    out: list[LaneStrip] = []
    if ev.length <= 1e-6:
        return out
    for sec, s0, s1 in section_bounds(ev):
        ss = samples(s0, s1, step)
        fr = frame(ev, ss)
        for lane_id, (t_in, t_out, lane) in lane_borders(ev, sec, s0, ss).items():
            kind = lane.get("type", "none")
            if lane_id == 0 or kind == "none" or not np.any(np.abs(t_out - t_in) > 1e-6):
                continue
            h_in, h_out = _heights(lane.findall("height"), ss - s0)
            out.append(LaneStrip(ev.id, kind, ss, t_in, t_out, fr.at(t_in, h_in), fr.at(t_out, h_out)))
    return out


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
    strips as one mesh, every triangle facing up (each on its own: a lane's width
    polynomial may dip below 0), degenerate ones (tapers to 0 m) left out."""
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
        down = cross[:, 2] < 0  # left lanes (borders the other way round), or a width below 0
        tris[down] = tris[down][:, ::-1]
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


def junction_surface(strips: list[LaneStrip], tolerance: float = PLANE_TOLERANCE
                     ) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """The union of a junction's lane strips (one material) as one flat mesh on the plane
    through them: (positions, normals, uvs (x, y), indices), or None where the strips are
    further than tolerance from a plane (they are then drawn as strips). Connecting roads
    overlap by design; drawn one by one, their coincident surfaces flicker and shade
    each other in a renderer."""
    pts = np.vstack([np.vstack([st.inner, st.outer]) for st in strips])
    centre = pts[:, :2].mean(axis=0)
    a = np.column_stack([pts[:, :2] - centre, np.ones(len(pts))])
    coef = np.linalg.lstsq(a, pts[:, 2], rcond=None)[0]
    if np.max(np.abs(a @ coef - pts[:, 2])) > tolerance:
        return None
    polys = []
    for st in strips:
        poly = Polygon(np.vstack([st.inner[:, :2], st.outer[::-1, :2]]))
        poly = poly if poly.is_valid else poly.buffer(0)
        if not poly.is_empty and poly.area > 1e-6:
            polys.append(poly)
    # closing: hairline gaps between neighbouring lanes would show the background through
    union = (shapely.union_all([q.buffer(GAP_CLOSING, join_style="mitre") for q in polys])
             .buffer(-GAP_CLOSING, join_style="mitre") if polys else Polygon())
    tris = [t for t in getattr(shapely.constrained_delaunay_triangles(union), "geoms", [])
            if t.area > 1e-8] if not union.is_empty else []
    if not tris:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 2)), np.zeros(0, np.uint32)
    xy = np.array([list(t.exterior.coords)[:3] for t in tris])  # (m, 3, 2)
    d1, d2 = xy[:, 1] - xy[:, 0], xy[:, 2] - xy[:, 0]
    ccw = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0] > 0
    xy[~ccw] = xy[~ccw][:, ::-1]
    xy = xy.reshape(-1, 2)
    z = np.column_stack([xy - centre, np.ones(len(xy))]) @ coef
    n = np.array([-coef[0], -coef[1], 1.0])
    n /= np.linalg.norm(n)
    return (np.column_stack([xy, z]), np.tile(n, (len(xy), 1)), xy.copy(),
            np.arange(len(xy), dtype=np.uint32))


def road_primitives(xodr: Union[str, Path, etree._Element], step: float = _D["step"],
                    known: Optional[set[str]] = None,
                    evals: Optional[dict[str, RoadEval]] = None) -> list[Primitive]:
    """Primitives per lane material covering every lane of the .xodr (path or parsed
    root): the roads' lanes as strips (UVs (s, t)), each junction's connecting-road lanes
    merged into one flat surface (UVs (x, y); strips where a junction is not planar).
    known: lane types with a material of their own (default: gltf.lane_colors); evals:
    the roads' evaluators by id, if the caller has them."""
    root = xodr if isinstance(xodr, etree._Element) else etree.parse(str(xodr)).getroot()
    known = set(_D["lane_colors"]) if known is None else known
    by_material: dict[str, list[LaneStrip]] = {}
    junctions: dict[tuple[str, str], list[LaneStrip]] = {}
    for road in root.findall("road"):
        ev = evals.get(road.get("id")) if evals is not None else None
        junction = road.get("junction", "-1")
        for st in lane_strips(ev if ev is not None else RoadEval(road), step):
            material = lane_material(st.lane_type, known)
            if junction == "-1":
                by_material.setdefault(material, []).append(st)
            else:
                junctions.setdefault((material, junction), []).append(st)
    out = []
    for material, strips in sorted(by_material.items()):
        pos, nrm, uv, idx = strip_mesh(strips)
        if len(idx):
            out.append(Primitive(material, pos, nrm, idx, uv))
    merged: dict[str, list[tuple[np.ndarray, ...]]] = {}
    kept = 0
    for (material, _), strips in sorted(junctions.items()):
        mesh = junction_surface(strips)
        if mesh is None:  # not planar: strips, as the roads
            mesh = strip_mesh(strips)
            kept += 1
        if len(mesh[3]):
            merged.setdefault(material, []).append(mesh)
    for material, meshes in sorted(merged.items()):
        offsets = np.cumsum([0] + [len(m[0]) for m in meshes[:-1]])
        out.append(Primitive(material, np.vstack([m[0] for m in meshes]), np.vstack([m[1] for m in meshes]),
                             np.concatenate([m[3] + o for m, o in zip(meshes, offsets)]).astype(np.uint32),
                             np.vstack([m[2] for m in meshes])))
    log.info("road mesh: %s; %d junction surfaces (%d not planar, as strips)",
             ", ".join(f"{p.material} {len(p.indices) // 3} triangles" for p in out) or "empty",
             len(junctions), kept)
    return out
