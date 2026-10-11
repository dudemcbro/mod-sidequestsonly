#!/usr/bin/env bash
# Runs the end-to-end test in a throwaway container from the prebuilt test server image
# (apps/test-server-image in azerothcore-wotlk-playerbots-custom): mounts this checkout,
# lets the image build the module and start the servers (the worldserver imports the
# module's SQL), then runs testclient/sqo_smoke.py inside it.
#
#   IMAGE   image to use     (default: ghcr.io/dudemcbro/acore-test-server:latest)
#   ENGINE  podman or docker (default: podman)
#   KEEP=1  leave the container running afterwards, with ports 3724 and 8085 published
#   SELINUX_LABEL  label option for the module mount. Default Z, or empty when the checkout
#                  is on a drive without SELinux labels (NTFS, exFAT); labeling is then
#                  switched off for the container instead.
#
# Extra arguments go to sqo_smoke.py, e.g. --verbose.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(cd "$HERE/../.." && pwd)"
IMAGE="${IMAGE:-ghcr.io/dudemcbro/acore-test-server:latest}"
ENGINE="${ENGINE:-podman}"
NAME="sqo-test-$$"

options=()
if [ "${KEEP:-0}" = 1 ]; then
    options+=(-p 3724:3724 -p 8085:8085)
fi

if [ -z "${SELINUX_LABEL+set}" ]; then
    case "$(findmnt -n -o FSTYPE --target "$MODULE_DIR" 2>/dev/null)" in
        ntfs*|fuseblk|exfat|vfat|msdos) SELINUX_LABEL="" ;;
        *) SELINUX_LABEL="Z" ;;
    esac
fi
IN=/opt/acore/modules/mod-sidequestsonly
volume="$MODULE_DIR:$IN"
if [ -n "$SELINUX_LABEL" ]; then
    volume="$volume:$SELINUX_LABEL"
else
    options+=(--security-opt label=disable)
fi

"$ENGINE" run -d --name "$NAME" "${options[@]}" \
    -e ACORE_ACCOUNTS="admin:admin:3 player:player" \
    -v "$volume" "$IMAGE" >/dev/null
if [ "${KEEP:-0}" != 1 ]; then
    trap '"$ENGINE" rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
fi

echo "$NAME: building the module and starting the server..."
ready=0
for _ in $(seq 1 900); do
    if "$ENGINE" exec "$NAME" grep -q "ready\.\.\." /opt/acore/server/logs/Server.log 2>/dev/null; then
        ready=1
        break
    fi
    if [ "$("$ENGINE" container inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
        break
    fi
    sleep 2
done
if [ "$ready" != 1 ]; then
    echo "$NAME: server did not start; last log lines:" >&2
    "$ENGINE" logs "$NAME" 2>&1 | tail -n 40 >&2
    exit 1
fi

# On a brand-new image volume the authserver can reach the realm list before the image has
# inserted its first row, then exit while the worldserver continues (pgrep still finds it
# as a zombie, so check its port). Start it again; on normal runs this is a no-op.
listening() {
    "$ENGINE" exec "$NAME" bash -c 'exec 3<>/dev/tcp/127.0.0.1/3724' 2>/dev/null
}
sleep 3
if ! listening; then
    "$ENGINE" exec "$NAME" bash -lc \
        "tmux new-window -t acore -n auth-retry -c /opt/acore/server/bin './authserver -c /opt/acore/server/etc/authserver.conf'"
fi
for _ in $(seq 1 60); do
    listening && break
    sleep 1
done

"$ENGINE" exec "$NAME" grep -h "mod-sidequestsonly" /opt/acore/server/logs/Server.log || true
"$ENGINE" exec "$NAME" python3 "$IN/tools/test-server/testclient/sqo_smoke.py" "$@"
