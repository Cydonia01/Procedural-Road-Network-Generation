#!/usr/bin/env bash
# Container entrypoint: prepares X11 auth (if a host cookie is mounted) and
# then execs the requested command.
set -euo pipefail

# The host's Xauthority cookie is bound to the host's hostname, which differs
# inside the container. Rewrite it as a wildcard (FamilyWild = ffff) entry so
# X clients in the container can authenticate against the host X server.
if [[ -n "${DISPLAY:-}" && -r /tmp/.host.Xauthority ]]; then
    export XAUTHORITY="${HOME}/.Xauthority"
    touch "${XAUTHORITY}"
    xauth -f /tmp/.host.Xauthority nlist 2>/dev/null \
        | sed -e 's/^..../ffff/' \
        | xauth -f "${XAUTHORITY}" nmerge - 2>/dev/null || true
fi

# VGL_DISPLAY set (gui-mac): render OpenGL off-screen in a private Xvfb and let
# VirtualGL ship the frames to $DISPLAY, which can't do Mesa GLX itself.
if [[ -n "${VGL_DISPLAY:-}" ]] && command -v vglrun >/dev/null; then
    Xvfb "${VGL_DISPLAY}" -screen 0 1920x1080x24 +extension GLX -nolisten tcp \
        >/dev/null 2>&1 &
    for _ in $(seq 50); do
        [[ -e "/tmp/.X11-unix/X${VGL_DISPLAY#:}" ]] && break
        sleep 0.1
    done
    exec vglrun "$@"
fi

exec "$@"
