# Input maps

Parish & Müller (2001) drive road growth with image maps of land and water,
elevation and population density. Highways seek population peaks, and local
constraints keep roads out of water and off steep slopes. Their maps were
drawn by hand or scanned from printed atlases, and the city in their Figure 2
is imaginary.

`roadgen/maps/` generates these maps procedurally, adds a **style** map of
street patterns, and can also read real maps from images. Only
[lsystem](lsystem.md#growing-on-input-maps) grows on them. The order the
maps enter the pipeline in is described in
[Architecture](architecture.md#how-maps-enter).

```bash
python -m cli maps                                               # generators, presets, parameters
python -m cli map terrain noise --preset coastal --seed 3 -o coast
python -m cli map elevation fbm --preset hilly --on terrain=out/<hash>/coast.npz -o coast_elev
python -m cli pipeline lsystem --preset city --maps --allow-traps     # all maps, then the network on them
```

## Map kinds

| Kind | Values | dtype | Generators |
|------|--------|-------|------------|
| `terrain` | `0` water, `1` land, `2` vegetation (park / forest) | `uint8` | `noise`, `image` |
| `elevation` | height [m] | `float32` | `fbm`, `image` |
| `population` | density [people/km²] | `float32` | `centers`, `image` |
| `style` | `0` grid, `1` radial, `2` organic, `3` contour, `4` suburb | `uint8` | `zones`, `image` |

**A linear pipeline: terrain → elevation → population → style.**

- Each generator gets the maps of the earlier kinds as `upstream` (a `MapSet`;
  any layer may be missing):
  - elevation reads terrain;
  - population reads both;
  - style reads all three.
- The stack is therefore consistent: water lies flat in valleys, and nobody
  lives on water, in parks or on steep slopes.
- A generator never reads a later kind or a RoadGraph. Without upstream maps
  it gives its standalone result, so any generators can be combined.
- The seed is salted with the kind and generator name, so one `--seed` gives
  unrelated maps.

**Grid and files.**

- A `GridSpec` (lower-left `origin`, `size` [m], square `cell_size`) places a
  map in world coordinates. Noise is sampled in world metres divided by a
  feature `scale`, so changing only `cell_size` changes the resolution, not
  the landscape.
- A `MapLayer` is saved as `.npz`: the array plus JSON metadata (kind, grid,
  generator, seed, params). A `.png` preview is written next to it.
- Row 0 of the array is the **south** edge.
- `MapLayer.sample(x, y)` reads values at world points: nearest cell for
  terrain and style, bilinear for the rest.
- `layer.on_grid(grid)` resamples a layer onto another grid.

Parameters follow the [usual layering](cli-reference.md#parameters-and---config),
under `maps.<kind>.<generator>` in the config. `--map-set KIND.KEY=VALUE`
sets one in `pipeline`.

## terrain `noise`: lakes, coast, rivers, vegetation

Three features, each from its own random stream, so adding a river leaves the
lakes and parks unchanged:

1. **Lakes and sea.** Domain-warped fBm is thresholded at its
   `water_fraction` quantile, so the share of water is exact before
   smoothing.
   - `coast_strength` blends in a linear ramp that pushes the water to one
     side (`coast_angle_deg`, or random), turning lakes into a sea.
   - `smoothing` runs 3×3 majority passes against speckles.
2. **Rivers.** Each is a band `river_width` wide through a random point in
   the middle half of the map, in a random direction. Its centre line is a
   sine meander (`river_meander`, `river_wavelength`), made irregular by 1D
   fBm.
3. **Vegetation.** The top `vegetation_fraction` of the land in another fBm
   field (`vegetation_scale`) becomes park or forest.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `scale` | 1500 | Size of the largest water bodies [m] |
| `octaves`, `lacunarity`, `gain` | 5, 2, 0.5 | fBm layers, frequency step, amplitude falloff |
| `warp` | 0.3 | Domain warp strength (in units of `scale`); bends outlines |
| `water_fraction` | 0.12 | Share of lakes or sea, before rivers |
| `coast_strength` | 0 | 0 gives scattered lakes, 1 a straight coast on one side |
| `coast_angle_deg` | null | Direction of the sea [deg from +x]; null = random |
| `smoothing` | 2 | Majority passes |
| `rivers`, `river_width`, `river_meander`, `river_wavelength` | 1, 50, 150, 1800 | Count, width [m], sideways amplitude [m], meander length [m] |
| `vegetation_fraction`, `vegetation_scale` | 0.15, 400 | Share of land that is park or forest, patch size [m] |

Presets: `inland` (no water), `lakes`, `coastal`, `river_city`.

## elevation `fbm`: fractal terrain

After Musgrave et al. (1989), which Parish & Müller cite for terrain:

1. Domain-warped fBm. `ridge` blends in a ridged multifractal, which gives
   sharp mountain crests.
2. Stretched to [0, 1] and raised to `exponent`. Values above 1 flatten
   valleys and sharpen peaks.
3. Scaled to [`min_elevation`, `max_elevation`].
4. **Thermal erosion.** For `erosion_iterations` passes, wherever the step to
   a 4-neighbour exceeds `tan(talus_deg) · cell_size`, `erosion_rate` of the
   excess slides downhill. Total height is conserved. This rounds off the
   unnaturally steep slopes of raw noise.

**On a terrain map with water**, step 3 changes:

- Land rises from `shore_height` above the water at the shore to its full
  height `shore_width` away (a smoothstep over the distance to water).
- After erosion, every water cell is set to `min_elevation` (the water
  surface), and land stays at least `shore_height` above it.

Lakes, rivers and sea therefore lie in valleys, all at one level. A small
`shore_width` packs the relief into steep banks that roads can't climb. The
default of 600 m keeps the share of steep cells about as without terrain.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `min_elevation`, `max_elevation` | 0, 80 | Height range [m]; `min_elevation` is also the water surface |
| `scale`, `octaves`, `lacunarity`, `gain`, `warp` | 1500, 6, 2, 0.5, 0.2 | As for terrain; higher `gain` is rougher |
| `ridge` | 0 | Ridged-noise blend, 0–1 |
| `exponent` | 1 | > 1 flattens valleys, sharpens peaks |
| `erosion_iterations`, `talus_deg`, `erosion_rate` | 20, 30, 0.5 | Erosion passes, steepest stable slope [deg], share moved per pass |
| `shore_width`, `shore_height` | 600, 2 | On a terrain map only: distance to full height [m], height at the shore [m] |

Presets: `flat`, `rolling`, `hilly`, `mountainous`.

## population `centers`: polycentric density

A polycentric form of Clark's (1951) urban density model, D(r) = D₀·e^(−r/b):
density falls exponentially with distance from a centre.

1. `n_centers` centres are placed at random, at least `min_center_distance`
   apart and `center_margin` from the edges.
   - The first is the main centre, with weight 1.
   - The others draw a weight from `secondary_weight`, and their decay length
     is scaled by √weight.
2. Density is the sum of the centre profiles, `exponential` (Clark) or
   `gaussian` (flatter core), with `peak_density` at the main centre.
3. It is multiplied by `1 + noise_amount · fBm` for neighbourhood variation,
   and `base_density` is added everywhere.

**On terrain and elevation maps**, each cell is multiplied by its
habitability, a factor in [0, 1]:

- 0 on water;
- `vegetation_factor` in parks and forest;
- falling linearly from 1 on flat ground to 0 at `max_slope_deg`.

Centres are only placed where habitability is at least 0.5, so a city never
centres on a lake or a cliff. They are stored in the metadata as `centers:
[[x, y, weight], …]`.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `n_centers`, `peak_density`, `decay_length` | 3, 15000, 700 | Centres, main peak [people/km²], decay length [m] |
| `profile` | `exponential` | or `gaussian` |
| `secondary_weight` | [0.3, 0.7] | Other centres' peak relative to the main one |
| `min_center_distance`, `center_margin` | 800, 0.15 | Spacing [m]; distance from the edges as a share of map size |
| `noise_amount`, `noise_scale` | 0.35, 400 | Relative fBm variation and its size [m] |
| `base_density` | 50 | Rural floor [people/km²] |
| `vegetation_factor`, `max_slope_deg` | 0.1, 20 | With upstream maps only: density in parks; slope where density reaches 0 [deg] (0 = no limit) |

Presets: `monocentric`, `polycentric`, `sprawl`.

## style `zones`: street patterns per district

Parish & Müller drive their pattern rules with grey-scale control maps
(sec. 3.2.2). A style map holds one street-pattern class per cell, and
lsystem's [field goal](lsystem.md#pattern-fields-global_goal-field) blends
the patterns by it. Only the field goal reads it, so `pipeline --maps` builds
a style map only for that goal.

**Districts.** `zones` places one site per `zone_size` square, at a random
spot in it. Each cell belongs to its nearest site after a domain warp of
`warp · zone_size`, so borders are irregular.

**Each district gets a class**, by the first rule that applies:

1. **Contour**, on an elevation map, if its mean grade is at least
   `contour_grade`.
2. On a population map, by its mean density on land:
   - **radial** if it holds the main centre or its site is within
     `radial_radius` of it;
   - **grid** at `core_density` or more;
   - down to `suburb_density`, **grid**, or **organic** with probability
     `organic_share`;
   - **suburb** below that.
3. Without upstream maps, by `shares` (grid, radial, organic, contour, suburb).

**Grid angles.** Every district also draws a grid angle: a common base angle
plus a normal spread of `angle_spread_deg`. Neighbouring districts therefore
meet at an angle, as in real cities.

**Metadata.** The layer stores the `sites` (`[x, y, class, angle_deg]`) and
the `radial_centers`. lsystem's grid follows the nearest site's angle, and its
radial pattern centres on the radial centres.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `zone_size` | 800 | Typical district size [m] |
| `warp` | 0.25 | Border warp, in units of `zone_size` |
| `shares` | (0.5, 0, 0.3, 0, 0.2) | Class shares without a population map |
| `core_density`, `suburb_density` | 6000, 1500 | Density bands [people/km²] |
| `organic_share` | 0.35 | Share of middle districts that are organic |
| `radial_radius` | 600 | Districts this close to the main centre are radial [m]; 0 = none |
| `contour_grade` | 0.10 | Districts at least this steep (mean rise / run) follow contours |
| `angle_spread_deg` | 25 | Spread of district grid angles [deg] |

Presets: `planned` (one grid angle, few organic districts, no radial core)
and `old_town` (a wide radial core, mostly organic, many angles).

## `image`: real or hand-drawn maps

Every kind has an `image` generator that stretches a PNG, JPEG or `.npy`
over the grid. The first image row is the **north** edge, and `path` is
required. Image maps are used as given and don't adapt to upstream maps,
since real data is consistent already. They do feed the kinds after them.

| Kind | Pixels → values |
|------|-----------------|
| `terrain` | **Colour image:** nearest of `water_color`, `land_color`, `vegetation_color`. **Grayscale** (also gray saved as RGB): water where darker than `gray_threshold` (`water_is_dark: false` flips it) |
| `elevation` | Grayscale 0..1 → `min_elevation`..`max_elevation` (default 0..100). `stretch: true` uses the raster's own min..max, for 16-bit or `.npy` data in arbitrary units |
| `population` | Grayscale 0..1 → `min_density`..`max_density` (default 0..20000) |
| `style` | Nearest of the five `colors` (grid, radial, organic, contour, suburb); radial centres are the centroids of radial pixels |

GeoTIFFs are not read directly, since that would need GDAL. Convert them
first:

```bash
gdal_translate -of PNG -ot UInt16 -scale srtm_tile.tif heightmap.png
python -m cli pipeline lsystem --preset city --maps --allow-traps --elevation image \
    --map-set elevation.path=heightmap.png --map-set elevation.stretch=true
```

A map file should cover the generator's area. A file made for another area
logs a warning, and its edge values are used outside it.

### Where to get real maps

All of these are free. Crop them to your area and export a grayscale or
class-coloured PNG.

| Kind | Source | Notes |
|------|--------|-------|
| Elevation | [Tangram Heightmapper](https://tangrams.github.io/heightmapper/) | Grayscale heightmap of any view, exported from the browser. Quickest |
| Elevation | [Copernicus DEM GLO-30](https://registry.opendata.aws/copernicus-dem/) | 30 m global DEM (GeoTIFF) |
| Elevation | [USGS EarthExplorer (SRTM)](https://earthexplorer.usgs.gov/), [OpenTopography](https://opentopography.org/) | 30 m global DEM |
| Terrain (water) | [JRC Global Surface Water](https://global-surface-water.appspot.com/) | 30 m water occurrence: a ready water mask |
| Terrain | [ESA WorldCover](https://esa-worldcover.org/) | 10 m land cover; recolour classes to the three terrain colours |
| Terrain (Europe) | [CORINE Land Cover](https://land.copernicus.eu/) | 100 m, 44 classes |
| Population | [GHSL GHS-POP](https://human-settlement.emergency.copernicus.eu/) | 100 m / 1 km global grid |
| Population | [WorldPop](https://www.worldpop.org/) | 100 m counts per country |
| Population | [HDX: Meta High Resolution Population Density](https://data.humdata.org/) | ~30 m, many countries |
| Population | [NASA SEDAC GPWv4](https://sedac.ciesin.columbia.edu/data/collection/gpw-v4) | ~1 km global density |
| All | [Virtual Terrain Project](http://vterrain.org/) | The terrain resource Parish & Müller cite |

Population grids give people per cell. Divide by the cell area in km² (or set
`max_density` to match) to get people/km².

## Adding a map generator

```python
from dataclasses import dataclass
from typing import Optional
import numpy as np
from roadgen.maps.base import MapGenerator, register_map
from roadgen.maps.model import GridSpec, MapLayer, MapSet

@dataclass(frozen=True)
class RampParams:
    slope: float = 0.05  # [m/m]

@register_map("elevation", "ramp")
class RampGenerator(MapGenerator[RampParams]):
    params_type = RampParams
    description = "plane rising towards +x"

    def generate(self, params: RampParams, grid: GridSpec, seed: int,
                 upstream: Optional[MapSet] = None) -> MapLayer:
        x, _ = grid.cell_centers()
        z = (x - grid.origin[0]) * params.slope
        return MapLayer("elevation", grid, z.astype(np.float32),
                        {"generator": self.name, "seed": seed})
```

1. Import the module in `roadgen/maps/__init__.py`. It then shows up in
   `maps`, `map elevation ramp` and `pipeline --elevation ramp`.
2. Put the parameter defaults under `maps.elevation.ramp` in `defaults.yaml`.
3. Draw all randomness from `self.rng(seed)`, or from
   `self.seed_sequence(seed).spawn(n)` for independent features.
4. Read upstream maps with `upstream.terrain.on_grid(grid)`. Return the
   standalone result when they are missing.
