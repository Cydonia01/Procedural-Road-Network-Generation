"""Report figures from a benchmark results.csv (PNG + PDF each).

1. rtf_vs_vehicles: real-time factor vs vehicle count, one line per grid
   size, with the RTF = 1 boundary and where each grid crosses it.
2. load_and_size_vs_junctions: esmini load time (per vehicle count) and
   .xodr file size vs number of junctions, as two panels (never two y-axes).
3. wall_vs_work: simulation wall time vs vehicles x junctions, with the
   real-time boundary (wall time = simulated time).

Points are means across seeds with +-1 standard deviation error bars. Only
"ok" rows are plotted; timeouts and memory kills are drawn as markers on the
lower edge of the RTF figure so the limit stays visible.
"""

from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Iterable, Optional, Union

from matplotlib.figure import Figure
from matplotlib.lines import Line2D

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Reference palette (light mode). Categorical slots in fixed order; the blue
# ramp is ordinal (no step lighter than 250 so every mark clears 2:1).
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE_RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
             "#1c5cab", "#184f95", "#104281", "#0d366b"]  # steps 250..700
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
LINE_W, MARKER_SIZE = 1.5, 6


def load_results(path: PathLike) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _num(row: dict, key: str) -> Optional[float]:
    try:
        v = float(row[key])
    except (KeyError, TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _grid_order(rows: Iterable[dict]) -> list[str]:
    sizes = {r["grid"]: int(r["n_avenues"]) * int(r["n_streets"]) for r in rows}
    return sorted(sizes, key=sizes.get)


def _agg(values: list[float]) -> tuple[float, float]:
    return mean(values), (stdev(values) if len(values) > 1 else 0.0)


def _style(ax, xlabel: str, ylabel: str, title: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8, which="both")
    ax.grid(True, which="major", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)


def _fit_x(ax, xs: Iterable[float], right_pad: float = 1.6) -> None:
    """Log x-range from the data (matplotlib otherwise keeps decades of empty axis)."""
    xs = [x for x in xs if x and x > 0]
    if xs:
        ax.set_xlim(min(xs) / 1.6, max(xs) * right_pad)


def power_law(points: list[tuple[float, float]]) -> Optional[tuple[float, float]]:
    """Least-squares fit y = a * x^b in log-log space; returns (a, b)."""
    pts = [(math.log(x), math.log(y)) for x, y in points if x > 0 and y > 0]
    if len(pts) < 2 or len({x for x, _ in pts}) < 2:
        return None
    mx = mean(x for x, _ in pts)
    my = mean(y for _, y in pts)
    b = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
    return math.exp(my - b * mx), b


def _draw_fit(ax, points: list[tuple[float, float]], label: str, min_x: float = 0.0) -> None:
    """Thin muted power-law fit through points with x >= min_x, labelled with its exponent."""
    fit = power_law([(x, y) for x, y in points if x >= min_x])
    if not fit:
        return
    a, b = fit
    xs = sorted(x for x, _ in points if x >= min_x)
    ax.plot([xs[0], xs[-1]], [a * xs[0] ** b, a * xs[-1] ** b], color=MUTED, linewidth=1.0,
            linestyle=(0, (1, 2)), zorder=2)
    ax.text(0.98, 0.04, f"fit: {label}^{b:.2f}", transform=ax.transAxes, ha="right",
            va="bottom", fontsize=8, color=INK_2)


def _figure(width: float = 7.5, height: float = 4.6, ncols: int = 1) -> tuple[Figure, list]:
    fig = Figure(figsize=(width, height), facecolor=SURFACE, layout="constrained")
    axes = fig.subplots(1, ncols)
    return fig, list(axes) if ncols > 1 else [axes]


def _save(fig: Figure, out_dir: Path, name: str) -> list[Path]:
    paths = []
    for ext in ("png", "pdf"):
        p = out_dir / f"{name}.{ext}"
        fig.savefig(p, dpi=200, facecolor=SURFACE, bbox_inches="tight")
        paths.append(p)
    return paths


def crossing(points: list[tuple[float, float]], level: float = 1.0) -> Optional[float]:
    """x where y first drops below level, log-log interpolated; None if never."""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if y0 >= level > y1:
            lx0, lx1, ly0, ly1 = map(math.log, (x0, x1, y0, y1))
            return math.exp(lx0 + (math.log(level) - ly0) * (lx1 - lx0) / (ly1 - ly0))
    return None


def _rtf_panel(ax, rows: list[dict], grids: list[str], key: str, title: str) -> list[tuple[str, str]]:
    """One RTF-vs-vehicles panel; returns (grid, where it goes below RTF = 1)."""
    _style(ax, "vehicles", "real-time factor (simulated s / wall s)", title)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ok = [r for r in rows if r["status"] == "ok" and _num(r, key)]
    crossings = []
    for k, grid in enumerate(grids):
        by_n = defaultdict(list)
        bound = defaultdict(bool)
        for r in ok:
            if r["grid"] == grid:
                n = int(r["n_vehicles"])
                by_n[n].append(_num(r, key))
                bound[n] |= key == "rtf" and r.get("rtf_lower_bound") == "1"
        if not by_n:
            continue
        xs = sorted(by_n)
        stats = [_agg(by_n[x]) for x in xs]
        color, marker = CATEGORICAL[k % len(CATEGORICAL)], MARKERS[k % len(MARKERS)]
        ax.errorbar(xs, [m for m, _ in stats], yerr=[sd for _, sd in stats], color=color,
                    marker=marker, markersize=MARKER_SIZE, linewidth=LINE_W, capsize=2.5,
                    elinewidth=0.9, markeredgecolor=SURFACE, markeredgewidth=0.8, zorder=3)
        # Hollow markers: stepping too fast to time, so the value is a lower bound.
        hollow = [(x, m) for x, (m, _) in zip(xs, stats) if bound[x]]
        if hollow:
            ax.plot(*zip(*hollow), linestyle="", marker=marker, markersize=MARKER_SIZE,
                    markerfacecolor=SURFACE, markeredgecolor=color, markeredgewidth=1.2, zorder=4)
        ax.annotate(grid, (xs[-1], stats[-1][0]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8, color=INK_2)
        means = [(x, m) for x, (m, _) in zip(xs, stats)]
        x_cross = crossing(means)
        if x_cross:
            crossings.append((grid, f"~{x_cross:.0f} vehicles"))
            ax.plot(x_cross, 1.0, marker="|", markersize=10, color=color, zorder=5)
        elif means[0][1] < 1.0:
            crossings.append((grid, f"already at {means[0][0]} vehicles"))
    return crossings


def plot_rtf_vs_vehicles(rows: list[dict], out_dir: Path) -> list[Path]:
    """Two panels on one shared log scale: stepping only, and end to end (incl. load)."""
    fig, axes = _figure(width=11.0, height=4.8, ncols=2)
    grids = _grid_order(rows)
    panels = [
        ("rtf", "Stepping only (after the network is loaded)"),
        ("rtf_total", "End to end (load + stepping)"),
    ]
    all_crossings = {}
    for ax, (key, title) in zip(axes, panels):
        all_crossings[key] = _rtf_panel(ax, rows, grids, key, title)

    # Shared y-range so the panels compare directly; real-time boundary on both.
    lo = min(min(ax.get_ylim()[0] for ax in axes), 0.3)
    hi = max(ax.get_ylim()[1] for ax in axes)
    counts = sorted({int(r["n_vehicles"]) for r in rows if r["status"] in ("ok", "timeout", "memory")})
    for ax in axes:
        ax.set_ylim(lo, hi)
        _fit_x(ax, counts, right_pad=2.8)  # room on the right for the direct labels
        ax.axhspan(lo, 1.0, color=GRID, alpha=0.45, zorder=0, linewidth=0)
        ax.axhline(1.0, color=INK_2, linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)
        ax.annotate("real time (RTF = 1)", (ax.get_xlim()[0], 1.0), xytext=(4, 4),
                    textcoords="offset points", fontsize=8, color=INK_2)
        ax.annotate("slower than real time", (ax.get_xlim()[0], lo), xytext=(4, 4),
                    textcoords="offset points", fontsize=8, color=MUTED)

    # Runs that never finished: timeout / memory, on the bottom edge of both panels.
    killed = [r for r in rows if r["status"] in ("timeout", "memory")]
    for ax in axes:
        for r in killed:
            k = grids.index(r["grid"])
            ax.plot(int(r["n_vehicles"]), lo, marker="x", color=CATEGORICAL[k % len(CATEGORICAL)],
                    markersize=MARKER_SIZE + 1, markeredgewidth=1.6, clip_on=False, zorder=5)

    handles = []
    for k, grid in enumerate(grids):
        junctions = next((r["n_junctions"] for r in rows if r["grid"] == grid and r["n_junctions"]), "?")
        handles.append(Line2D([], [], color=CATEGORICAL[k % len(CATEGORICAL)], marker=MARKERS[k % len(MARKERS)],
                              linewidth=LINE_W, markersize=MARKER_SIZE, label=f"{grid} ({junctions} junctions)"))
    if killed:
        handles.append(Line2D([], [], color=INK_2, marker="x", linestyle="", label="killed (timeout / memory)"))
    if any(r.get("rtf_lower_bound") == "1" for r in rows):
        handles.append(Line2D([], [], color=INK_2, marker="o", linestyle="", markerfacecolor=SURFACE,
                              label="lower bound (too fast to time)"))
    fig.legend(handles=handles, loc="outside right center", fontsize=8, frameon=False,
               title="grid", title_fontsize=8, labelcolor=INK_2)

    notes = []
    for key, label in (("rtf", "Stepping"), ("rtf_total", "End to end")):
        found = all_crossings[key]
        notes.append(f"{label} drops below real time: " + (
            "; ".join(f"{g} {where}" for g, where in found) if found else "never in this sweep"))
    killed_grids = sorted({r["grid"] for r in killed}, key=grids.index)
    if killed_grids:
        notes.append("Killed before finishing (x): " + ", ".join(killed_grids))
    fig.text(0.01, -0.02, "\n".join(notes), fontsize=8, color=INK_2, va="top", ha="left")
    return _save(fig, out_dir, "rtf_vs_vehicles")


def plot_load_and_size(rows: list[dict], out_dir: Path) -> list[Path]:
    fig, (ax_load, ax_size) = _figure(width=10.0, height=4.4, ncols=2)
    ok = [r for r in rows if r["status"] == "ok" and _num(r, "load_time_s") is not None]

    _style(ax_load, "junctions", "load time (s)", "esmini load time (to first step)")
    ax_load.set_xscale("log")
    ax_load.set_yscale("log")
    counts = sorted({int(r["n_vehicles"]) for r in ok})
    ramp = _ramp(len(counts))
    for n, color in zip(counts, ramp):
        by_j = defaultdict(list)
        for r in ok:
            if int(r["n_vehicles"]) == n:
                by_j[int(r["n_junctions"])].append(_num(r, "load_time_s"))
        xs = sorted(by_j)
        stats = [_agg(by_j[x]) for x in xs]
        ax_load.errorbar(xs, [m for m, _ in stats], yerr=[sd for _, sd in stats], color=color,
                         marker="o", markersize=MARKER_SIZE - 1, linewidth=LINE_W, capsize=2.5,
                         elinewidth=0.9, markeredgecolor=SURFACE, markeredgewidth=0.8, label=f"{n}")
    ax_load.legend(title="vehicles", fontsize=8, title_fontsize=8, frameon=False, labelcolor=INK_2)
    junctions = sorted({int(r["n_junctions"]) for r in ok})
    _fit_x(ax_load, junctions)
    if counts:  # network cost: fit the smallest vehicle count, beyond the startup floor
        smallest = [(int(r["n_junctions"]), _num(r, "load_time_s")) for r in ok
                    if int(r["n_vehicles"]) == counts[0]]
        _draw_fit(ax_load, smallest, "load time \u221d junctions", min_x=100)

    _style(ax_size, "junctions", ".xodr file size (MB)", "OpenDRIVE file size")
    ax_size.set_xscale("log")
    ax_size.set_yscale("log")
    nets = {}
    for r in rows:
        j, size = _num(r, "n_junctions"), _num(r, "xodr_size_bytes")
        if j and size:
            nets[(r["grid"], r["seed"])] = (int(j), size / 1e6)
    by_j = defaultdict(list)
    for j, mb in nets.values():
        by_j[j].append(mb)
    xs = sorted(by_j)
    stats = [_agg(by_j[x]) for x in xs]
    ax_size.errorbar(xs, [m for m, _ in stats], yerr=[sd for _, sd in stats], color=CATEGORICAL[0],
                     marker="o", markersize=MARKER_SIZE, linewidth=LINE_W, capsize=2.5,
                     elinewidth=0.9, markeredgecolor=SURFACE, markeredgewidth=0.8)
    _fit_x(ax_size, xs, right_pad=1.8)
    for x, (m, _) in zip(xs, stats):
        ax_size.annotate(f"{m:.1f} MB" if m >= 1 else f"{m * 1000:.0f} kB", (x, m), xytext=(0, 8),
                         textcoords="offset points", ha="center", fontsize=7.5, color=INK_2)
    return _save(fig, out_dir, "load_and_size_vs_junctions")


def plot_wall_vs_work(rows: list[dict], out_dir: Path) -> list[Path]:
    fig, (ax,) = _figure()
    _style(ax, "vehicles x junctions", "simulation wall time (s)",
           "Stepping wall time vs problem size")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ok = [r for r in rows if r["status"] == "ok" and _num(r, "sim_wall_time_s")]
    groups = defaultdict(list)
    for r in ok:
        groups[(int(r["n_vehicles"]) * int(r["n_junctions"]))].append(_num(r, "sim_wall_time_s"))
    xs = sorted(groups)
    stats = [_agg(groups[x]) for x in xs]
    ax.errorbar(xs, [m for m, _ in stats], yerr=[sd for _, sd in stats], color=CATEGORICAL[0],
                marker="o", linestyle="", markersize=MARKER_SIZE, capsize=2.5, elinewidth=0.9,
                markeredgecolor=SURFACE, markeredgewidth=0.8, zorder=3)
    _fit_x(ax, xs)
    _draw_fit(ax, [(x, m) for x, (m, _) in zip(xs, stats)], "wall time \u221d (vehicles \u00d7 junctions)", min_x=1e4)
    durations = {_num(r, "sim_duration") for r in ok} - {None}
    if len(durations) == 1:
        d = durations.pop()
        ax.axhline(d, color=INK_2, linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)
        ax.annotate(f"real time: wall = simulated ({d:.0f} s)", (ax.get_xlim()[0], d), xytext=(4, 4),
                    textcoords="offset points", fontsize=8, color=INK_2)
        lo, hi = ax.get_ylim()
        ax.axhspan(d, max(hi, d * 2), color=GRID, alpha=0.45, zorder=0, linewidth=0)
        ax.set_ylim(lo, max(hi, d * 2))
    return _save(fig, out_dir, "wall_vs_work")


def _ramp(n: int) -> list[str]:
    if n <= 1:
        return [BLUE_RAMP[4]] * n
    return [BLUE_RAMP[round(i * (len(BLUE_RAMP) - 1) / (n - 1))] for i in range(n)]


def summary(rows: list[dict]) -> str:
    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
    return ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))


def plot_all(results: PathLike, out_dir: Optional[PathLike] = None) -> list[Path]:
    rows = load_results(results)
    out = Path(out_dir) if out_dir else Path(results).parent / "plots"
    out.mkdir(parents=True, exist_ok=True)
    log.info("%d rows (%s)", len(rows), summary(rows))
    paths = plot_rtf_vs_vehicles(rows, out)
    paths += plot_load_and_size(rows, out)
    paths += plot_wall_vs_work(rows, out)
    return paths
