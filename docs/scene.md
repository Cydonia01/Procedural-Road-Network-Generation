# 3D scene

esmini draws only the roads. `roadgen/scene/` adds the ground and simple
buildings around them, as an OpenSceneGraph file esmini loads as its
`SceneGraphFile`. All of it uses the same [fitted roads](geometry.md#fitted-roads),
junction trims and [heights](heights.md) as the builder, so nothing covers or
floats over a road.

```bash
python -m cli pipeline lsystem --set start=[0,0] --set extent=[1000,320] --buildings -o city
python -m cli pipeline lsystem --preset city --maps --allow-traps --ground --buildings -o hills
python -m cli simulate out/<hash>/hills.xosc --gui
python -m cli view out/<hash>/hills.xodr --scenegraph out/<hash>/hills_scene.osgt
```

| Option | Writes | Contents |
|--------|--------|----------|
| `pipeline --buildings` | `PREFIX_buildings.osgt` | Buildings only |
| `pipeline --ground` (needs an elevation map) | `PREFIX_scene.osgt` | Ground, plus the buildings with `--buildings` |
| `ground` command | `scene.osgt` | The same, from map files and a draped graph ([CLI](cli-reference.md#ground)) |

esmini takes a single SceneGraphFile, so ground and buildings share one
file. `pipeline --simulate` attaches it to the scenario.

## Ground

`ground.py`, parameters under `ground:`.

- **Mesh.** The elevation map becomes a triangle mesh every `cell_size`
  (20 m), coloured by terrain class, each vertex the mean over a 3 × 3 patch
  of samples, so shores blend instead of stepping cell by cell.
- **Levelling.** The mesh is levelled `below_road` (0.3 m) under every road
  and junction area. It follows the same fitted roads, junction trims,
  junction planes and profile fit as the builder; a KD-tree finds the
  nearest point of each road.
  - Every vertex of a triangle that touches a road is levelled: out to
    `road_margin` (2 m) beyond the edge plus a cell diagonal.
  - Banking is followed across the road only; beyond the edge the ground
    stays at the edge's height.
  - On sag curves a vertex goes down by as much as the road dips below the
    chord to the next vertex along it.
  - Last, every triangle that still covers a sample of a road surface (every
    `cell_size` / 8 along each road, at five offsets across it, and on each
    junction plane) has its corners pressed down below it.
- **Bridges.** Land under a bridge is lowered to `under_bridge` (2 m) below
  the deck, never raised. The ground over tunnels is left alone.
- **Check.** The pipeline logs how many road-surface samples the ground
  covers (`poke_through`); a warning if any.
- **With HD maps** (the default; not with `--no-hd`) the levelling reaches the roads' outer
  edges, sidewalks and shoulders included, and uses the same wider junction
  trims as the build. Curbs and sidewalks are raised in the `.xodr` itself
  (`<height>`), which esmini draws.

The junction trims must match the `.xodr`, so pass `ground` the same
`--junction-margin` as the build.

## Junction surface

`junctions.py`, parameters under `scene:`. Parts of a junction that no lane
covers (corners no turn follows, slivers between turning lanes) are found by
the builder and listed in the sidecar as `junction_fills`. When the pipeline
writes a scene (`--ground` or `--buildings`), it draws them as flat triangles
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
   `setback` (3 m). Dead-end roads inside a block stay clear too. With HD
   maps the half width includes the road's
   [side lanes](geometry.md#cross-sections), so buildings stand `setback`
   behind the sidewalks.
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
  - esmini loads scene graphs only when it has a window (`simulate --gui`,
    `view`).
- **`.obj` + `.mtl`** (y-up by default, `buildings.y_up`): for Blender,
  MeshLab and similar tools.
