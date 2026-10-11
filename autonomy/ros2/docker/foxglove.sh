#!/usr/bin/env bash
# Run this on the MAC, not in the container. Unlike ros2/scripts/*, which run
# inside, this one drives the container from the host: it checks the container
# can actually serve the bridge, starts it if needed, builds, and launches.
#
# Foxglove itself runs on the Mac and connects to ws://localhost:8765, which
# compose.yaml publishes. No noVNC desktop is involved.
#
#   ./foxglove.sh                        # kinematic demo, lidar on
#   ./foxglove.sh v_max:=12.0 seed:=7    # extra args go to the launch file
set -e

CONTAINER=lhr-autonomy
HERE=$(cd "$(dirname "$0")" && pwd)

# Layout IDs belong to a Foxglove account, so keep them local to each Mac.
# An exported override takes precedence over the saved default.
layout_override=${FOXGLOVE_LAYOUT_ID:-}
if [ -f "$HERE/foxglove.local.env" ]; then
    source "$HERE/foxglove.local.env"
fi
FOXGLOVE_LAYOUT_ID=${layout_override:-${FOXGLOVE_LAYOUT_ID:-}}

if [ -z "$FOXGLOVE_LAYOUT_ID" ]; then
    echo "Set FOXGLOVE_LAYOUT_ID in $HERE/foxglove.local.env first." >&2
    echo "Import ../foxglove/lhr_sim.json in Foxglove, save it, and copy its layoutId." >&2
    exit 1
fi
if [[ ! "$FOXGLOVE_LAYOUT_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "FOXGLOVE_LAYOUT_ID must be the ID, not a full URL." >&2
    exit 1
fi

rebuild() {
    echo "  cd $HERE && docker compose up -d --build" >&2
    exit 1
}

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "No $CONTAINER container. Create it:" >&2
    rebuild
fi

# Port publishing is fixed when the container is created, so a container made
# before 8765 was added to compose.yaml cannot serve the bridge however it is
# started, and `docker start` will happily resume it. Checked here because the
# symptom otherwise is Foxglove failing to connect with the stack apparently up.
if ! docker inspect -f '{{index .HostConfig.PortBindings "8765/tcp"}}' \
        "$CONTAINER" 2>/dev/null | grep -q 8765; then
    echo "$CONTAINER does not publish 8765, so Foxglove cannot reach it." >&2
    echo "It predates that port being added. Recreate it:" >&2
    rebuild
fi

if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "true" ]; then
    echo "starting $CONTAINER"
    docker start "$CONTAINER" >/dev/null
fi

# Same class of staleness: the bridge is lhr_demo's exec_depend, the image
# names it explicitly, and an image built before that fails only at launch.
if ! docker exec "$CONTAINER" \
        dpkg -s ros-jazzy-foxglove-bridge >/dev/null 2>&1; then
    echo "ros-jazzy-foxglove-bridge is missing from the image." >&2
    echo "It predates that package being added. Rebuild:" >&2
    rebuild
fi

if [[ "$*" == *plant:=bobsim* ]]; then
    if ! docker exec "$CONTAINER" test -d /opt/lhr/simulation/bobsim/_0_Utils/dyn_py; then
        echo "BobSim is not mounted. Initialize simulation/bobsim and recreate the container:" >&2
        rebuild
    fi
    if ! docker exec "$CONTAINER" python3 -c 'import pandas' >/dev/null 2>&1; then
        echo "BobSim dependencies are missing. Rebuild the container:" >&2
        rebuild
    fi
fi

# A normal run is a gated one: metrics decides when it is over, so a completed
# lap ends it after about 17 s and the viewer drops its connection. That is
# right for the gate and useless for looking at the car. Turning metrics off
# leaves nothing to publish /lhr/metrics/lap_complete, so the mission never
# completes, the car keeps driving, and no process exit emits Shutdown: the run
# lasts until Ctrl-C. Pass enable_metrics:=true to get the gated behaviour back.
if [[ "$*" != *enable_metrics:=* ]]; then
    set -- enable_metrics:=false "$@"
fi

# build.sh is what installs the car mesh into share/, which is what makes its
# package:// URI resolve, so it runs every time rather than being left to
# whoever remembers.
docker exec -it -u ubuntu "$CONTAINER" bash -lc '
    set -e
    cd ~/autonomy/ros2
    ./scripts/build.sh'

# Foxglove retries the connection while the ROS launch brings up the bridge.
# Selecting the saved Z-up layout avoids restoring an older Y-up scene.
open -a Foxglove "foxglove://open?ds=foxglove-websocket&ds.url=ws%3A%2F%2Flocalhost%3A8765&layoutId=$FOXGLOVE_LAYOUT_ID"
echo "Foxglove opened with the saved layout. Runs until Ctrl-C."

# Pass launch arguments as positional parameters, so quoting survives the
# host/container boundary rather than turning argument text into shell code.
exec docker exec -it -u ubuntu "$CONTAINER" bash -lc '
    set -e
    cd ~/autonomy/ros2
    exec ./scripts/run_demo.sh foxglove:=true lidar:=true "$@"' bash "$@"
