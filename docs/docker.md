# Docker

The image has Python 3.12, every dependency from `pyproject.toml`, the dev
tooling, and esmini v3.9.0 under `/opt/esmini`. Everything in the docs works
inside it unchanged. First steps are in
[Getting started](getting-started.md#docker-recommended).

```bash
docker compose build                               # first time, or after dependency changes
docker compose run --rm dev                        # interactive shell
docker compose run --rm -T dev pytest              # one command, non-interactive
docker compose run --rm gui python -m cli view out/<hash>/net.xodr
```

| Service | Use for | Platforms |
|---------|---------|-----------|
| `dev` | Tests, pipeline, `simulate`, `drive-check`: anything without a window | Linux, macOS, Windows |
| `gui` | `view`, `simulate --gui` on a Linux desktop (X11 or XWayland), GPU accelerated | Linux |
| `gui-mac` | `view`, `simulate --gui` via XQuartz, software rendered | macOS |

## How it works

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage: `esmini` (download) → `deps` (virtualenv from `pyproject.toml`) → `runtime` → `dev` |
| `compose.yaml` | Services `dev`, `gui`, `gui-mac` |
| `requirements-dev.txt` | Container-only tooling: pytest-cov, ruff, mypy, ipython, ipykernel |
| `docker/entrypoint.sh` | Makes the host's X11 cookie usable in the container |
| `.dockerignore` | Allowlist: the build sees only `pyproject.toml`, `requirements-dev.txt` and `docker/` |
| `.devcontainer/devcontainer.json` | VS Code Dev Containers config (uses `dev`) |

- **The source is mounted, not copied.** The repo is at `/workspace` with
  `PYTHONPATH=/workspace`, so edits apply immediately. Rebuild only when
  `pyproject.toml`, `requirements-dev.txt` or the `Dockerfile` change.
- **Dependencies come from `pyproject.toml`.** `[project.dependencies]` goes
  into the image, and the `dev` stage adds the `[dev]` extra plus
  `requirements-dev.txt`. There is no separate requirements list.
- **esmini.**
  - **Binaries** come from `esmini-bin_Linux.zip`, the only bundle with
    `libesminiRMLib.so`, which drive-check needs.
  - **Models and samples** come from `esmini-demo_Linux.zip`.
  - **Environment:** `ESMINI_HOME=/opt/esmini`,
    `ESMINI_RESOURCES=/opt/esmini/resources`, and
    `ESMINI_LIB=/opt/esmini/bin/libesminiLib.so`.
- **Runs as you.** The container user has UID/GID 1000, so files in `out/`
  stay yours. If your user differs, create `.env` with `LOCAL_UID=<id -u>`
  and `LOCAL_GID=<id -g>` and rebuild (Linux only).
- **Platform.** esmini ships only x86_64 Linux binaries, so the image is
  always `linux/amd64`.
- **Schema check.** Put the XSD inside the repo and pass its container path:
  `docker compose run --rm -e OPENDRIVE_XSD=/workspace/schemas/opendrive_16_core.xsd dev ...`

## Linux GUI (`gui`)

The service forwards:

- the X socket;
- your `$XAUTHORITY` cookie, which the entrypoint rewrites to be valid inside
  the container (no `xhost +` needed);
- `/dev/dri` for hardware OpenGL.

On SELinux hosts (Fedora, RHEL), labels are disabled for this service so it
can reach the X socket.

Troubleshooting:

- **No GPU, WSL or a VM:** remove the `devices:` entry and set
  `LIBGL_ALWAYS_SOFTWARE=1`.
- **Still no display:** run `xhost +local:` on the host.
- **Which renderer:** `docker compose run --rm gui glxinfo -B`.

## macOS (`dev` and `gui-mac`)

**Apple Silicon.** The amd64 image runs through Rosetta. Enable *Docker
Desktop → Settings → General → Use Rosetta for x86_64/amd64 emulation*.
Everything works, but timing and memory differ from native runs, so measure
those on Linux.

GUI setup:

1. Install XQuartz (`brew install --cask xquartz`), then log out and back in.
2. In XQuartz → Settings → Security, enable **Allow connections from network
   clients**. Then quit XQuartz (⌘Q) and start it again.
3. With XQuartz running, run `xhost +localhost` once per session
   (`/opt/X11/bin/xhost` if it isn't on your `PATH`).
4. `docker compose run --rm gui-mac python -m cli view out/<hash>/<name>.xodr`

**Why it's slow.** Mesa can't create OpenGL contexts on XQuartz, and
XQuartz's own GLX only offers OpenGL 1.4, which esmini rejects. The
workaround:

1. The `gui-mac` entrypoint starts a private Xvfb on `VGL_DISPLAY` (`:99`).
2. esmini renders there with llvmpipe through [VirtualGL](https://virtualgl.org).
3. VirtualGL copies each frame to the XQuartz window.

esmini needs an explicit `--window` size for this, which `view` and
`simulate --gui` pass. For heavy interactive use, a
[native install](getting-started.md#native) with `esmini-bin_macOS.zip` is
smoother.

Troubleshooting:

- **`Authorization required…`, or `odrviewer` segfaults silently.**
  XQuartz isn't running or refuses the container.
  - Start XQuartz and rerun `xhost +localhost`, adding `xhost +127.0.0.1` if
    needed.
  - `docker compose run --rm gui-mac xeyes` is a quick test.
- **`Failed to create window … Failed 2nd attempt to create viewer`.**
  XQuartz is in a bad state, usually after a viewer was closed or its
  container stopped. Restart XQuartz, rerun `xhost +localhost`, and retry.

## Without a display

`xvfb-run` renders off-screen:

```bash
docker compose run --rm dev xvfb-run -a odrviewer --odr out/<hash>/net.xodr --capture_screen
```

Pass `--path $ESMINI_RESOURCES/models` to `odrviewer` or `esmini` to see real
vehicle models instead of boxes.

## Updating esmini

```bash
docker compose build --build-arg ESMINI_VERSION=vX.Y.Z
```

Or change the default in the `Dockerfile`. After upgrading, rerun
`drive-check`: its junction-selector angles were calibrated against esmini
3.9.
