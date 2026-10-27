# 3D scene

esmini draws only the roads. `roadgen/scene/` adds the ground and simple
buildings around them, as an OpenSceneGraph file esmini loads as its
`SceneGraphFile`. All of it uses the same [fitted roads](geometry.md#fitted-roads),
junction trims and [heights](heights.md) as the builder, so nothing covers or
floats over a road.

```bash
python -m cli pipeline lsystem --preset grid --set extent=[1000,320] --buildings --simulate -o city
python -m cli pipeline lsystem --preset city --maps --allow-traps --ground --buildings --simulate -o hills
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
  (20 m), coloured by terrain class.
- **Levelling.** The mesh is levelled `below_road` (0.3 m) under every road
  and junction area, plus `road_margin` (2 m) beyond the road edges. It
  follows the same fitted roads, junction trims, junction planes and profile
  fit as the builder; a KD-tree finds the nearest point of each road.
- **Left alone:** the ground under bridges and tunnels.

The junction trims must match the `.xodr`, so pass `ground` the same
`--junction-margin` as the build.

## Buildings

`buildings.py`, parameters under `buildings:`.

1. **Blocks** are the bounded faces of the road graph (shapely
   `polygonize`), drawn along the fitted roads, so buildings follow the
   curves.
2. **Footprint.** A block minus every road widened by its half width plus
   `setback` (3 m). Dead-end roads inside a block stay clear too.
3. **Rounded corners.** Convex corners are rounded by `corner_radius` (6 m),
   so buildings keep clear of turning paths. Footprints under `min_area`
   (150 m²) are dropped.
4. **Extruded** to a random height between `min_height` and `max_height`
   (12–60 m). The roof is triangulated by ear clipping (Delaunay for holes),
   and the walls get outward normals.
5. **With maps,** a building stands on the ground. Its base is 1 m below the
   lowest ground under it, and its height counts from the highest. Footprints
   touching water are dropped.

## Formats

- **`.osgt`** (OpenSceneGraph ASCII, z-up; `osgt.py`): what esmini loads.
  - esmini's bundled OSG can't read `.obj`.
  - esmini loads scene graphs only when it has a window (`simulate --gui`,
    `view`).
- **`.obj` + `.mtl`** (y-up by default, `buildings.y_up`): for Blender,
  MeshLab and similar tools.
