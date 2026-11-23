"""Road marks of a written .xodr as meshes, for the Blender export (kadmos.scene.gltf).

Two sources, both as the .xodr gives them:

- **Lane marks** (<roadMark> of each lane): on the lane's outer border (the
  centre lane's on the lane offset), from the mark's sOffset to the next one
  or the end of the lane section. Each <type>/<line> is a line of its own
  (tOffset beside the border, dashes of ``length`` with ``space`` between,
  from its sOffset); a mark without <type> lines is one line of the mark's
  width, solid or broken (3 m / 9 m) by its type, two lines for double types.
  Type "none" draws nothing.
- **Marking objects** (<object type="roadMark">: stop and yield lines, turn
  arrows): every <marking> is a line of its width through the corners it
  references, in order, dashed by lineLength / spaceLength (solid if
  spaceLength is 0).

Every line is a flat strip ``lift`` above the road surface (reference line,
elevation, superelevation; lane <height> is not added), sampled every
``step``, UVs (s, t) in metres like the lanes. The material is named after
the colour (mark_white, mark_yellow, ...; "standard" is white).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from lxml import etree

from kadmos.scene.gltf import Primitive
from kadmos.scene.roads import Frame, LaneStrip, frame, lane_borders, samples, section_bounds, strip_mesh
from kadmos.validate.continuity import RoadEval

log = logging.getLogger(__name__)

DEFAULT_WIDTH = 0.15  # mark line width without a width attribute [m]
BROKEN = (3.0, 9.0)  # dash and gap of a broken mark without <line> records [m]
DOUBLE_GAP = 0.15  # between the lines of a double mark without <line> records [m]

# OpenDRIVE mark colours -> material colour (RGB in [0, 1]); others are drawn white.
MARK_COLORS = {
    "white": (0.92, 0.92, 0.9), "yellow": (0.95, 0.75, 0.1), "blue": (0.1, 0.3, 0.8),
    "green": (0.1, 0.6, 0.2), "red": (0.8, 0.1, 0.1), "orange": (0.95, 0.5, 0.1),
}


@dataclass
class _Line:
    """One line of a mark: lateral offset from the border, width, dash (0: solid) and gap,
    start of its dash pattern [m]."""

    t_offset: float
    width: float
    length: float
    space: float
    s_offset: float = 0.0


def mark_color(color: Optional[str]) -> str:
    """The material colour name of an OpenDRIVE mark colour."""
    return color if color in MARK_COLORS else "white"


def mark_lines(mark: etree._Element) -> list[_Line]:
    """The lines a <roadMark> draws ([] for type none or unknown types)."""
    kind = mark.get("type", "none")
    width = float(mark.get("width", DEFAULT_WIDTH) or DEFAULT_WIDTH)
    lines = mark.findall("type/line")
    if lines:
        return [_Line(float(ln.get("tOffset", 0)), float(ln.get("width", width) or width),
                      float(ln.get("length", 0)), float(ln.get("space", 0)), float(ln.get("sOffset", 0)))
                for ln in lines]
    parts = kind.split()
    if not parts or any(p not in ("solid", "broken") for p in parts):
        return []
    if len(parts) == 1:
        offsets = [0.0]
        w = width
    else:  # "solid solid", "solid broken", ...: the width covers both lines and the gap
        w = max((width - DOUBLE_GAP) / 2, 0.05) if width > DOUBLE_GAP else DEFAULT_WIDTH
        offsets = [-(w + DOUBLE_GAP) / 2, (w + DOUBLE_GAP) / 2]
    return [_Line(t, w, *(BROKEN if p == "broken" else (0.0, 0.0))) for t, p in zip(offsets, parts)]


def dashes(a: float, b: float, length: float, space: float, start: float = 0.0) -> list[tuple[float, float]]:
    """The drawn stretches of [a, b]: all of it if space <= 0 or length <= 0, else dashes of
    length with space between, the pattern starting at a + start."""
    if b - a <= 1e-6:
        return []
    if space <= 0 or length <= 0:
        return [(a, b)]
    out, s = [], a + start
    while s < b - 1e-6:
        out.append((max(s, a), min(s + length, b)))
        s += length + space
    return [(x, y) for x, y in out if y - x > 1e-6]


def lane_mark_strips(ev: RoadEval, step: float, lift: float) -> list[LaneStrip]:
    """The lane marks of one road as strips (lane_type: the colour name)."""
    out: list[LaneStrip] = []
    if ev.length <= 1e-6:
        return out
    for sec, s0, s1 in section_bounds(ev):
        lanes = [lane for side in ("left", "center", "right") for lane in sec.findall(f"{side}/lane")]
        # (lane id, colour, line, dash samples) of every dash; evaluated together per section
        todo: list[tuple[int, str, _Line, np.ndarray]] = []
        for lane in lanes:
            marks = sorted(lane.findall("roadMark"), key=lambda m: float(m.get("sOffset", 0)))
            for j, mark in enumerate(marks):
                a = s0 + float(mark.get("sOffset", 0))
                b = s0 + float(marks[j + 1].get("sOffset", 0)) if j + 1 < len(marks) else s1
                a, b = max(a, s0), min(b, s1)
                for line in mark_lines(mark):
                    for da, db in dashes(a, b, line.length, line.space, line.s_offset):
                        todo.append((int(lane.get("id")), mark_color(mark.get("color")), line,
                                     samples(da, db, step)))
        if not todo:
            continue
        ss = np.concatenate([d[3] for d in todo])
        fr = frame(ev, ss)
        borders = lane_borders(ev, sec, s0, ss)
        k = 0
        for lane_id, color, line, part in todo:
            sl = slice(k, k + len(part))
            k += len(part)
            if lane_id not in borders:
                continue
            t = borders[lane_id][1][sl] + line.t_offset
            t_in, t_out = t - line.width / 2, t + line.width / 2
            sub = _sub(fr, sl)
            out.append(LaneStrip(ev.id, color, part, t_in, t_out, sub.at(t_in, lift), sub.at(t_out, lift)))
    return out


def _sub(fr: Frame, sl: slice) -> Frame:
    """The frame at samples sl."""
    return Frame(fr.s[sl], fr.xy[sl], fr.normal[sl], fr.hdg[sl], fr.z[sl], fr.bank[sl])


def object_mark_strips(ev: RoadEval, step: float, lift: float) -> list[LaneStrip]:
    """The marking objects (<object type="roadMark">) of one road as strips: each marked
    segment between referenced corners is a strip of the marking's width."""
    out: list[LaneStrip] = []
    for obj in ev.road.findall("objects/object"):
        if obj.get("type") != "roadMark":
            continue
        s_o, t_o, hdg = float(obj.get("s", 0)), float(obj.get("t", 0)), float(obj.get("hdg", 0))
        corners = {c.get("id"): (float(c.get("u", 0)), float(c.get("v", 0)))
                   for c in obj.iter("cornerLocal")}
        rot = np.array([[np.cos(hdg), -np.sin(hdg)], [np.sin(hdg), np.cos(hdg)]])
        for marking in obj.iter("marking"):
            refs = [corners[r.get("id")] for r in marking.findall("cornerReference") if r.get("id") in corners]
            width = float(marking.get("width", DEFAULT_WIDTH))
            length, space = float(marking.get("lineLength", 0)), float(marking.get("spaceLength", 0))
            color = mark_color(marking.get("color"))
            for p, q in zip(refs, refs[1:]):
                # the segment in (s, t), about the object's position
                a = np.array([s_o, t_o]) + rot @ np.array(p)
                b = np.array([s_o, t_o]) + rot @ np.array(q)
                seg = float(np.hypot(*(b - a)))
                if seg < 1e-6:
                    continue
                d = (b - a) / seg
                side = np.array([-d[1], d[0]]) * width / 2
                for da, db in dashes(0.0, seg, length, space):
                    f = samples(da, db, step)
                    centre = a + f[:, None] * d
                    inner, outer = centre - side, centre + side
                    for pts in (inner, outer):  # a line at the road's very start or end
                        pts[:, 0] = np.clip(pts[:, 0], 0.0, ev.length)
                    pi, po = _on_road(ev, inner, lift), _on_road(ev, outer, lift)
                    out.append(LaneStrip(ev.id, color, centre[:, 0], inner[:, 1], outer[:, 1], pi, po))
    return out


def _on_road(ev: RoadEval, st: np.ndarray, lift: float) -> np.ndarray:
    """World points (n, 3) of (s, t) points (n, 2), lift above the banked surface."""
    return frame(ev, st[:, 0]).at(st[:, 1], lift)


def mark_primitives(root: etree._Element, step: float, lift: float,
                    evals: Optional[dict[str, RoadEval]] = None) -> list[Primitive]:
    """One Primitive per mark colour (material mark_<colour>) with every lane mark and
    marking object of the .xodr."""
    by_color: dict[str, list[LaneStrip]] = {}
    for road in root.findall("road"):
        ev = evals.get(road.get("id")) if evals is not None else None
        ev = ev if ev is not None else RoadEval(road)
        for st in [*lane_mark_strips(ev, step, lift), *object_mark_strips(ev, step, lift)]:
            by_color.setdefault(st.lane_type, []).append(st)
    out = []
    for color, strips in sorted(by_color.items()):
        pos, nrm, uv, idx = strip_mesh(strips)
        if len(idx):
            out.append(Primitive(f"mark_{color}", pos, nrm, idx, uv))
    log.info("road marks: %s", ", ".join(f"{p.material} {len(p.indices) // 3} triangles" for p in out) or "none")
    return out
