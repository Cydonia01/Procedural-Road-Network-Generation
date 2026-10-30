"""Plot a built .xodr: every lane as a polygon coloured by its OpenDRIVE type.

Reads the file alone (any .xodr, whoever built it), with the validators' road
evaluator (roadgen.validate.continuity): per lane section, the lane edges are
sampled every `step` metres from the reference line, the lane offset and the
lane widths. Connecting roads are drawn too, under the normal roads. Road
marks are drawn on the lane borders, broken ones dash by dash, double ones
thicker; curb marks are left out, since the curb lane itself is drawn.
Stop and yield lines (roadMark objects) are drawn across their outline,
crosswalks as light boxes,
signs as markers by code, and, from the sidecar next to the file, a glyph
per junction for its control kind; traffic lights are signals like the
signs (code 1000001).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Union

import numpy as np
from lxml import etree
from matplotlib.collections import LineCollection, PolyCollection
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
MARK_COLOR = "#ffffff"
SIGN_STYLE = {  # DE code -> (marker, face colour)
    "206": ("8", "#d62728"), "205": ("v", "#ffffff"), "301": ("D", "#f2c40f"), "215": ("o", "#1f77b4"),
    "1000001": ("s", "#2ca02c"),  # traffic light
    "274": ("o", "#ffffff"), "306": ("D", "#f2c40f"), "307": ("D", "#7f7f7f"), "350": ("s", "#1f77b4"),
    "103": ("^", "#ff7f0e"), "105": ("^", "#d62728"),
}
CROSSWALK_COLOR = "#f5f5f5"
OTHER_SIGN = ("s", "#999999")
CONTROL_STYLE = {  # junction control -> (marker, colour)
    "signal": ("h", "#d62728"), "priority": ("D", "#f2c40f"), "roundabout": ("o", "#1f77b4"),
    "right_before_left": ("^", "#2ca02c"),
}
MIN_MARK_POINTS = 0.3  # thinnest drawn mark [pt]
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


def mark_lines(road: etree._Element, step: float = 2.0) -> list[tuple[str, float, np.ndarray]]:
    """(mark type, width [m], (n, 2) polyline) of every drawn road mark of a road: each
    mark record over its stretch of the lane's outer border, a broken one per dash."""
    ev = RoadEval(road)
    sections = ev.sections
    bounds = [float(sec.get("s")) for sec in sections] + [ev.length]
    out = []
    for k, sec in enumerate(sections):
        s0, s1 = bounds[k], bounds[k + 1]
        sides = {sign: sorted(sec.findall(f"{side}/lane"), key=lambda lane: abs(int(lane.get("id"))))
                 for side, sign in (("right", -1), ("left", 1))}

        for lane in sec.iter("lane"):
            marks = lane.findall("roadMark")
            starts = [s0 + float(m.get("sOffset", "0")) for m in marks] + [s1]
            for m, a, b in zip(marks, starts, starts[1:]):
                kind = m.get("type", "none")
                if kind in ("none", "curb") or b - a <= 1e-6:
                    continue
                width = float(m.get("width", "0.15"))
                pieces = [(a, b)]
                line = m.find("type/line")
                if kind == "broken" and line is not None:
                    dash, period = float(line.get("length")), float(line.get("length")) + float(line.get("space"))
                    dashes = np.arange(a + float(line.get("sOffset", "0")), b, period)
                    pieces = [(u, min(u + dash, b)) for u in dashes]
                lane_id = int(lane.get("id"))
                for u, v in pieces:
                    ss = np.linspace(u, v, max(2, int(np.ceil((v - u) / step)) + 1))
                    pts = []
                    for s in ss:
                        x, y, hdg = ev.reference(s)
                        t = _border(ev, sides, s0, lane_id, s)
                        pts.append((x - np.sin(hdg) * t, y + np.cos(hdg) * t))
                    out.append((kind, width, np.array(pts)))
    return out


def _road_point(ev: RoadEval, s: float, t: float) -> tuple[float, float]:
    x, y, hdg = ev.reference(s)
    return x - np.sin(hdg) * t, y + np.cos(hdg) * t


def furniture(road: etree._Element) -> tuple[list[np.ndarray], list[np.ndarray], list[tuple[str, float, float]]]:
    """(stop and yield lines as (2, 2) segments, crosswalks as (4, 2) polygons, signs as
    (code, x, y)) of a road."""
    ev = RoadEval(road)
    lines, crosswalks = [], []
    for obj in road.findall("objects/object"):
        s, t = float(obj.get("s")), float(obj.get("t"))
        corners = [float(c.get("v")) for c in obj.findall("outlines/outline/cornerLocal")]
        if not corners:
            continue
        lo, hi = min(corners), max(corners)
        if obj.get("type") == "roadMark":
            lines.append(np.array([_road_point(ev, s, t + v) for v in (lo, hi)]))
        elif obj.get("type") == "crosswalk":
            depth = max((float(m.get("width", "0")) for m in obj.findall("markings/marking")), default=0.0)
            us = [float(c.get("u")) for c in obj.findall("outlines/outline/cornerLocal")]
            half = max(depth / 2, (max(us) - min(us)) / 2 + depth / 2)
            crosswalks.append(np.array([_road_point(ev, s + du, t + v) for du, v in
                                        ((-half, lo), (-half, hi), (half, hi), (half, lo))]))
    signs = [(sig.get("type"), *_road_point(ev, float(sig.get("s")), float(sig.get("t"))))
             for sig in road.findall("signals/signal")]
    return lines, crosswalks, signs


def _border(ev: RoadEval, sides: dict[int, list[etree._Element]], s0: float, lane_id: int, s: float) -> float:
    """t of a lane's outer border (the centre lane's: the lane offset) at station s of a
    section starting at s0, given each side's lanes from the centre outward."""
    t = _offset(ev, s)
    if lane_id:
        sign = 1 if lane_id > 0 else -1
        t += sign * sum(_width(lane, s - s0) for lane in sides[sign][:abs(lane_id)])
    return t


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
    lines: list[tuple[str, float, np.ndarray]] = []
    stop_lines: list[np.ndarray] = []
    crosswalks: list[np.ndarray] = []
    signs: list[tuple[str, float, float]] = []
    starts: dict[str, list[tuple[float, float]]] = {}  # junction id -> its connecting roads' starts
    for road in roads:
        for kind, poly in lane_polygons(road, step):
            polys.setdefault(kind, []).append(poly)
        lines += mark_lines(road, step)
        road_lines, road_crosswalks, road_signs = furniture(road)
        stop_lines += road_lines
        crosswalks += road_crosswalks
        signs += road_signs
        if road.get("junction", "-1") != "-1":
            starts.setdefault(road.get("junction"), []).append(RoadEval(road).reference(0.0)[:2])
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
    if lines:
        points_per_m = scale * 72  # figure inches per metre times points per inch
        widths = [max(MIN_MARK_POINTS, w * points_per_m * (2.5 if "solid solid" in k else 1.0))
                  for k, w, _ in lines]
        ax.add_collection(LineCollection([p for _, _, p in lines], colors=MARK_COLOR, linewidths=widths,
                                         capstyle="butt"))
    if crosswalks:
        ax.add_collection(PolyCollection(crosswalks, facecolors=CROSSWALK_COLOR, edgecolors="none", alpha=0.8,
                                         zorder=3))
    if stop_lines:
        ax.add_collection(LineCollection(stop_lines, colors=MARK_COLOR, linewidths=max(1.0, 0.5 * scale * 72),
                                         capstyle="butt"))
    for code in sorted({c for c, _, _ in signs}):
        marker, color = SIGN_STYLE.get(code, OTHER_SIGN)
        xy = np.array([(x, y) for c, x, y in signs if c == code])
        ax.scatter(xy[:, 0], xy[:, 1], s=12, marker=marker, c=color, edgecolors="black", linewidths=0.3,
                   zorder=4, label="traffic light" if code == "1000001" else f"sign {code}")
    _plot_control(ax, Path(xodr_path), starts)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_axis_off()
    handles, _ = ax.get_legend_handles_labels()
    ax.legend(handles=[Patch(color=LANE_COLORS.get(k, OTHER_COLOR), label=k) for k in sorted(polys)] + handles,
              loc="upper right", fontsize=8, framealpha=0.9)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    log.info("wrote %s (%d lanes, %d mark lines, %d signs)", out, sum(len(ps) for ps in polys.values()),
             len(lines), len(signs))
    return out


def _plot_control(ax, xodr_path: Path, starts: dict[str, list[tuple[float, float]]]) -> None:
    """A glyph per controlled junction at the centre of its connecting roads' starts, from
    the sidecar's junction records (nothing without them)."""
    sidecar = xodr_path.with_suffix(".roadmap.json")
    if not sidecar.exists():
        return
    junctions = json.loads(sidecar.read_text()).get("junctions", [])
    for kind, (marker, color) in CONTROL_STYLE.items():
        xy = [np.mean(starts[str(j["junction"])], axis=0) for j in junctions
              if j["control"] == kind and str(j["junction"]) in starts]
        if xy:
            pts = np.array(xy)
            ax.scatter(pts[:, 0], pts[:, 1], s=40, marker=marker, c=color, edgecolors="black", linewidths=0.5,
                       zorder=5, alpha=0.85, label=kind.replace("_", " "))
