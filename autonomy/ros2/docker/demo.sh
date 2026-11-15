#!/usr/bin/env bash
# Run this on the MAC, like foxglove.sh. Demo runs of the full Gazebo stack:
# the Gazebo GUI on the noVNC desktop, the Foxglove bridge on
# ws://localhost:8765, and a truth frame for ../foxglove/lhr_gazebo_truth.json.
#
#   ./demo.sh lap       autocross seed 1, stops by itself after the finish
#   ./demo.sh fault     oval at 8 m/s; steer_freeze fires at 7.6 m/s, safety stops the car
#   ./demo.sh loop      rules_autocross seed 1, laps until stopped
#   ./demo.sh extreme   #218's extreme_combined course, laps until stopped
#   ./demo.sh fsi       #223's real FSI layout, laps until stopped
#   ./demo.sh fsg       #223's real FSG layout, laps until stopped
#   ./demo.sh <run> paused   start with the car on the line and the sim paused
#   ./demo.sh pause | resume | stop
#
# Gazebo: http://localhost:6080/vnc.html?autoconnect=true&resize=scale
# Ctrl-C only ends the event printout; the sim runs until "stop".
set -e
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH

CONTAINER=lhr-autonomy
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
# The gate stack (testing/tools/gate/stack.args). Gazebo on a Mac renders on
# the CPU and drops LiDAR frames, hence the documented 1.5 s timeout (#160).
STACK="perception:=lidar estimator:=ekf safety:=true lidar_source:=mid360 \
enable_stereo_camera:=true enable_camera_lidar_fusion:=true safety_lidar_timeout_s:=1.5"

in_container() { docker exec -u ubuntu "$CONTAINER" bash -c "source /opt/ros/jazzy/setup.bash; $1"; }

world_control() {  # pause: true | pause: false
    in_container "gz service -s /world/fsae_world/control --reqtype gz.msgs.WorldControl \
        --reptype gz.msgs.Boolean --timeout 1000 --req '$1'" 2>/dev/null | grep -q true
}

stop_sim() {  # also waits for the old world to exit, so "paused" pauses the new one
    docker exec "$CONTAINER" bash -c 'pkill -INT -f "[r]os2 launch lhr_gazebo"
        pkill -f "[t]ruth_relay.py"
        for i in $(seq 1 15); do pgrep -f "[g]z sim|[r]os2 launch" >/dev/null || exit 0; sleep 1; done
        pkill -KILL -f "[g]z sim"; true' || true
}

pr_world() {  # branch path: a world from an unmerged PR, copied to the container's /tmp
    git -C "$REPO" fetch -q origin "$1"
    git -C "$REPO" show "FETCH_HEAD:$2" | docker exec -i "$CONTAINER" sh -c "cat > /tmp/$(basename "$2")"
}

WORLDS=autonomy/ros2/src/lhr_gazebo/worlds
case "$1" in
    lap) ARGS="track_style:=autocross seed:=1" ;;
    fault) ARGS="track_style:=oval seed:=1 v_max:=8.0 enable_metrics:=false \
fault:=steer_freeze fault_after_s:=10 fault_min_speed_mps:=7.6" ;;
    # Metrics off: nothing ends the mission at the finish, so the car keeps lapping.
    loop) ARGS="track_style:=rules_autocross seed:=1 world:=/tmp/rules_autocross_seed1.sdf enable_metrics:=false" ;;
    # Until #218 merges: its 3 m corridor, 80 m straight into a 9 m hairpin,
    # slalom, chicane and nearby return lane, on main's stack.
    extreme) ARGS="track_style:=rules_autocross seed:=1 width_m:=3.0 world:=/tmp/extreme_combined_seed1.sdf enable_metrics:=false" ;;
    # Until #223 merges: main has no amz_* track style, so the legacy style
    # stands in, whose width_m is cone-center spacing (the layouts' median gate
    # width). max_step_m:=8 is #223's diagnostic override for FSG's sparse gates.
    fsi) ARGS="track_style:=autocross seed:=1 width_m:=3.92 max_step_m:=8 world:=/tmp/amz_fsi.sdf enable_metrics:=false" ;;
    fsg) ARGS="track_style:=autocross seed:=1 width_m:=3.96 max_step_m:=8 world:=/tmp/amz_fsg.sdf enable_metrics:=false" ;;
    pause) world_control 'pause: true' && echo paused; exit ;;
    resume) world_control 'pause: false' && echo running; exit ;;
    stop) stop_sim; echo stopped; exit 0 ;;
    *) sed -n '2,15p' "$0" >&2; exit 1 ;;
esac

colima status >/dev/null 2>&1 || colima start
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" = true ] || docker start "$CONTAINER" >/dev/null

case "$1" in
    loop) in_container "cd ~/autonomy/ros2 && source install/setup.bash && python3 \
        src/lhr_gazebo/scripts/generate_world.py --style rules_autocross --seed 1 \
        --output /tmp/rules_autocross_seed1.sdf >/dev/null" ;;
    extreme) pr_world tanush/extreme-autocross "$WORLDS/extreme_combined_seed1.sdf" ;;
    fsi) pr_world tanush/competition-layouts "$WORLDS/amz_fsi.sdf" ;;
    fsg) pr_world tanush/competition-layouts "$WORLDS/amz_fsg.sdf" ;;
esac

stop_sim
docker exec -d -u ubuntu -e DISPLAY=:1 -e LIBGL_ALWAYS_SOFTWARE=1 "$CONTAINER" bash -lc \
    "cd ~/autonomy/ros2 && exec ./scripts/run_gazebo_demo.sh gui:=true rviz:=false foxglove:=true $STACK $ARGS > /tmp/demo.log 2>&1"
docker exec -d -u ubuntu "$CONTAINER" bash -lc \
    "source ~/autonomy/ros2/install/setup.bash; exec python3 ~/autonomy/ros2/foxglove/truth_relay.py > /tmp/truth_relay.log 2>&1"
echo "starting $1 ..."

if [ "$2" = paused ]; then
    for i in $(seq 1 240); do world_control 'pause: true' && break; sleep 0.25; done
    echo "paused on the line: ./demo.sh resume, or play in Gazebo"
    sleep 10  # the GUI and the car exist well before sim time would reach READY
else
    for i in $(seq 1 120); do docker exec "$CONTAINER" grep -q "OFF -> READY" /tmp/demo.log 2>/dev/null && break; sleep 1; done
fi

# Harmonic follows through the /gui/track topic; the /gui/follow service
# answers true and does nothing.
in_container "gz topic -t /gui/track -m gz.msgs.CameraTrack -p 'track_mode: FOLLOW_LOOK_AT, \
follow_target: {name: \"fsae_vehicle\"}, track_target: {name: \"fsae_vehicle\"}, \
follow_offset: {x: -8, y: 0, z: 4}, follow_pgain: 0.2, track_pgain: 0.2'" \
    || echo "camera follow failed: right-click fsae_vehicle in the Entity Tree > Follow"
echo "running (double-click the Gazebo title bar to maximize it)"

[ -n "$NOTAIL" ] && exit 0
docker exec "$CONTAINER" bash -c 'tail -n +1 -F /tmp/demo.log | grep --line-buffered -E \
    "State: |Fault start|Safety trip|lap_time_s|cones_hit|max_cte|process has died"'
