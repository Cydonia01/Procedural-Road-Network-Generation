# One run config, hashed, and one folder per run (issue #8)

Two changes to how a CLI run is described and recorded:

1. **Run config.** Today a run's parameters come from seven mechanisms.
   Fold them into one document that holds only what differs from the
   defaults, and hash it.
2. **Run folders.** Replace `runs/history.json` with one folder per hash,
   `out/<hash>/`, that holds the run's config and every file it wrote
   (GitHub issue #8, "Reorganize hash history").

Status: **implemented** (2026-10-25). The first proposal (prompt 1) was
narrowed by prompt 2; see [Changes from the first proposal](#changes-from-the-first-proposal).

## Prompts

**2026-10-25, prompt 1**

> I have 2 problems regarding old implementations:
> -
>
> Currently, we have this parameter precedence:
> Parameter priority (lowest to highest):
> defaults.yaml < --config FILE < --preset < --params FILE.yaml < --set
>
> all methods should be compacted into a config file which is also need to be hashed afterwards. Make as compact as possible without affecting flexibility. This is related to issue 8.
>
> - Instead of putting hashes into a json, create a folder for every hash and put outputs of that command inside it.
>
> Analyze the repo and documentation to gain better knowledge. Also, instead of implementing it directly, give me a plan markdown file.

**2026-10-25, prompt 2**

> The issue number 8 is reorganizing hash history which is the other problem I talked about. By doing this two task, I want to centralize and have clear definition of each run. In each folder, I want clearly to see what parameters did this run used besides defaults. Because including defaults everytime will bloat in my opinion. You can keep changed parameters either as json or as yaml (json is the current approach). If a command fails, don't create any folder or output. Currently writing "python -m cli <hash>" runs with the hash's parameters. This behavior should be preserved. This output will not go to the github so do not worry about the size. They should be directly in out/<hash>. This is because I want to see the output artifacts separately. We should be able to rerun the same hash both using the hash and the config file that is in the folder. -o should just specify the names of the artifacts. Running the same input will be the common case. However, checking if it is the same with the old or not is not necessary. Even it is not intended. Because the pipeline will change over time. Same run will output different items. Implement according to this. No need to complexize things. Also, after implementing, tell me what is old and not needed and can be discarded. Also, no need to run bench. Just run the old history to fill the hashes and generate the examples.

Issue #8 (open): "Reorganize hash history". Body: *"Instead of putting
hashes into a json, create a folder for every hash and put outputs of that
command inside it."*

## What was wrong (found in prompt 1's analysis)

| # | Mechanism (before) | Scope |
|---|---|---|
| 1 | `roadgen/defaults.yaml` | everything |
| 2 | `--config FILE` | everything, same nesting |
| 3 | `--preset NAME` | one generator / map generator |
| 4 | `--params FILE.yaml` | one generator / map generator |
| 5 | `--set KEY=VALUE` | one generator / map generator |
| 6 | dedicated flags (Manhattan's 18, `--seed`, `--junction-margin`, ...) | per command |
| 7 | `--map-set KIND.KEY=VALUE` | pipeline input maps |

- **P1: file contents weren't hashed.** `--params` went into the hash as a
  path string, so changing the file didn't change the hash.
- **P2: `-o` was part of the hash**, and outputs went anywhere under `out/`.
  A later run could overwrite them.
- **P3: the whole effective config was stored.** That is 200+ lines per
  config, mostly defaults.
- **P4: the same value had up to five spellings**, with different hashes.
  For example `--avenues 3` and `--set n_avenues=3`.

## Design as implemented

### The run config (`run.yaml`)

Every recorded run has `out/<hash>/run.yaml`:

```yaml
hash: 3fa2c81b9d04
note: my run
recorded: '2026-10-25T18:00:00+00:00'
cli: python -m cli pipeline lsystem --preset city --maps --seed 7 -o city   # as typed
command: [pipeline, lsystem]
args: {maps: true, output: city, seed: 7}   # arguments that differ from their defaults
config:                                     # parameters that differ from defaults.yaml
  generators:
    lsystem: {preset: city}
```

- **One `config` section for all parameter sources.** `config` uses the
  nesting of `defaults.yaml`. `--config` is stored as its difference from
  the defaults.
- **Parameter options are folded into their section.** `--preset`,
  `--params` (its content), `--set`, Manhattan's flags and `--map-set` are
  written into the section they set: `generators.<name>`, or
  `maps.<kind>.<name>` for maps. A section may name a `preset`, and
  `--config` files may too, which is new.
- **Values equal to the defaults are dropped.** Inside a section with a
  preset, a value is dropped only if it equals the preset's value, so an
  explicit "back to the default" survives.
- **The hash** is sha256 of `command` + `args` + `config`, 12 hex digits.
  `note`, `-v` and `--no-history` aren't hashed. `-o` is an argument, so
  `-o a` and `-o b` are two runs; with a fixed `-o` the folder holds exactly
  the files its `run.yaml` names.
- **Precedence**, lowest first: defaults < preset < `--config` <
  `--params` < flags and `--set`. The only change is that a `--config` value
  now beats a preset. No recorded run depended on the old order.

Code: `cli.compile_spec()` turns parsed arguments into the spec.
`cli.execute()` runs a spec. `cli.namespace_for()` rebuilds the arguments
from parser defaults plus `args`. Every command now goes through
`compile_spec` → `execute`, recorded or not, so the run file is exactly
what ran.

### Run folders (`roadgen/history.py`, `RunStore`)

- **Where.** `out/<hash>/` (`history.dir: out`, or `$ROADGEN_HISTORY`;
  `off` disables recording) holds `run.yaml` and every output.
- **`-o` and output arguments** are only file names. A path is an error.
  Defaults are `<name>[_<preset>]_s<seed>`, `<input>.xodr`, `<input>.png`,
  `<input>.xosc`, `scene.osgt`, ... `simulate` writes esmini's
  `.dat/.csv/.log` into the folder, and a recorded `bench` uses the folder
  as its `out_dir`.
- **No trace on failure.** A command runs in `out/.tmp-<hash>-<pid>/`, which
  is at the same depth, so relative paths inside `.xosc` files stay valid.
  On success it is renamed to `out/<hash>/`, replacing an earlier run of the
  same hash and keeping its note if no new one is given. On failure or an
  exception it is deleted.
- **Rerun.** `python -m cli HASH` (unchanged), `replay HASH [--show]`, and
  the new `python -m cli run out/HASH/run.yaml` (or any hand-written file of
  that form) run the spec with today's code and defaults. No comparison
  with the earlier outputs.
- **`history [--grep]`** lists `out/*/run.yaml`, oldest first.
- **`--no-history`** (and `ROADGEN_HISTORY=off`) makes no folder; `-o` is a
  path as before. Tests run this way (`tests/conftest.py`), and
  `tests/test_history.py` turns recording on in a temporary folder.

### Migration and examples

- **Migration.** `plans/scripts/migrate_history.py` replayed the old
  `runs/history.json` oldest first. It turned `-o` paths into names and
  rewired input paths that an earlier run wrote to that run's folder. It
  skipped the GUI runs (`view`, `simulate --gui`), which need the gui
  service. `out/migration.json` maps old → new hashes.
- **`runs/examples.sh`** now keeps each run's folder in a variable (`$RUN`
  from the logged `run <hash>: out/<hash>` line). Later commands read
  `$RUN/<file>`.
- **`runs/make_readme.py`** writes `runs/README.md` from the run folders.

## Decisions

Made by the user:
- One config with only the parameters that differ from the defaults. The
  format is YAML or JSON (YAML chosen, below).
- Folders directly in `out/<hash>/`, with the command's outputs. They are
  not tracked in git, so size doesn't matter.
- A failed command leaves no folder and no output.
- `python -m cli <hash>` keeps working, and the folder's config file can be
  rerun too.
- `-o` only names the artifacts.
- Rerunning the same inputs is normal: replace, don't compare with the old
  outputs (the pipeline changes over time).
- Keep it simple. Fill the folders by replaying the old history. Don't run
  `bench`.

Left to Claude, with reasons:
- **YAML for `run.yaml`.** It has the same format and nesting as
  `defaults.yaml` and `--config`, so a run file can be read and copied
  as a config.
- **Command-specific switches stay in `args`, keyed by argument name.**
  Only generator and map parameters move into `config`. Turning every flag
  into a config path (`--seed` → `seed`, `--junction-margin` →
  `opendrive.junction_margin`) was in the first proposal; it was dropped
  as unneeded complexity.
- **`-o` is part of the hash.** Then a folder's contents always match its
  `run.yaml`. If `-o` weren't hashed, a rerun with another `-o` would
  change the file names under the same hash.
- **A rerun without `--note` keeps the earlier note**, so a replay doesn't
  erase the description.
- **The `geometry_version` field and the replay warning were removed.** You
  said comparing with old runs isn't wanted. The builder's sidecar still
  records the geometry version.
- **File-content hashing for inputs was dropped** (images, input maps):
  inputs stay plain paths, mostly `out/<hash>/...` of the run that wrote
  them. `--params` content is still in the config, because it is a
  parameter source.
- **`docs/implementation-guide.md` uses `--no-history` in its `cli`
  shorthand.** It is a recorded walkthrough with long file chains;
  `--no-history` keeps its paths valid. The other docs use `-o NAME` and
  `out/<hash>/...`.

## Changes from the first proposal

The first proposal (prompt 1) had these; prompt 2 asked for less, so they
were dropped:
- Folders in `runs/<hash>/` with only `run.yaml` tracked in git → `out/<hash>/`, nothing tracked.
- Full resolved config, hashed → only the difference from the defaults.
- Sections per command (`READS`) and a key-access test → not needed with diffs.
- `@HASH/file` references, `replay --deps`, content hashes of input files → plain paths.
- Output digests, comparison on rerun, the git commit in `run.yaml` → not wanted.
- Failed runs kept in `out/failed/` → nothing kept.
- Legacy-hash lookup → `out/migration.json` only.

## Results

Tests: `docker compose run --rm -T dev python -m pytest -q` (see the summary
of the implementation for the numbers). Migration: see `out/migration.json`.
