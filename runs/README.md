# Example runs

Eight cities, each the final product: one `pipeline` run with HD maps
(medians, road marks, signs, lights, turn lanes), buildings, a ground mesh
where the city is grown on input maps, and a drive-check. Each opens in odrviewer.
Made by [`examples.sh`](examples.sh); this page is written from the run
folders by [`make_readme.py`](make_readme.py).

```bash
bash runs/examples.sh            # generate all, then view each (needs a desktop session)
NO_GUI=1 bash runs/examples.sh   # generate only
bash runs/examples.sh view       # view only
```

Every successful `python -m cli` command gets its own folder `out/<hash>/`
(git-ignored) with its outputs and `run.yaml`, which shows the command and
the parameters that differ from `kadmos/defaults.yaml`. See
[CLI reference → history](../docs/cli-reference.md#run-folders-history-replay-run).

```bash
python -m cli history        # all runs: hash, note, command
python -m cli HASH           # run one again
```

In Docker, prefix commands with `docker compose run --rm -T dev`, or
`docker compose run --rm gui` for views (see
[docs/docker.md](../docs/docker.md)). A view stays open until you close the
window and is not recorded as a run.

## Cities

- `7d0bf8761f40` Diamond interchange: freeway crossing an arterial with ramps

  ```bash
  python -m cli pipeline junction --preset diamond --set interchange_length=800 --no-buildings -o diamond
  python -m cli view out/7d0bf8761f40/diamond.xodr   # look at it
  ```

- `0bfc44351931` Cloverleaf interchange: freeway crossing a freeway with loop ramps

  ```bash
  python -m cli pipeline junction --preset cloverleaf --set interchange_length=800 --no-buildings -o cloverleaf
  python -m cli view out/0bfc44351931/cloverleaf.xodr   # look at it
  ```

- `b32d9399e81c` City with tunnels

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 8 --set tunnels=true -o city_tunnels
  python -m cli view out/b32d9399e81c/city_tunnels.xodr --scenegraph out/b32d9399e81c/city_tunnels_scene.osgt   # look at it
  ```

- `c051de292761` City with tunnels: highways at most 5 % grade

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 5 --set tunnels=true --set highway_max_grade=0.05 -o city_tunnels_steep
  python -m cli view out/c051de292761/city_tunnels_steep.xodr --scenegraph out/c051de292761/city_tunnels_steep_scene.osgt   # look at it
  ```

- `ccaded6329a0` Freeways with tunnels and overpasses

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 5 --set tunnels=true --set highway_max_grade=0.05 --set highway_crossings=separated -o freeway_tunnels
  python -m cli view out/ccaded6329a0/freeway_tunnels.xodr --scenegraph out/ccaded6329a0/freeway_tunnels_scene.osgt   # look at it
  ```

- `408bd9980661` Tunnels: city on high ridged hills

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 3 --set tunnels=true --set highway_max_grade=0.05 --map-set elevation.max_elevation=150 --map-set elevation.ridge=0.5 -o tunnels
  python -m cli view out/408bd9980661/tunnels.xodr --scenegraph out/408bd9980661/tunnels_scene.osgt   # look at it
  ```

- `a8673f136922` City with tunnels

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 5 --set tunnels=true -o city_tunnels
  python -m cli view out/a8673f136922/city_tunnels.xodr --scenegraph out/a8673f136922/city_tunnels_scene.osgt   # look at it
  ```

- `5ab814f818a2` Mixed-density city on generated maps: highways, ring roads, river, hills

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 7 -o city
  python -m cli view out/5ab814f818a2/city.xodr --scenegraph out/5ab814f818a2/city_scene.osgt   # look at it
  ```

- `92c59af7b533` Manhattan grid: alternating one-way avenues and streets, ring road

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 -o manhattan
  python -m cli view out/92c59af7b533/manhattan.xodr --scenegraph out/92c59af7b533/manhattan_buildings.osgt   # look at it
  ```

- `7fab2c945c70` Paris: star of avenues, rings, grid outside

  ```bash
  python -m cli pipeline lsystem --preset paris -o paris
  python -m cli view out/7fab2c945c70/paris.xodr --scenegraph out/7fab2c945c70/paris_buildings.osgt   # look at it
  ```

- `268166b6c479` Suburb: cul-de-sac bulbs, suburban cross-section

  ```bash
  python -m cli pipeline lsystem --preset suburb --seed 1 --cross-section suburban -o suburb
  python -m cli view out/268166b6c479/suburb.xodr --scenegraph out/268166b6c479/suburb_buildings.osgt   # look at it
  ```

- `edb73c707dcf` Mixed city: style-map districts on generated maps

  ```bash
  python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 -o mixed_city
  python -m cli view out/edb73c707dcf/mixed_city.xodr --scenegraph out/edb73c707dcf/mixed_city_scene.osgt   # look at it
  ```

- `ccf2517c30bf` Hill town: contour streets on hilly maps

  ```bash
  python -m cli pipeline lsystem --preset hill_town --maps --seed 1 -o hill_town
  python -m cli view out/ccf2517c30bf/hill_town.xodr --scenegraph out/ccf2517c30bf/hill_town_scene.osgt   # look at it
  ```

- `faff0bc7b820` City with freeways and overpasses

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 1 --set highway_crossings=separated -o freeways
  python -m cli view out/faff0bc7b820/freeways.xodr --scenegraph out/faff0bc7b820/freeways_scene.osgt   # look at it
  ```

- `286a8d098a10` Freeway corridor with a diamond interchange, lane adds and drops

  ```bash
  python -m cli pipeline lsystem --preset highway_corridor --maps --seed 1 -o highway_corridor
  python -m cli view out/286a8d098a10/highway_corridor.xodr --scenegraph out/286a8d098a10/highway_corridor_scene.osgt   # look at it
  ```
