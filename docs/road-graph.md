# RoadGraph data model

`roadgen/graph/model.py` defines the interface between generators and the
rest of the pipeline. It is a plain undirected graph with lane information on
its edges. Coordinates are in metres, headings in radians and speeds in m/s.

## Classes

### `Node`

| Field | Type | Meaning |
|-------|------|---------|
| `id` | int | Unique node id |
| `x`, `y` | float | Position [m] |
| `kind` | `"junction"` / `"dead_end"` / `"auto"` | Informational. Generators set it from the degree (1 → `dead_end`, ≥3 → `junction`, otherwise `auto`) |

The builder ignores `kind`. It decides from the **degree**: degree 1 is a dead
end, and every node of degree ≥2 becomes an OpenDRIVE junction.

### `Edge`

A road between two nodes. **Forward lanes run `from_node → to_node`**.

| Field | Default | Meaning |
|-------|---------|---------|
| `id` | – | Unique edge id |
| `from_node`, `to_node` | – | Endpoint node ids |
| `road_class` | – | `"avenue"` (major) or `"street"` (minor). Used for plotting and stats |
| `lanes_forward` | – | Lanes driving from → to |
| `lanes_backward` | – | Lanes driving to → from |
| `lane_width` | 3.5 | [m] |
| `speed_limit` | 13.9 | [m/s] (≈ 50 km/h) |

Properties: `total_lanes`, and `is_one_way`, which is true when exactly one
of the lane counts is 0.

### `RoadGraph`

`nodes: dict[int, Node]`, `edges: dict[int, Edge]`, `metadata: dict`.

| Method | Purpose |
|--------|---------|
| `add_node`, `add_edge` | Insert; duplicate ids raise `ValueError` |
| `incident_edges(n)`, `neighbors(n)`, `degree(n)` | Topology queries, sorted by id for determinism |
| `edge_length(e)`, `edge_heading(e)` | Geometry of a straight edge |
| `to_networkx()` | Undirected `networkx.Graph` with all attributes |
| `validate()` | List of errors: missing endpoint nodes, zero length, duplicate edges between the same pair of nodes, negative lane counts, edges with no lanes |

Generators fill `metadata` with `name`, `generator`, `seed` and the full
`params`, so every graph records how it was made. The builder uses
`metadata["name"]` as the OpenDRIVE header name.

## JSON format

`roadgen/graph/io.py` reads and writes the graph (`save_json`, `load_json`).
Nodes and edges are written sorted by id. The format is versioned. Loading any
`schema_version` other than `1` raises an error.

```json
{
  "schema_version": 1,
  "metadata": {"name": "three_nodes", "note": "hand-written example"},
  "nodes": [
    {"id": 1, "x": 0.0,   "y": 0.0,  "kind": "dead_end"},
    {"id": 2, "x": 100.0, "y": 0.0,  "kind": "junction"},
    {"id": 3, "x": 100.0, "y": 80.0, "kind": "dead_end"}
  ],
  "edges": [
    {"id": 10, "from_node": 1, "to_node": 2, "road_class": "avenue",
     "lanes_forward": 2, "lanes_backward": 2},
    {"id": 11, "from_node": 2, "to_node": 3, "road_class": "street",
     "lanes_forward": 1, "lanes_backward": 0, "speed_limit": 8.3}
  ]
}
```

Fields with defaults (`kind`, `lane_width`, `speed_limit`) may be omitted.
Hand-written graphs like this one are useful for testing the builder in
isolation. See `tests/fixtures/` for junction shapes (T, Y, skewed 4-way,
5-way stars, one-way crossings).

## Plotting

`plot_graph(graph, path, show_node_ids=False)` (CLI: `plot-graph`) draws
avenues in red and streets in blue. Line width scales with the lane count,
one-way edges get a direction arrow, and nodes are coloured by `kind`. It uses
matplotlib's object API (`Figure`), so it never needs a display.
