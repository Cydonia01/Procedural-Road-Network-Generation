# Copilot Prompts — Procedural Road Network → OpenDRIVE → esmini

**How to use these prompts**
- Put **Prompt 0 (Project Context)** in `.github/copilot-instructions.md`. Copilot reads that file automatically on every request.
- Use Copilot Chat in **Agent mode**. Paste **one phase prompt at a time**.
- Don't start the next phase until the current one passes its acceptance checks. Run the tests and open the output in esmini yourself.
- When something breaks, paste the exact error or the esmini log back into the same chat.

---

## Prompt 0 — Project Context (save as `.github/copilot-instructions.md`)

```
# Project: Procedural Road Network Generation for esmini

We are building a Python pipeline that procedurally generates road networks,
converts them to ASAM OpenDRIVE (.xodr), generates OpenSCENARIO (.xosc) traffic
scenarios, and runs them in the esmini simulator for large-scale traffic and
scalability experiments. We do NOT use OpenStreetMap; all networks are generated.

Pipeline:
  Generator -> RoadGraph (JSON) -> OpenDRIVE Builder -> .xodr -> Validator
                                                       -> Scenario Builder -> .xosc -> esmini (headless) -> metrics

Architecture rules:
- Generators only output a RoadGraph. They never touch OpenDRIVE.
- The OpenDRIVE builder only consumes a RoadGraph. It knows nothing about which generator produced it.
- Every stage is runnable from the CLI and testable in isolation.

Tech stack:
- Python 3.10+, type hints everywhere, dataclasses for models.
- numpy, shapely, networkx, matplotlib, lxml, pytest.
- `scenariogeneration` package (xodr + xosc modules) for writing OpenDRIVE/OpenSCENARIO XML.
  Wrap it behind our own builder classes so it can be replaced later.
- esmini binaries (esmini, odrviewer) are external tools; their path comes from
  env var ESMINI_HOME or a config file. Never hard-code paths.

Repo layout:
  roadgen/
    graph/        model.py, io.py, plot.py
    generators/   base.py, manhattan.py
    opendrive/    builder.py, lanes.py, junctions.py, geometry.py
    validate/     schema.py, topology.py, continuity.py
    scenario/     xosc_builder.py
    sim/          runner.py
    bench/        benchmark.py
  cli.py
  tests/
  out/            (generated files, git-ignored)

OpenDRIVE conventions we follow:
- Target OpenDRIVE 1.6 (or lower if esmini complains); right-hand traffic.
- Lane ids: negative = right of reference line, positive = left, 0 = center lane.
- Roads stop at the junction boundary; junction interiors are covered only by connecting roads.
- Every road/junction id is unique; links use correct contactPoint (start/end).
- Units: meters, radians, m/s.

Coding rules:
- Small, pure functions; no global state; deterministic output given a seed.
- Write pytest tests for every new module.
- Log with the `logging` module, not print.
- Don't implement features from later phases unless asked.
```

---

## Prompt 1 — Phase 0: Setup and the RoadGraph Data Model

```
Phase 0: project setup and the RoadGraph data model.

1. Create the repo layout from copilot-instructions.md with empty __init__.py files,
   a pyproject.toml (dependencies: numpy, shapely, networkx, matplotlib, lxml,
   scenariogeneration, pytest), .gitignore (ignore out/), and a README with setup steps.

2. In roadgen/graph/model.py implement dataclasses:
   - Node: id: int, x: float, y: float, kind: Literal["junction","dead_end","auto"]
   - Edge: id: int, from_node: int, to_node: int,
           road_class: Literal["avenue","street"],
           lanes_forward: int, lanes_backward: int,   # forward = from_node -> to_node
           lane_width: float = 3.5, speed_limit: float = 13.9
   - RoadGraph: nodes: dict[int, Node], edges: dict[int, Edge], metadata: dict
     Methods: add_node, add_edge, neighbors(node_id), degree(node_id),
     edge_length(edge_id), edge_heading(edge_id), incident_edges(node_id),
     to_networkx(), validate() -> list[str] errors
     (validate checks: edges reference existing nodes, no zero-length edges,
      no duplicate edges between the same node pair, lanes_forward+lanes_backward >= 1).

3. roadgen/graph/io.py: save_json / load_json, with a "schema_version" field.

4. roadgen/graph/plot.py: plot_graph(graph, path) with matplotlib. Line width scales
   with lane count, color by road_class, arrows on one-way edges, node ids optional.

5. cli.py using argparse with a subcommand stub: `python -m cli plot-graph in.json out.png`.

6. tests/test_graph.py: round-trip JSON, validate() catches each error type,
   degree/heading/length are correct.

Acceptance: `pytest` passes; a hand-written 3-node JSON can be plotted to PNG.
```

---

## Prompt 2 — Phase 1: Single Road → OpenDRIVE → esmini

```
Phase 1: convert the simplest RoadGraph (2 nodes, 1 edge) into a valid .xodr.

1. roadgen/opendrive/lanes.py:
   make_lanes(lanes_forward, lanes_backward, lane_width) -> scenariogeneration Lanes
   - right lanes (negative ids) carry forward traffic, left lanes (positive ids) backward traffic
   - driving lane type, standard road marks (solid center line for two-way,
     broken between same-direction lanes, solid edge lines)
   - one-way roads: all lanes on the right side.

2. roadgen/opendrive/geometry.py: helpers to compute the start (x, y, heading) and length
   of a straight road segment from two points, plus a trim(start, end, trim_start, trim_end)
   helper (we'll need it for junctions in Phase 2).

3. roadgen/opendrive/builder.py: class OpenDriveBuilder with
   build(graph: RoadGraph) -> xodr.OpenDrive and write(path).
   For now handle only edges whose endpoints are dead ends (degree 1).
   Each Edge becomes one <road> with a <line> planView geometry. Use the absolute
   (x, y, hdg) from our geometry, not scenariogeneration's automatic adjustment, so
   coordinates match the graph exactly.
   Keep a mapping edge_id -> road_id and save it in a sidecar JSON (we'll need it for scenarios).

4. roadgen/validate/schema.py: validate an .xodr against the OpenDRIVE XSD if the XSD
   path is configured; skip with a warning otherwise.
   roadgen/validate/topology.py: parse the .xodr with lxml and check that all ids are unique
   and every predecessor/successor reference exists.

5. cli.py: `python -m cli build-xodr graph.json out.xodr` and
   `python -m cli view out.xodr` (launches $ESMINI_HOME/bin/odrviewer --odr out.xodr --density 1).

6. Tests: generated XML parses; the road length equals the edge length; the lane count matches.

Acceptance: odrviewer opens the file and shows cars driving on the single road in the
correct direction(s). Test with 1+1 lanes, 2+2 lanes, and a one-way 2+0 road.
```

---

## Prompt 3 — Phase 2: Junctions (the hard part)

```
Phase 2: support nodes with degree >= 3 as OpenDRIVE junctions.
Start with a single 4-way junction, then a T-junction.

1. Junction boundary: for each junction node compute a trim distance per incident edge
   = (max half-width of the crossing roads) + margin (configurable, default 2 m).
   Trim each road so it ends at the junction boundary (use geometry.trim).

2. roadgen/opendrive/junctions.py: build a <junction> per junction node.
   For each ordered pair (incoming road, outgoing road), excluding U-turns:
   - Classify the movement as straight, left, or right from the heading difference.
   - Lane mapping:
       right turn -> from the rightmost incoming lane to the rightmost outgoing lane
       left turn  -> from the leftmost incoming lane to the leftmost outgoing lane
       straight   -> lane i to lane i (min(lanes_in, lanes_out) lanes)
     Only create movements allowed by lane directions (respect one-way edges).
   - Create one connecting road per lane movement (single lane, inside the junction).
     Geometry: straight -> <line>; turns -> <paramPoly3> (or arc+line) whose start/end
     position AND heading exactly match the incoming lane end and outgoing lane start
     (apply the lane offset from the reference line).
   - Set the connecting road's <link> predecessor/successor to the incoming/outgoing roads
     with the correct contactPoint, and add the <connection> with the right <laneLink>.
   - Set each normal road's predecessor/successor to the junction (elementType="junction").
   First try scenariogeneration's CommonJunctionCreator (check the installed version's API).
   If it can't produce correct lane-level geometry for our cases, build connecting roads
   manually with the rules above.

3. roadgen/validate/continuity.py: for every link (road<->connecting road), evaluate the
   lane-center (x, y, heading) at both contact points and assert they match within 1 cm and
   0.01 rad. Report every violation with road ids.

4. Test fixtures (tests/fixtures/*.json): single_4way.json, single_T.json,
   4way_avenue_x_street.json (2+2 crossing 1+1), 4way_oneway.json.

5. Tests: every movement exists; laneLinks reference existing lanes; continuity passes;
   topology passes.

Acceptance: in odrviewer with --density 2, cars go straight and turn left and right
through each junction without disappearing, jumping, or getting stuck. Save a screenshot
of each fixture in out/.
```

---

## Prompt 4 — Phase 3: Manhattan Grid Generator

```
Phase 3: implement the parametric Manhattan grid generator.

1. roadgen/generators/base.py: abstract class RoadNetworkGenerator with
   generate(params, seed) -> RoadGraph and a params dataclass.

2. roadgen/generators/manhattan.py: ManhattanGenerator with ManhattanParams:
   n_avenues: int, n_streets: int,
   avenue_spacing: float = 250.0, street_spacing: float = 80.0,
   rotation_deg: float = 0.0, origin: tuple = (0, 0),
   avenue_lanes: tuple = (2, 2), street_lanes: tuple = (1, 1),
   lane_width: float = 3.5, avenue_speed: float = 13.9, street_speed: float = 11.1,
   jitter: float = 0.0 (random node position noise in meters),
   edge_drop_prob: float = 0.0 (randomly remove street segments, keep the graph connected)
   Algorithm:
   - Place nodes on a grid: node (i, j) at i*avenue_spacing along x, j*street_spacing along y,
     then rotate by rotation_deg around the origin.
   - Avenue edges connect (i, j)-(i, j+1); street edges connect (i, j)-(i+1, j).
   - Apply jitter and edge dropping with numpy.random.default_rng(seed).
   - Set node.kind from degree. Make sure the result is connected (networkx).
   Same seed + same params must give identical output.

3. cli.py: `python -m cli generate manhattan --avenues 5 --streets 10 --seed 1 -o out/grid.json`
   and a one-shot `python -m cli pipeline manhattan ... -o out/grid` that writes the
   .json, .png, and .xodr, and runs all validators.

4. Tests: node and edge counts are correct; spacing and rotation are correct;
   determinism; connectivity after dropping edges; the full pipeline passes validation
   for 2x2, 3x5, and 10x10 grids.

5. Performance: log generation and build time; a 20x50 grid should build in a few seconds.
   Profile and fix it if it doesn't.

Acceptance: a 10x10 grid in odrviewer with random traffic runs for 2 minutes with
cars moving through all junctions.
```

---

## Prompt 5 — Phase 4: Manhattan Realism

```
Phase 4: make the Manhattan generator closer to real Manhattan.
Changes must stay inside the generator and params unless the builder truly needs a new feature.

1. One-way pattern: add params street_oneway_pattern: Literal["none","alternating"] and
   avenue_oneway_pattern: Literal["none","alternating"]. Alternating = consecutive streets
   flip direction (as in Manhattan cross streets). One-way roads get all lanes forward.
2. Multi-lane avenues: support e.g. 4 lanes one-way (4, 0).
3. Real-world preset: ManhattanParams.preset("manhattan_like") with rotation ~29 deg,
   avenue spacing ~250 m, street spacing ~80 m, one-way alternating streets,
   avenue width 4 lanes, street width 2 lanes.
4. Builder: confirm junctions handle mixes of one-way and two-way roads with different
   lane counts. Every reachable node must still have at least one way in and one way out
   (add a validator: the networkx DiGraph built from lane directions is strongly connected;
   if it isn't, log which nodes are traps).
5. Map edges: decide how dead ends at the grid boundary are handled. Implement an option
   boundary_mode: Literal["dead_end","loop"]. "loop" adds a ring road around the grid so
   traffic never dead-ends.
6. Tests for each new option + a strong-connectivity test for the preset.

Acceptance: the manhattan_like preset (10 avenues x 30 streets) passes all validators,
and odrviewer traffic flows with no vehicles stuck at dead ends.
```

---

## Prompt 6 — Phase 5: Agent Scenarios (.xosc)

```
Phase 5: generate OpenSCENARIO files with many vehicles for esmini.

1. roadgen/scenario/xosc_builder.py: ScenarioBuilder(xodr_path, graph, edge_to_road_map)
   with ScenarioParams:
   n_vehicles: int, seed: int, sim_duration: float,
   spawn_mode: Literal["random_lanes","explicit_routes"],
   speed_range: tuple, min_spawn_gap: float = 10.0
   - random_lanes: place vehicles at random valid lane positions (LanePosition road/lane/s)
     respecting min_spawn_gap and lane direction; give each a speed and let esmini's
     default controller follow the road. Pick a random route at junctions.
   - explicit_routes: compute shortest paths on the directed graph between random
     origin/destination pairs and convert them to OpenSCENARIO Route/AssignRouteAction
     waypoints (road ids from edge_to_road_map).
   - The StopTrigger ends the simulation at sim_duration.
   Use the scenariogeneration xosc module.

2. roadgen/sim/runner.py: run_esmini(xosc_path, headless=True, timestep=0.05,
   record=True, csv=True) -> SimResult
   - Build the command: esmini --osc file.xosc --headless --fixed_timestep 0.05
     [--record out.dat] [--csv_logger out.csv]
   - Capture stdout/stderr and wall-clock time; parse warnings/errors from the esmini log;
     return SimResult(wall_time, sim_time, real_time_factor, n_vehicles, errors, log_path).

3. cli.py: `python -m cli scenario out/grid.xodr --vehicles 100 --seed 1 -o out/grid.xosc`
   and `python -m cli simulate out/grid.xosc [--gui]`.

4. Tests: generated .xosc parses; all spawn positions are on valid lanes; with the same
   seed the scenario is identical; a tiny scenario (2x2 grid, 5 cars, 10 s) runs in
   esmini headless with exit code 0 (skip the test if ESMINI_HOME is unset).

Acceptance: a 10x10 grid with 200 vehicles runs headless for 120 s without errors.
With --gui the vehicles visibly route through junctions.
```

---

## Prompt 7 — Phase 6: Scalability Experiments

```
Phase 6: benchmark harness for scalability experiments.

1. roadgen/bench/benchmark.py: run a parameter sweep from a YAML config, e.g.
   grid_sizes: [[5,5],[10,10],[20,20],[30,30],[50,50]]
   vehicle_counts: [10, 50, 100, 500, 1000, 2000]
   seeds: [1,2,3]
   sim_duration: 60
   For each combination record: n_nodes, n_edges, n_junctions, total road length,
   generation time, xodr build time, xodr file size, esmini load time (time to first
   step, parsed from the log or measured with a 0-second run), simulation wall time,
   real-time factor, peak memory (psutil, child process), and errors.
   Skip combinations where vehicles don't fit (spawn gap) and log why.
   Write the results to out/bench/results.csv (append mode, resumable: skip rows already present).

2. Plots (matplotlib, saved as PNG + PDF for the report):
   - real-time factor vs n_vehicles (one line per grid size)
   - load time and file size vs n_junctions
   - wall time vs (n_vehicles x n_junctions)
   Error bars across seeds.

3. cli.py: `python -m cli bench config.yaml` and `python -m cli bench-plot results.csv`.

4. Add a timeout per run and a hard memory cap. Record failures instead of crashing.

Acceptance: the sweep runs unattended, results.csv is complete, and the plots clearly show
where esmini stops being real-time (RTF < 1).
```

---

## Prompt 8 (Optional) — Phase 7: Pluggable Generators and 3D

```
Phase 7: prepare for new algorithms and 3D.

1. Generator registry: generators register by name (decorator); the CLI lists them and
   loads params from YAML. Add a skeleton LSystemGenerator (Parish & Müller style:
   priority queue of proposed segments, global goals + local constraints: intersect,
   snap to nearby node within radius r, minimum segment length). Output must still be a
   valid RoadGraph that passes graph.validate(). Implement the "grid" global goal first
   and confirm it reproduces a Manhattan-like grid.
2. Make the OpenDRIVE builder handle non-90-degree junctions and 3- to 5-way junctions
   (headings come from the graph, so this is mostly testing). Add fixtures with angled roads.
3. 3D: esmini can render roads from .xodr alone. Add optional export of simple building
   boxes per block (extract blocks as planar-graph faces with shapely polygonize, inset by
   road width, extrude to a random height) to an .obj or .osgb that esmini can load as
   SceneGraphFile in the .xosc.

Acceptance: `python -m cli pipeline lsystem --preset grid` produces a valid .xodr that runs in esmini.
```
