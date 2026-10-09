# Docker environment

The container has Python 3.12, every dependency from `pyproject.toml`, the
dev tooling, and esmini v3.9.0 preinstalled under `/opt/esmini`. Everything
in the docs works inside it unchanged.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build: `esmini` (download) → `deps` (virtualenv from `pyproject.toml`) → `runtime` → `dev` |
| `compose.yaml` | Services `dev`, `gui` (Linux), `gui-mac` (macOS) |
| `requirements-dev.txt` | Container-only tooling: pytest-cov, ruff, mypy, ipython, ipykernel |
| `docker/entrypoint.sh` | Makes the host's X11 cookie usable inside the container |
| `.dockerignore` | Allowlist: the build only sees `pyproject.toml`, `requirements-dev.txt` and `docker/` |
| `.devcontainer/devcontainer.json` | VS Code Dev Containers config (uses `dev`) |

## How it works

- **The source is bind-mounted, not copied.** The repo is mounted at
  `/workspace` and `PYTHONPATH=/workspace`, so `python -m cli` and `pytest`
  see your edits immediately. Rebuild only when `pyproject.toml`,
  `requirements-dev.txt` or the `Dockerfile` change.
- **Dependencies come from `pyproject.toml`.** `[project.dependencies]`
  goes into the image, and the `dev` stage adds the `[dev]` extra plus
  `requirements-dev.txt`. Don't maintain a separate requirements list.
- **esmini.** The binaries come from the `esmini-bin_Linux.zip` release (the
  only bundle that includes `libesminiRMLib.so`, which drive-check needs).
  Vehicle models and sample files come from `esmini-demo_Linux.zip`.
  Preset environment variables:

  | Variable | Value |
  |----------|-------|
  | `ESMINI_HOME` | `/opt/esmini` |
  | `ESMINI_RESOURCES` | `/opt/esmini/resources` (samples, models) |
  | `ESMINI_LIB` | `/opt/esmini/bin/libesminiLib.so` |

- **Runs as you.** The container user `dev` has UID/GID 1000 by default.
  Files written to `out/` stay owned by you.
- **Platform.** esmini only ships x86_64 Linux binaries, so the image is
  always `linux/amd64`.

## Usage

```bash
docker compose build                               # first time, or after dependency changes
docker compose run --rm dev                        # interactive shell
docker compose run --rm dev pytest                 # one-off command
docker compose run --rm dev python -m cli pipeline manhattan --preset manhattan_like --simulate
docker compose run --rm gui python -m cli view out/manhattan_like_s1.xodr
```

| Service | Use for | Platforms |
|---------|---------|-----------|
| `dev` | Tests, pipeline, `simulate`, `drive-check`, `bench`: anything without a window | Linux, macOS, Windows |
| `gui` | `view`, `simulate --gui` on a Linux desktop (X11 or XWayland), GPU accelerated | Linux |
| `gui-mac` | `view`, `simulate --gui` via XQuartz, software rendered | macOS |

**Different UID on Linux.** If your user isn't UID/GID 1000, create `.env`
with `LOCAL_UID=<id -u>` and `LOCAL_GID=<id -g>`, then rebuild. macOS doesn't
need this.

**Schema check.** Put the XSD inside the repo and pass the container path:
`docker compose run --rm -e OPENDRIVE_XSD=/workspace/schemas/opendrive_16_core.xsd dev ...`

## Linux GUI (`gui`)

The service forwards:
- the X socket;
- your `$XAUTHORITY` cookie, which the entrypoint rewrites so it's valid
  inside the container (no `xhost +` needed);
- `/dev/dri` for hardware OpenGL.

On SELinux hosts (Fedora/RHEL), labels are disabled for this service so it
can reach the X socket.

Troubleshooting:
- **No GPU, WSL or a VM:** remove the `devices:` entry and set
  `LIBGL_ALWAYS_SOFTWARE=1`.
- **Still can't connect to the display:** run `xhost +local:` on the host as
  a fallback.
- **Checking OpenGL:** `docker compose run --rm gui glxinfo -B` shows the
  renderer.

## macOS (`dev` and `gui-mac`)

**Apple Silicon.** The amd64 image runs through Rosetta. Enable *Docker
Desktop → Settings → General → Use Rosetta for x86_64/amd64 emulation*.
Everything works, but performance numbers differ from native runs, so run
[benchmarks](benchmarking.md#getting-meaningful-numbers) on Linux.

GUI setup:

1. Install XQuartz (`brew install --cask xquartz`, or the `.pkg` from
   xquartz.org), then log out and back in.
2. XQuartz → Settings → Security → enable **Allow connections from network
   clients**, then quit XQuartz (⌘Q) and start it again.
3. With XQuartz running, run `xhost +localhost` once per session (use
   `/opt/X11/bin/xhost` if it isn't on your `PATH`).
4. `docker compose run --rm gui-mac python -m cli view out/<name>.xodr`

Mesa can't create OpenGL contexts on XQuartz ("No matching fbConfigs or
visuals found"), and XQuartz's own indirect GLX only reports OpenGL 1.4,
which esmini rejects. So the `gui-mac` entrypoint starts a private Xvfb on
`VGL_DISPLAY` (`:99`), esmini renders there with llvmpipe through
[VirtualGL](https://virtualgl.org), and VirtualGL copies each frame to the
XQuartz window. esmini needs an explicit `--window` size for this (`view`
and `simulate --gui` pass one). It works, but it is slower than native. For
heavy interactive use on a Mac, the
[native install](getting-started.md#option-b-native-install) with
`esmini-bin_macOS.zip` is smoother.

Troubleshooting:

- **`Authorization required, but no authorization protocol specified`**, or
  `odrviewer` segfaults with no output: XQuartz isn't running or is refusing
  the container. Start XQuartz and rerun `xhost +localhost`. If that still
  fails, also run `xhost +127.0.0.1`. `docker compose run --rm gui-mac xeyes`
  is a quick connectivity test.
- **`Failed to create window ... Failed 2nd attempt to create viewer`**:
  XQuartz is in a bad state. This happens most often after a previous viewer
  window was closed or the container was stopped while the viewer was
  running. Quit XQuartz (⌘Q), start it again, rerun `xhost +localhost`, and
  retry. Running from an interactive `docker compose run --rm gui-mac` shell
  works the same way as passing the command directly.

## Without any display

`xvfb-run` is in the image for off-screen rendering:

```bash
docker compose run --rm dev xvfb-run -a odrviewer --odr out/net.xodr --capture_screen
```

Pass `--path $ESMINI_RESOURCES/models` to `odrviewer`/`esmini` to see real
vehicle models instead of boxes.

## Updating esmini

```bash
docker compose build --build-arg ESMINI_VERSION=vX.Y.Z
```

Or bump the default in the `Dockerfile`. After upgrading, rerun `drive-check`,
because its junction-selector angles were calibrated against esmini 3.9.
