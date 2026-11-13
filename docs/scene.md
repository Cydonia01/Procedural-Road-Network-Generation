# 3D scene

esmini draws only the roads. `roadgen/scene/` adds the ground, bridge and
tunnel structures and simple buildings around them, as an OpenSceneGraph
file esmini loads as its `SceneGraphFile`. The ground and the structures
follow the built `.xodr` itself (what esmini draws), the buildings the
[fitted roads](geometry.md#fitted-roads), so nothing covers or floats over a
road.

```bash
python -m cli pipeline lsystem --set start=[0,0] --set extent=[1000,320] -o city
python -m cli pipeline lsystem --preset city --maps -o hills
python -m cli view out/<hash>/hills.xodr --scenegraph out/<hash>/hills_scene.osgt
```

`pipeline` writes the scene by default ([options](cli-reference.md#pipeline)):

| Run | Writes | Contents |
|--------|--------|----------|
| `pipeline` with an elevation map | `PREFIX_scene.osgt` | Ground, bridges and tunnels, plus the buildings (`--no-buildings` leaves them out) |
| `pipeline` without one, or with `--no-ground` | `PREFIX_buildings.osgt` | Buildings only (`--no-buildings`: no scene) |
| `ground` command | `scene.osgt` | The same, from map files and a built `.xodr` ([CLI](cli-reference.md#ground)) |

esmini takes a single SceneGraphFile, so ground and buildings share one
file. `view --scenegraph` shows it with the roads.

## Ground

`ground.py` and `surface.py`, parameters under `ground:`.

- **Road surfaces.** `surface.py` reads the built `.xodr`: for every road
  and connecting road, its outer edges every 2 m, with their heights from
  the elevation profile, the superelevation and any lane `<height>` (the
  generated roads have none). The strips between the edges (outside bridges
  and tunnels) and the junction fills make up the footprint.
- **Mesh.** The elevation map becomes a grid every `cell_size` (20 m). A
  cell a road or the shoreline crosses is triangulated again (constrained
  Delaunay) over the part outside the roads; the other cells keep their two
  triangles. Points of a cell shared with its neighbour get one vertex.
- **Footprint cut.** No triangle lies over a road. The ground meets each road
  along its outer edge, `below_road` (0.03 m) below the edge's height, and
  reaches `under_road` (0.2 m) in under it, so no gap shows where esmini
  draws a curved edge as chords. With HD maps the outer edge is the
  driving lanes' (the roads have no side lanes), so the ground runs right up
  to the carriageway.
- **Side slopes.** Within `slope_reach` (40 m) of a road edge the ground
  stays within `side_slope` (0.5, i.e. 1:2) times the distance of the edge's
  height: an embankment where the road runs above the land, a cut where it
  runs below. Water is only ever lowered.
- **Bridges.** Land under a bridge is lowered to `under_bridge` (2 m) below
  the deck, never raised. The ground over tunnels is left alone. Neither is
  cut out.
- **Shoreline.** The water area is the union of the terrain map's water
  cells, rounded by a closing and an opening `shore_smoothing` cells wide,
  simplified and corner-cut, so the raster's stair steps become a smooth
  line. Cells it crosses are split along it: water colour on one side, land
  colour on the other. Land and vegetation still blend over a cell.
- **Check.** The pipeline logs how many samples of the driven surface (every
  2 m along each road, at a quarter, half and three quarters across) the
  ground covers (`poke_through`); a warning if any. A test holds it at 0.
- The `ground` command needs the `.xodr` (`--xodr`) to cut the ground;
  without it the mesh follows the elevation map only.

## Structures

`structures.py`, parameters under `structures:` (`enabled: false` leaves
them out). esmini draws a bridge as a road surface in the air and a tunnel as
a road under the ground.

- **Decks.** Under each `<objects><bridge>` stretch, a deck `deck_depth`
  (1 m) thick, its sides rising `parapet` (1 m) above the road's outer edges.
- **Piers.** Every `pier_spacing` (30 m) along a longer bridge, a pier
  `pier_size` (1.5 m) thick and half the deck wide, from below the ground to
  the deck; none on another road, none where the deck is less than 1 m above
  the ground.
- **Tunnel portals.** At both ends of each `<tunnel>` stretch, two walls
  beside the road and a lintel `portal_clearance` (5 m) above it, each
  `portal_frame` (1 m) thick.

## Junction surface

`junctions.py`, parameters under `scene:`. Parts of a junction that no lane
covers (corners no turn follows, slivers between turning lanes) are found by
the builder and listed in the sidecar as `junction_fills`. When the pipeline
writes a scene (ground or buildings), it draws them as flat triangles
on the junction plane, `fill_below` (0.02 m) under the lanes, in
`asphalt_color` (the mean of esmini's `resources/models/asphalt.jpg`).
`junction_fill: false` leaves them out of the scene. The `.xodr` always
describes the same areas as `<object type="patch">` ([OpenDRIVE](opendrive.md)),
which esmini draws on top in a plain, lighter grey; making that look match
is open.

## Buildings

`buildings.py`, parameters under `buildings:`.

1. **Blocks** are the bounded faces of the road graph (shapely
   `polygonize`), drawn along the fitted roads, so buildings follow the
   curves.
2. **Footprint.** A block minus every road widened by its half width plus
   `setback` (7 m): a strip of ground between the road edge and the
   buildings. Dead-end roads inside a block stay clear too. With HD maps
   the half width includes the road's [median](geometry.md#cross-sections).
   A left-turn pocket widens its approach by a lane, from the junction trim
   over the pocket length and taper.
3. **Rounded corners.** Convex corners are rounded by `corner_radius` (6 m),
   so buildings keep clear of turning paths.
4. **Parcels** (`parcels.py`, `parcels: true`). Each block area is split by
   recursive oriented-box splits (Vanegas et al. 2012): lengthwise in the
   middle while a piece is deeper than `depth` allows (two rows back to
   back), then across, at a seeded 40–60 %, while it is wider than the
   `frontage` maximum. Parcels without street frontage (at least half the
   least frontage on the area's outline) stay empty as courtyards.
5. **One building per parcel,** inset `inset` (1.5 m) on every side with
   sharp corners; under `min_area` (150 m²) none. No building on water, in
   parks or in forest (terrain map).
6. **Height** between `min_height` and `max_height` (12–60 m): with a
   population map it grows with the density under the building, reaching
   `max_height` at `height_density` (10 000 people/km²), with a seeded
   log-normal spread; otherwise random. `parcels: false` gives the older
   one building per block area, at a random height.
7. **Extruded.** The roof is triangulated by ear clipping (Delaunay for
   holes), and the walls get outward normals.
8. **With maps,** a building stands on the ground. Its base is 1 m below the
   lowest ground under it, and its height counts from the highest. Footprints
   touching water are dropped.

## Formats

- **`.osgt`** (OpenSceneGraph ASCII, z-up; `osgt.py`): what esmini loads.
  - esmini's bundled OSG can't read `.obj`.
  - esmini loads scene graphs only when it has a window (`view`).
- **`.obj` + `.mtl`** (y-up by default, `buildings.y_up`): for Blender,
  MeshLab and similar tools.
