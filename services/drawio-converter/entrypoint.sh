#!/bin/bash
set -e

cleanup() {
    echo "Stopping Xvfb"
    pkill Xvfb || true
    rm -f /tmp/.X99-lock
}

trap cleanup EXIT

# Remove any existing lock file
rm -f /tmp/.X99-lock

# Start Xvfb
Xvfb :99 -ac &
export XVFB_PID=$!

# Wait for Xvfb to be ready
for i in $(seq 1 10)
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

# Run the Python script
exec python -m drawio_converter.drawio_converter
