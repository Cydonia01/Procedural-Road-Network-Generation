# RoadGraph

`roadgen/graph/model.py` defines the interface between generators and the
rest of the pipeline. A RoadGraph is a plain undirected graph with lane
information on its edges. Units are metres, radians and m/s.

## Node

| Field | Type | Meaning |
|-------|------|---------|
| `id` | int | Unique node id |
| `x`, `y` | float | Position [m] |
| `kind` | `"junction"` / `"dead_end"` / `"auto"` / `"ramp"` | Set by `assign_node_kinds` from the degree (1 → `dead_end`, ≥3 → `junction`, else `auto`). `"junction"` on a two-road node keeps it a junction instead of a bend. `"ramp"` marks a highway node where interchange ramps leave or join ([movement rules](opendrive.md#ramp-nodes)) |
| `radius_cap` | float or null | Largest corner radius [m] at a bend, set by [align](heights.md#1-align-bends-that-leave-legal-ground) |
| `z` | float, default 0 | Height [m]; at a junction, the height of the junction area. Set by [drape](heights.md#2-drape-heights-from-the-elevation-map) |

**What a node becomes** in OpenDRIVE depends on its degree:

| Degree | Becomes |
|--------|---------|
| 1 | A dead end: the road simply ends there |
| 2 | A **bend**: the road continues through it, and both edges become one curved road ([chains](geometry.md#chains)). It stays a two-arm junction if `kind` is `"junction"` or it can't be merged |
| ≥ 3 | An OpenDRIVE junction |

## Edge

A road between two nodes. **Forward lanes run `from_node → to_node`.**

| Field | Default | Meaning |
|-------|---------|---------|
| `id` | – | Unique edge id |
| `from_node`, `to_node` | – | Endpoint node ids |
| `road_class` | – | `"highway"`, `"avenue"` (major) or `"street"` (minor). Sets design rules and plot colours |
| `lanes_forward`, `lanes_backward` | – | Lanes driving from → to, and to → from |
| `lane_width` | 3.5 | [m] |
| `speed_limit` | 13.9 | [m/s] (≈ 50 km/h) |
| `profile` | `[]` | Heights [m] at evenly spaced points strictly between the nodes: point k of n lies `k · length / (n + 1)` from `from_node`. Empty means straight between the node heights |
| `bridges` | `[]` | Stretches over water or another road, as `(start, end)` distances [m] from `from_node` |
| `tunnels` | `[]` | Stretches under ground, same form |

- `total_lanes` gives the sum of both directions.
- `is_one_way` is true when exactly one of the lane counts is 0.

## RoadGraph

`nodes: dict[int, Node]`, `edges: dict[int, Edge]`, `metadata: dict`.

| Method | Purpose |
|--------|---------|
| `add_node`, `add_edge` | Insert; duplicate ids raise `ValueError` |
| `incident_edges(n)`, `neighbors(n)`, `degree(n)` | Topology queries, sorted by id for determinism |
| `edge_length(e)`, `edge_heading(e)` | Geometry of the straight edge |
| `edge_profile(e)` | (distances, heights) along an edge, endpoints included |
| `has_elevation` | Whether any height is set |
| `to_networkx()` | Undirected `networkx.Graph` with all attributes |
| `validate()` | Errors: missing endpoint nodes, zero length, duplicate edges between one pair of nodes, negative lane counts, edges without lanes |

### Metadata

Every graph records how it was made: `name` (also the OpenDRIVE header
name), `generator`, `seed` and the full `params`. Some generators and steps
add lists of what they built:

| Key | Content |
|-----|---------|
| `maps` | Per kind: `generator`, `file`, and `source` for a `--map-file` |
| `bridges`, `tunnels` | Highway edge ids over water / through hills |
| `culdesacs`, `roundabouts`, `interchanges` | Built by the [transforms](generators.md#graph-transforms) |
| `overpasses` | Built by [grade separation](heights.md#3-grade-separation-overpasses) |
| `attempts` | Draws the `junction` generator needed |

The [network metrics](validation.md#network-metrics) count these features.

## JSON format

`roadgen/graph/io.py` reads and writes the graph (`save_json`, `load_json`).
Nodes and edges are sorted by id. Loading any `schema_version` other than 1
is an error.

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

- Fields with defaults may be left out.
- `radius_cap`, `z`, `profile`, `bridges` and `tunnels` are only written when
  set, so a flat graph's JSON has no height fields.
- Hand-written graphs like this one test the builder in isolation.
  `tests/fixtures/` has junction shapes ([Development](development.md#fixtures)).

## Plotting

`plot_graph(graph, path, show_node_ids=False)` (CLI `plot-graph`) draws the
fitted roads:

- highways orange, avenues red and streets blue;
- line width scaled by lane count;
- arrows on one-way edges, and nodes coloured by `kind`.

It uses matplotlib's object API, so it needs no display.
