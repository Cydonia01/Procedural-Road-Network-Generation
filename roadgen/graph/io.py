"""JSON serialization for RoadGraph."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Union

from roadgen.graph.model import Edge, Node, RoadGraph

SCHEMA_VERSION = 1

PathLike = Union[str, Path]
_OPTIONAL: dict[str, Any] = {"z": 0.0, "radius_cap": None, "profile": [], "bridges": [],
                             "tunnels": []}  # field -> default


def graph_to_dict(graph: RoadGraph) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": graph.metadata,
        "nodes": [_compact(asdict(n)) for _, n in sorted(graph.nodes.items())],
        "edges": [_compact(asdict(e)) for _, e in sorted(graph.edges.items())],
    }


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Leave out unset heights, so a flat graph's JSON stays as it was."""
    return {k: v for k, v in d.items() if k not in _OPTIONAL or v != _OPTIONAL[k]}


def graph_from_dict(data: dict[str, Any]) -> RoadGraph:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {version!r}, expected {SCHEMA_VERSION}"
        )
    graph = RoadGraph(metadata=dict(data.get("metadata", {})))
    for n in data.get("nodes", []):
        graph.add_node(Node(**n))
    for e in data.get("edges", []):
        e = dict(e)
        e["bridges"] = [tuple(b) for b in e.get("bridges", [])]  # JSON has lists
        e["tunnels"] = [tuple(t) for t in e.get("tunnels", [])]
        graph.add_edge(Edge(**e))
    return graph


def save_json(graph: RoadGraph, path: PathLike) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph_to_dict(graph), indent=2) + "\n")


def load_json(path: PathLike) -> RoadGraph:
    return graph_from_dict(json.loads(Path(path).read_text()))
