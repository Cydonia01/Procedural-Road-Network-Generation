# Example runs

Eight cities, each the final product: one `pipeline` run with HD maps (side
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

- `c25a4b5b6566` Mixed-density city on generated maps: highways, ring roads, river, hills

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 7 --ground --allow-traps --buildings --drive-check -o city
  python -m cli view out/c25a4b5b6566/city.xodr --scenegraph out/c25a4b5b6566/city_scene.osgt   # look at it
  ```

- `3fb83c4ca4af` Manhattan grid: alternating one-way avenues and streets, ring road

  ```bash
  python -m cli pipeline lsystem --preset manhattan_like --seed 1 --allow-traps --buildings --drive-check -o manhattan
  python -m cli view out/3fb83c4ca4af/manhattan.xodr --scenegraph out/3fb83c4ca4af/manhattan_buildings.osgt   # look at it
  ```

- `51cf2255da53` Paris: star of avenues, rings, grid outside

  ```bash
  python -m cli pipeline lsystem --preset paris --allow-traps --buildings --drive-check -o paris
  python -m cli view out/51cf2255da53/paris.xodr --scenegraph out/51cf2255da53/paris_buildings.osgt   # look at it
  ```

- `1a781d6dc182` Suburb: cul-de-sac bulbs, suburban cross-section

  ```bash
  python -m cli pipeline lsystem --preset suburb --seed 1 --cross-section suburban --allow-traps --buildings --drive-check -o suburb
  python -m cli view out/1a781d6dc182/suburb.xodr --scenegraph out/1a781d6dc182/suburb_buildings.osgt   # look at it
  ```

- `fe6fa20f0885` Mixed city: style-map districts on generated maps

  ```bash
  python -m cli pipeline lsystem --preset mixed_city --maps --seed 1 --ground --allow-traps --buildings --drive-check -o mixed_city
  python -m cli view out/fe6fa20f0885/mixed_city.xodr --scenegraph out/fe6fa20f0885/mixed_city_scene.osgt   # look at it
  ```

- `50712981312c` Hill town: contour streets on hilly maps

  ```bash
  python -m cli pipeline lsystem --preset hill_town --maps --seed 1 --ground --allow-traps --buildings --drive-check -o hill_town
  python -m cli view out/50712981312c/hill_town.xodr --scenegraph out/50712981312c/hill_town_scene.osgt   # look at it
  ```

- `f059ec3a9d87` City with freeways and overpasses

  ```bash
  python -m cli pipeline lsystem --preset city --maps --seed 1 --set highway_crossings=separated --ground --allow-traps --buildings --drive-check -o freeways
  python -m cli view out/f059ec3a9d87/freeways.xodr --scenegraph out/f059ec3a9d87/freeways_scene.osgt   # look at it
  ```

- `f310f3371fd4` Freeway corridor with a diamond interchange, lane adds and drops

  ```bash
  python -m cli pipeline lsystem --preset highway_corridor --maps --seed 1 --ground --allow-traps --buildings --drive-check -o highway_corridor
  python -m cli view out/f310f3371fd4/highway_corridor.xodr --scenegraph out/f310f3371fd4/highway_corridor_scene.osgt   # look at it
  ```
