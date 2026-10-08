"""JSON serialization for RoadGraph."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Union

from roadgen.graph.model import Edge, Node, RoadGraph

SCHEMA_VERSION = 1

PathLike = Union[str, Path]


def graph_to_dict(graph: RoadGraph) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": graph.metadata,
        "nodes": [asdict(n) for _, n in sorted(graph.nodes.items())],
        "edges": [asdict(e) for _, e in sorted(graph.edges.items())],
    }


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
        graph.add_edge(Edge(**e))
    return graph


def save_json(graph: RoadGraph, path: PathLike) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph_to_dict(graph), indent=2) + "\n")


def load_json(path: PathLike) -> RoadGraph:
    return graph_from_dict(json.loads(Path(path).read_text()))
