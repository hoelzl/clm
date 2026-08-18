#!/bin/bash
set -e

cleanup() {
    echo "Stopping Xvfb"
    pkill Xvfb || true
    rm -f /tmp/.X99-lock

    echo "Stopping D-Bus"
    pkill dbus-daemon || true
}

trap cleanup EXIT

# The container runs unprivileged, and often under a uid that exists only on
# the host (the executor passes the host uid so rendered diagrams land in the
# mounted source tree with the right ownership). Two things need that uid to
# look like a real user:
#
#   * Electron calls os.userInfo() at startup and throws without a password
#     database entry — the failure mode is a *hang*, not a message;
#   * D-Bus refuses to start for an unknown uid.
#
# /etc/passwd is writable in this image (and every setuid bit is stripped, so
# that is not an escalation path). XDG_RUNTIME_DIR must be *owned* by the
# running uid, not merely writable, so it is created here rather than baked in.
if ! getent passwd "$(id -u)" >/dev/null 2>&1; then
    echo "clm:x:$(id -u):$(id -g):CLM worker:${HOME:-/home/clm}:/bin/bash" >> /etc/passwd         || echo "WARNING: uid $(id -u) is not in /etc/passwd and it is not writable" >&2
fi
XDG_RUNTIME_DIR="/tmp/xdg-$(id -u)"
mkdir -p "${XDG_RUNTIME_DIR}"
chmod 0700 "${XDG_RUNTIME_DIR}"
export XDG_RUNTIME_DIR
export HOME="${HOME:-/home/clm}"

# Start a *session* D-Bus and export its address. The system bus needs root
# and a writable /var/run/dbus; the container is unprivileged since S10
# (#798), and Electron only ever wanted a session bus.
echo "Starting D-Bus session daemon"
DBUS_SESSION_BUS_ADDRESS="$(dbus-daemon --session --fork --print-address)"
export DBUS_SESSION_BUS_ADDRESS

# Remove any existing lock file
rm -f /tmp/.X99-lock

# Start Xvfb
echo "Starting Xvfb"
Xvfb :99 -ac &
export XVFB_PID=$!

# Wait for Xvfb to be ready
for _ in $(seq 1 10)
do
    if xdpyinfo -display :99 >/dev/null 2>&1
    then
        break
    fi
    echo "Waiting for Xvfb..."
    sleep 1
done

if ! xdpyinfo -display :99 >/dev/null 2>&1
then
    echo "Xvfb failed to start"
    exit 1
fi

export DISPLAY=:99
export DRAWIO_EXECUTABLE=/opt/drawio/drawio

# Run the DrawIO worker (SQLite mode)
echo "Running DrawIO worker"
exec python -m clm.workers.drawio
