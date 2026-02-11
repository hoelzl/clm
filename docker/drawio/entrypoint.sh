#!/bin/bash
set -e

cleanup() {
    echo "Stopping Xvfb"
    pkill Xvfb || true
    rm -f /tmp/.X99-lock

    echo "Stopping D-Bus"
    pkill dbus-daemon || true
    rm -f /var/run/dbus/pid
}

trap cleanup EXIT

# Start D-Bus
echo "Starting D-Bus daemon"
mkdir -p /var/run/dbus
dbus-daemon --system --fork

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
