"""Matplotlib rendering of a RoadGraph for quick visual inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Union

from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

from roadgen.graph.model import RoadGraph

ROAD_CLASS_COLORS = {"avenue": "#d62728", "street": "#1f77b4"}
LINE_WIDTH_PER_LANE = 1.2
ARROW_FRACTION = 0.3  # one-way arrow length relative to its edge
NODE_COLORS = {"junction": "black", "dead_end": "gray", "auto": "white"}


def plot_graph(
    graph: RoadGraph,
    path: Union[str, Path],
    show_node_ids: bool = False,
    dpi: int = 150,
) -> None:
    """Render graph to an image file. Edges referencing missing nodes are skipped."""
    fig = Figure(figsize=(8, 8))
    ax = fig.add_subplot()

    segments, colors, widths, arrows = [], [], [], []
    for e in graph.edges.values():
        if e.from_node not in graph.nodes or e.to_node not in graph.nodes:
            continue
        a, b = graph.nodes[e.from_node], graph.nodes[e.to_node]
        segments.append([(a.x, a.y), (b.x, b.y)])
        colors.append(ROAD_CLASS_COLORS.get(e.road_class, "black"))
        widths.append(LINE_WIDTH_PER_LANE * max(e.total_lanes, 1))
        if e.is_one_way:
            arrows.append((a, b) if e.lanes_forward > 0 else (b, a))
    ax.add_collection(
        LineCollection(segments, colors=colors, linewidths=widths, capstyle="round", zorder=1)
    )

    if arrows:
        # One quiver call: per-edge annotations are slow on large one-way grids.
        ax.quiver(
            [(s.x + d.x) / 2 for s, d in arrows],
            [(s.y + d.y) / 2 for s, d in arrows],
            [ARROW_FRACTION * (d.x - s.x) for s, d in arrows],
            [ARROW_FRACTION * (d.y - s.y) for s, d in arrows],
            angles="xy",
            scale_units="xy",
            scale=1,
            pivot="middle",
            color="black",
            width=0.002,
            headwidth=5,
            headlength=6,
            zorder=2,
        )

    nodes = list(graph.nodes.values())
    ax.scatter(
        [n.x for n in nodes],
        [n.y for n in nodes],
        s=20,
        c=[NODE_COLORS.get(n.kind, "white") for n in nodes],
        edgecolors="black",
        linewidths=0.8,
        zorder=3,
    )
    if show_node_ids:
        for n in nodes:
            ax.annotate(
                str(n.id),
                (n.x, n.y),
                textcoords="offset points",
                xytext=(4, 4),
                fontsize=8,
                zorder=4,
            )
    ax.autoscale_view()

    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.grid(True, linewidth=0.3, alpha=0.5)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
