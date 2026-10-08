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

exec "$@"
