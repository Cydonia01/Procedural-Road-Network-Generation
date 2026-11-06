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

- `9ce0717e2ee9` Mixed-density city on generated maps: highways, ring roads, river, hills

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 7 --ground --buildings --allow-traps --drive-check --lane-plot -o city
  python -m cli view out/9ce0717e2ee9/city.xodr --scenegraph out/9ce0717e2ee9/city_scene.osgt   # look at it
  ```

- `b30f79e6c69f` Manhattan grid: alternating one-way avenues and streets, ring road

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 --buildings --allow-traps --drive-check --lane-plot -o manhattan
  python -m cli view out/b30f79e6c69f/manhattan.xodr --scenegraph out/b30f79e6c69f/manhattan_buildings.osgt   # look at it
  ```

- `29a66fc7eb9b` Paris: star of avenues, rings, grid outside

  ```bash
  python -m cli pipeline lsystem --preset paris --buildings --allow-traps --drive-check --lane-plot -o paris
  python -m cli view out/29a66fc7eb9b/paris.xodr --scenegraph out/29a66fc7eb9b/paris_buildings.osgt   # look at it
  ```

- `4932d8403b35` Suburb: cul-de-sac bulbs, suburban cross-section

  ```bash
  python -m cli pipeline lsystem --preset suburb --seed 1 --cross-section suburban --buildings --allow-traps --drive-check --lane-plot -o suburb
  python -m cli view out/4932d8403b35/suburb.xodr --scenegraph out/4932d8403b35/suburb_buildings.osgt   # look at it
  ```

- `ea18f01d0693` Mixed city: style-map districts on generated maps

  ```bash
  python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 --ground --buildings --allow-traps --drive-check --lane-plot -o mixed_city
  python -m cli view out/ea18f01d0693/mixed_city.xodr --scenegraph out/ea18f01d0693/mixed_city_scene.osgt   # look at it
  ```

- `805902244ce5` Hill town: contour streets on hilly maps

  ```bash
  python -m cli pipeline lsystem --preset hill_town --maps --seed 1 --ground --buildings --allow-traps --drive-check --lane-plot -o hill_town
  python -m cli view out/805902244ce5/hill_town.xodr --scenegraph out/805902244ce5/hill_town_scene.osgt   # look at it
  ```

- `6c26de989dee` City with freeways and overpasses

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 1 --set highway_crossings=separated --ground --buildings --allow-traps --drive-check --lane-plot -o freeways
  python -m cli view out/6c26de989dee/freeways.xodr --scenegraph out/6c26de989dee/freeways_scene.osgt   # look at it
  ```

- `7d021bfe5299` Freeway corridor with a diamond interchange, lane adds and drops

  ```bash
  python -m cli pipeline lsystem --preset highway_corridor --maps --seed 1 --ground --buildings --allow-traps --drive-check --lane-plot -o highway_corridor
  python -m cli view out/7d021bfe5299/highway_corridor.xodr --scenegraph out/7d021bfe5299/highway_corridor_scene.osgt   # look at it
  ```

- `91a8b2a32f37` Diamond interchange: freeway crossing an arterial with ramps

  ```bash
  python -m cli pipeline junction --preset diamond --set interchange_length=800 --allow-traps --drive-check --lane-plot -o diamond
  python -m cli view out/91a8b2a32f37/diamond.xodr   # look at it
  ```

- `56d725c4bd7d` Cloverleaf interchange: freeway crossing a freeway with loop ramps

  ```bash
  python -m cli pipeline junction --preset cloverleaf --set interchange_length=800 --allow-traps --drive-check --lane-plot -o cloverleaf
  python -m cli view out/56d725c4bd7d/cloverleaf.xodr   # look at it
  ```
