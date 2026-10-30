"""Plot a built .xodr: every lane as a polygon coloured by its OpenDRIVE type.

Reads the file alone (any .xodr, whoever built it), with the validators' road
evaluator (roadgen.validate.continuity): per lane section, the lane edges are
sampled every `step` metres from the reference line, the lane offset and the
lane widths. Connecting roads are drawn too, under the normal roads. Later
stage B phases add road marks, signals and objects here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

import numpy as np
from lxml import etree
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from roadgen.validate.continuity import RoadEval, _piecewise, _poly

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

LANE_COLORS = {  # OpenDRIVE lane type -> colour
    "driving": "#4d4d4d",
    "median": "#5a8f3c",
    "parking": "#6b7a99",
    "biking": "#c0504d",
    "curb": "#111111",
    "sidewalk": "#c9c4b8",
    "shoulder": "#9c8b6e",
    "border": "#7fa86a",
}
OTHER_COLOR = "#b04fb0"  # lane types without a colour above
MAX_PIXELS = 4000  # longest side of the image [px]


def lane_polygons(road: etree._Element, step: float = 2.0) -> list[tuple[str, np.ndarray]]:
    """(lane type, (n, 2) outline) of every lane of a road, per lane section."""
    ev = RoadEval(road)
    sections = ev.sections
    bounds = [float(sec.get("s")) for sec in sections] + [ev.length]
    out = []
    for k, sec in enumerate(sections):
        s0, s1 = bounds[k], bounds[k + 1]
        if s1 - s0 <= 1e-6:
            continue
        ss = np.linspace(s0, s1, max(2, int(np.ceil((s1 - s0) / step)) + 1))
        ref = np.array([ev.reference(s) for s in ss])
        offset = np.array([_offset(ev, s) for s in ss])
        normal = np.column_stack([-np.sin(ref[:, 2]), np.cos(ref[:, 2])])
        for side, sign in (("right", -1.0), ("left", 1.0)):
            lanes = sorted(sec.findall(f"{side}/lane"), key=lambda lane: abs(int(lane.get("id"))))
            inner = offset.copy()
            for lane in lanes:
                widths = np.array([_width(lane, s - s0) for s in ss])
                outer = inner + sign * widths
                if np.max(widths) > 1e-6:
                    a = ref[:, :2] + normal * inner[:, None]
                    b = ref[:, :2] + normal * outer[:, None]
                    out.append((lane.get("type", "none"), np.vstack([a, b[::-1]])))
                inner = outer
    return out


def _offset(ev: RoadEval, s: float) -> float:
    rec = _piecewise(ev.offsets, s, "s")
    return _poly(rec, s - float(rec.get("s"))) if rec is not None else 0.0


def _width(lane: etree._Element, ds: float) -> float:
    rec = _piecewise(lane.findall("width"), ds, "sOffset")
    return _poly(rec, ds - float(rec.get("sOffset"))) if rec is not None else 0.0


def plot_xodr(xodr_path: PathLike, out_path: PathLike, step: float = 2.0) -> Path:
    """Draw the lanes of an .xodr into an image; return its path."""
    root = etree.parse(str(xodr_path)).getroot()
    roads = root.findall("road")
    # Connecting roads first, so the normal roads' lanes lie on top of them.
    roads.sort(key=lambda r: r.get("junction", "-1") == "-1")
    polys: dict[str, list[np.ndarray]] = {}
    for road in roads:
        for kind, poly in lane_polygons(road, step):
            polys.setdefault(kind, []).append(poly)
    if not polys:
        raise ValueError(f"{xodr_path}: no lanes to draw")
    pts = np.vstack([p for ps in polys.values() for p in ps])
    (x0, y0), (x1, y1) = pts.min(axis=0), pts.max(axis=0)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    dpi = 100
    scale = MAX_PIXELS / dpi / max(w, h)
    fig = Figure(figsize=(max(w * scale, 4.0), max(h * scale, 4.0)), dpi=dpi)
    ax = fig.add_subplot()
    order = ["driving", "median", "shoulder", "parking", "biking", "border", "curb", "sidewalk"]
    for kind in sorted(polys, key=lambda k: order.index(k) if k in order else len(order)):
        ax.add_collection(PolyCollection(polys[kind], facecolors=LANE_COLORS.get(kind, OTHER_COLOR),
                                         edgecolors="none", linewidths=0))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.legend(handles=[Patch(color=LANE_COLORS.get(k, OTHER_COLOR), label=k) for k in sorted(polys)],
              loc="upper right", fontsize=8, framealpha=0.9)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    log.info("wrote %s (%d lanes)", out, sum(len(ps) for ps in polys.values()))
    return out
