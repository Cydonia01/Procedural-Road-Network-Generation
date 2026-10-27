# CLAUDE.md

Project rules (architecture, tech stack, OpenDRIVE conventions, coding rules)
live in the Copilot instructions and apply here too:

@.github/copilot-instructions.md

## Running things

- The host has no Python dependencies; run everything in the dev container:
  `docker compose run --rm -T dev python -m pytest -q`
  (`-T` for non-interactive use). Same for `python -m cli ...` and `ruff check`.
- `tests/test_runner.py::test_monitor_memory_cap_and_peak` is known to fail in
  this environment; it is not caused by code changes.
- Generated files go under `out/` (git-ignored).
- Every successful CLI run gets a folder `out/<hash>/` with its outputs
  (`-o` only names them) and `run.yaml`: the command plus only the args and
  parameters that differ from defaults (`roadgen/history.py`). `python -m cli
  HASH` or `python -m cli run out/HASH/run.yaml` reruns it. Use
  `--no-history` for throwaway runs (no folder, `-o` is a path); tests run
  with recording off (`tests/conftest.py`).

## Where things are

- All default parameters: `roadgen/defaults.yaml`. Params dataclasses read
  their field defaults from it (`roadgen/config.py`); a test checks they match,
  so new params go in both places.
- Road generators: `roadgen/generators/` (registry in `base.py`).
- Input maps (terrain, elevation, population, style): `roadgen/maps/` —
  separate per-kind registry, same params/preset pattern. `lsystem` reads
  them (`uses_maps`, `map_bounds`, `map_kinds`, `generate(..., maps=MapSet)`);
  the CLI builds them before generating. Without maps lsystem output must
  stay identical, and new lsystem features must default to off:
  `tests/test_lsystem_identity.py` checks the existing presets against
  fingerprints recorded before stage C.
- lsystem is the only city generator (the `manhattan` generator was removed
  in stage C): grids, pattern fields (`patterns.py`) and graph transforms
  (`roadgen/generators/transforms/`).
- Docs: `docs/` (index in `docs/README.md`, by task and by source folder).
  Each topic has one home page; other pages link to it instead of repeating
  it (parameters and run folders: `cli-reference.md`; module map and output
  files: `architecture.md`). Update the matching page, the CLI reference and
  the index when adding commands or modules. Keep stage labels and
  "fixed in" notes out of the reference pages; history goes in
  `docs/development.md#project-history` and `plans/`.
- Reference papers: `files/*.pdf` (`file1.pdf` is Parish & Müller 2001).

## Plans and prompts (traceability)

- Every plan, design discussion or review gets its own Markdown file in
  `plans/`, named `YYYY-MM-DD-<short-slug>.md`. Discussions (pros/cons,
  options, decisions) are saved too, not only final plans. Add each file to
  the index in `plans/README.md`.
- Each file starts with a **Prompts** section quoting, verbatim and dated,
  the user prompts that led to it. Follow-up prompts that change a plan are
  appended there, and the plan is updated rather than duplicated.
- Record the decisions the user made, and the ones left to Claude with the
  reasoning, in a **Decisions** section.
- When implementation deviates from a plan, update the plan file in the
  same change and note why.
- Small helper scripts used to produce numbers quoted in a plan go in
  `plans/scripts/`.

## Conventions beyond the Copilot file

- New pluggable components follow the existing pattern: ABC + `register`
  decorator, frozen params dataclass with presets, defaults in
  `defaults.yaml`, deterministic for a seed, CLI subcommand + pipeline flag.
- Match the existing style: `Optional[...]`, short comments with units in
  brackets (`[m]`, `[m/s]`), `log` via `logging.getLogger(__name__)`.
