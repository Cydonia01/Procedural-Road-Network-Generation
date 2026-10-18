# CLAUDE.md

Project rules (architecture, tech stack, OpenDRIVE conventions, coding rules)
live in the Copilot instructions and apply here too:

@.github/copilot-instructions.md

## Running things

- The host has no Python dependencies; run everything in the dev container:
  `docker compose run --rm -T dev python -m pytest -q`
  (`-T` for non-interactive use). Same for `python -m cli ...` and `ruff check`.
- `tests/test_bench.py::test_monitor_memory_cap_and_peak` is known to fail in
  this environment; it is not caused by code changes.
- Generated files go under `out/` (git-ignored).
- Every successful CLI run is recorded in `runs/history.json` under a hash
  of its inputs (`roadgen/history.py`; `python -m cli HASH` replays it). Use
  `--no-history` for throwaway runs; tests are redirected by `tests/conftest.py`.

## Where things are

- All default parameters: `roadgen/defaults.yaml`. Params dataclasses read
  their field defaults from it (`roadgen/config.py`); a test checks they match,
  so new params go in both places.
- Road generators: `roadgen/generators/` (registry in `base.py`).
- Input maps (terrain, elevation, population): `roadgen/maps/` — separate
  per-kind registry, same params/preset pattern. `lsystem` reads them
  (`uses_maps`, `map_bounds`, `generate(..., maps=MapSet)`); the CLI builds
  them before generating. Without maps lsystem output must stay identical.
- Docs: `docs/` (index in `docs/README.md`). Update the matching page, the
  CLI reference and the README when adding commands or modules.
- Reference papers: `files/*.pdf` (`file1.pdf` is Parish & Müller 2001).

## Conventions beyond the Copilot file

- New pluggable components follow the existing pattern: ABC + `register`
  decorator, frozen params dataclass with presets, defaults in
  `defaults.yaml`, deterministic for a seed, CLI subcommand + pipeline flag.
- Match the existing style: `Optional[...]`, short comments with units in
  brackets (`[m]`, `[m/s]`), `log` via `logging.getLogger(__name__)`.
