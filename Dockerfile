# syntax=docker/dockerfile:1.7
#
# Development / runtime image for the Procedural Road Network Generation project.
#
# Stages:
#   esmini   - downloads and unpacks the prebuilt esmini release (binaries + resources)
#   deps     - builds an isolated virtualenv with the dependencies from pyproject.toml
#   runtime  - slim image: Python + esmini + the X11/OpenGL libs esmini needs
#   dev      - runtime + dev tooling (tests, linters, debugging utilities)
#
# The prebuilt esmini binaries are x86_64-only and require glibc >= 2.35, so
# the base must be Debian bookworm (2.36) / Ubuntu 22.04 or newer, and the
# image is always built for linux/amd64 (Apple Silicon runs it via Rosetta).

ARG PYTHON_VERSION=3.12
ARG DEBIAN_RELEASE=bookworm
ARG ESMINI_VERSION=v3.9.0

# -----------------------------------------------------------------------------
FROM debian:${DEBIAN_RELEASE}-slim AS esmini

ARG ESMINI_VERSION
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip

# Binaries come from the "bin" bundle: unlike "demo", it also ships
# libesminiRMLib.so, which roadgen's drive-check loads via ctypes.
# Resources (vehicle models, sample .xodr/.xosc) come from the "demo" bundle.
ARG ESMINI_URL=https://github.com/esmini/esmini/releases/download/${ESMINI_VERSION}
RUN curl -fsSL --retry 3 -o /tmp/bin.zip "${ESMINI_URL}/esmini-bin_Linux.zip" \
    && curl -fsSL --retry 3 -o /tmp/demo.zip "${ESMINI_URL}/esmini-demo_Linux.zip" \
    && unzip -q /tmp/bin.zip -d /tmp \
    && unzip -q /tmp/demo.zip 'esmini-demo/resources/*' 'esmini-demo/scripts/*' -d /tmp \
    && mv /tmp/esmini /opt/esmini \
    && mv /tmp/esmini-demo/resources /tmp/esmini-demo/scripts /opt/esmini/ \
    # Static libs are only needed to link C++ code against esmini.
    && rm -f /opt/esmini/bin/*.a \
    && rm -rf /tmp/*.zip /tmp/esmini-demo

# VirtualGL, for the macOS GUI: XQuartz can't host Mesa's GLX contexts, so
# esmini renders into an in-container Xvfb and VirtualGL copies the frames to
# the XQuartz window.
ARG VIRTUALGL_VERSION=3.1.1
RUN curl -fsSL --retry 3 -o /tmp/virtualgl.deb \
    "https://github.com/VirtualGL/virtualgl/releases/download/${VIRTUALGL_VERSION}/virtualgl_${VIRTUALGL_VERSION}_amd64.deb"

# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# pyproject.toml is the single source of truth for dependencies. Only the
# dependency lists are installed here; the roadgen source itself is
# bind-mounted at /workspace and found through PYTHONPATH, so code edits never
# require a rebuild (and only pyproject.toml changes invalidate this layer).
COPY pyproject.toml /tmp/pyproject.toml
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
project = tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]
print("\n".join(project.get("dependencies", [])))
PY
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel \
    && pip install -r /tmp/requirements.txt

# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS runtime

ARG ESMINI_VERSION
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=${USER_UID}

LABEL org.opencontainers.image.title="procedural-road-network-generation" \
      org.opencontainers.image.description="Procedural OpenDRIVE road network generation + esmini simulation" \
      org.esmini.version="${ESMINI_VERSION}"

# Runtime libraries for esmini/odrviewer (OpenSceneGraph is statically linked;
# it only needs X11 + OpenGL + fontconfig), Mesa drivers for hardware or
# software rendering, and Xvfb for off-screen rendering when no display exists.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglx-mesa0 \
        libgl1-mesa-dri \
        libx11-6 \
        libxrandr2 \
        libxinerama1 \
        libfontconfig1 \
        fonts-dejavu-core \
        zlib1g \
        xvfb \
        xauth \
        tini

# Reuse an existing group if the GID is taken (e.g. macOS "staff" = 20).
RUN (getent group "${USER_GID}" >/dev/null || groupadd --gid "${USER_GID}" "${USERNAME}") \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USERNAME}"

COPY --from=esmini /opt/esmini /opt/esmini
COPY --from=deps /opt/venv /opt/venv
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

ENV PATH="/opt/venv/bin:/opt/esmini/bin:${PATH}" \
    ESMINI_HOME=/opt/esmini \
    ESMINI_LIB=/opt/esmini/bin/libesminiLib.so \
    ESMINI_RESOURCES=/opt/esmini/resources \
    LD_LIBRARY_PATH=/opt/esmini/bin \
    PYTHONPATH=/workspace \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /workspace
USER ${USERNAME}

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["bash"]

# -----------------------------------------------------------------------------
FROM runtime AS dev

USER root
# apt resolves the VirtualGL .deb's own dependencies (libegl1, libxv1, ...).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=bind,from=esmini,source=/tmp/virtualgl.deb,target=/tmp/virtualgl.deb \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        make \
        less \
        mesa-utils \
        x11-apps \
        /tmp/virtualgl.deb

# Project dev extras (pyproject [dev]) + container-only tooling.
COPY pyproject.toml /tmp/pyproject.toml
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN python - <<'PY' >> /tmp/requirements-dev.txt
import tomllib
project = tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]
print("\n".join(project.get("optional-dependencies", {}).get("dev", [])))
PY
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements-dev.txt

ARG USERNAME=dev
USER ${USERNAME}
