# Example runs

Ten networks, each the final product: one `pipeline` run with HD maps (side
lanes, road marks, signs, lights, turn lanes), buildings, a ground mesh where
the city is grown on input maps, and a drive-check. Each opens in odrviewer.
Made by [`examples.sh`](examples.sh); this page is written from the run
folders by [`make_readme.py`](make_readme.py).

```bash
bash runs/examples.sh            # generate all, then view each (needs a desktop session)
NO_GUI=1 bash runs/examples.sh   # generate only
bash runs/examples.sh view       # view only
```

Every successful `python -m cli` command gets its own folder `out/<hash>/`
(git-ignored) with its outputs and `run.yaml`, which shows the command and
the parameters that differ from `roadgen/defaults.yaml`. See
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

- `18a44ba1c39e` Mixed-density city on generated maps: highways, ring roads, river, hills

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 7 --ground --buildings --drive-check --lane-plot -o city
  python -m cli view out/18a44ba1c39e/city.xodr --scenegraph out/18a44ba1c39e/city_scene.osgt   # look at it
  ```

- `cce7125e36f6` Manhattan grid: alternating one-way avenues and streets, ring road

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 --buildings --drive-check --lane-plot -o manhattan
  python -m cli view out/cce7125e36f6/manhattan.xodr --scenegraph out/cce7125e36f6/manhattan_buildings.osgt   # look at it
  ```

- `716b97a10420` Paris: star of avenues, rings, grid outside

  ```bash
  python -m cli pipeline lsystem --preset paris --buildings --drive-check --lane-plot -o paris
  python -m cli view out/716b97a10420/paris.xodr --scenegraph out/716b97a10420/paris_buildings.osgt   # look at it
  ```

- `e908018c1be8` Suburb: cul-de-sac bulbs, suburban cross-section

  ```bash
  python -m cli pipeline lsystem --preset suburb --seed 1 --cross-section suburban --buildings --drive-check --lane-plot -o suburb
  python -m cli view out/e908018c1be8/suburb.xodr --scenegraph out/e908018c1be8/suburb_buildings.osgt   # look at it
  ```

- `b168aa2fa34a` Mixed city: style-map districts on generated maps

  ```bash
  python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 --ground --buildings --drive-check --lane-plot -o mixed_city
  python -m cli view out/b168aa2fa34a/mixed_city.xodr --scenegraph out/b168aa2fa34a/mixed_city_scene.osgt   # look at it
  ```

- `9856df2f8c96` Hill town: contour streets on hilly maps

  ```bash
  python -m cli pipeline lsystem --preset hill_town --maps --seed 1 --ground --buildings --drive-check --lane-plot -o hill_town
  python -m cli view out/9856df2f8c96/hill_town.xodr --scenegraph out/9856df2f8c96/hill_town_scene.osgt   # look at it
  ```

- `eb06b0d91901` City with freeways and overpasses

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 1 --set highway_crossings=separated --ground --buildings --drive-check --lane-plot -o freeways
  python -m cli view out/eb06b0d91901/freeways.xodr --scenegraph out/eb06b0d91901/freeways_scene.osgt   # look at it
  ```

- `0b78e8552f11` Freeway corridor with a diamond interchange, lane adds and drops

  ```bash
  python -m cli pipeline lsystem --preset highway_corridor --maps --seed 1 --ground --buildings --drive-check --lane-plot -o highway_corridor
  python -m cli view out/0b78e8552f11/highway_corridor.xodr --scenegraph out/0b78e8552f11/highway_corridor_scene.osgt   # look at it
  ```

- `1b708b0b5fa8` Diamond interchange: freeway crossing an arterial with ramps

  ```bash
  python -m cli pipeline junction --preset diamond --set interchange_length=800 --drive-check --lane-plot -o diamond
  python -m cli view out/1b708b0b5fa8/diamond.xodr   # look at it
  ```

- `6ad207eb9374` Cloverleaf interchange: freeway crossing a freeway with loop ramps

  ```bash
  python -m cli pipeline junction --preset cloverleaf --set interchange_length=800 --drive-check --lane-plot -o cloverleaf
  python -m cli view out/6ad207eb9374/cloverleaf.xodr   # look at it
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
