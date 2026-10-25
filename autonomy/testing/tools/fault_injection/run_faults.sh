#!/usr/bin/env bash
# Run the #117 fault-injection matrix in Gazebo and score every run.
#
# Run from autonomy/ros2 after ./scripts/build.sh, on Ubuntu or inside the
# Docker container. Each run is one headless launch with safety:=true and at
# most one fault from fault_injector. Bags go to $OUT/bags (never into git,
# see testing/README.md) and analyze_run.py writes one row per run to
# $OUT/results.csv (a rerun replaces its row); summarize.py turns that into
# $OUT/summary.md.
#
#   ../testing/tools/fault_injection/run_faults.sh
#   CASES="ekf_kill lidar_drop" SPEEDS=5 REPEATS=1 CLEAN_SEEDS= \
#       ../testing/tools/fault_injection/run_faults.sh
#   EXTRA="safety_lidar_timeout_s:=1.5" ...   # CPU-rendered machines (#123)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROS2=$(cd "$HERE/../../../ros2" && pwd)

# Empty means none, like CLEAN_SEEDS: CASES= runs only the clean laps.
CASES=${CASES-"ekf_kill controller_kill controller_pause odom_nan odom_spike lidar_drop cmd_nan steer_freeze geofence"}
SPEEDS=${SPEEDS:-"3 5 8"}
REPEATS=${REPEATS:-2}
CLEAN_SEEDS=${CLEAN_SEEDS-"1 2 3 4 5"}   # clean autocross laps: false trips
TRACK=${TRACK:-oval}
AFTER=${AFTER:-25}          # fault starts this many seconds after DRIVING
RUN_S=${RUN_S:-60}          # wall seconds per faulted run
CLEAN_S=${CLEAN_S:-150}
FENCE=${FENCE:-20}          # geofence case: radius well inside the track
OUT=${OUT:-$HOME/lhr-test-data/$(date +%F)-fault-injection}
EXTRA=${EXTRA:-}

TOPICS="/clock /lhr/safety/status /lhr/safety/heartbeat /lhr/mission/status
/lhr/vehicle/odom /lhr/vehicle/odom_truth /lhr/vehicle/cmd /lhr/sim/fault"
mkdir -p "$OUT/bags"
failed=''

cleanup() {
    # [x] keeps pkill -f from matching this script's own command line.
    pkill -f '[g]z sim'
    pkill -f '[p]arameter_bridge'
    sleep 2
}

world_for() {  # track seed: prints world:=<sdf>, or nothing if one is committed
    # Without a committed <track>_seed<N>.sdf the launch refuses to start,
    # so generate the one the seed asks for.
    local track=$1 seed=$2
    [ -f "$ROS2/src/lhr_gazebo/worlds/${track}_seed${seed}.sdf" ] && return 0
    local sdf="$OUT/worlds/${track}_seed${seed}.sdf"
    if [ ! -f "$sdf" ]; then
        mkdir -p "$OUT/worlds"
        python3 "$ROS2/src/lhr_gazebo/scripts/generate_world.py" \
            --style "$track" --seed "$seed" --output "$sdf" > /dev/null || return 1
    fi
    echo "world:=$sdf"
}

one_run() {  # id kind track seed v_max seconds [launch args...]
    local id=$1 kind=$2 track=$3 seed=$4 vmax=$5 secs=$6
    shift 6
    local bag="$OUT/bags/$id"
    echo "=== $id"
    local world
    if ! world=$(world_for "$track" "$seed"); then
        echo "no world for $track seed $seed, skipping" >&2
        failed="$failed $id"
        return
    fi
    rm -rf "$bag"
    ros2 bag record -s mcap -o "$bag" --topics $TOPICS \
        > "$OUT/bags/$id.record.log" 2>&1 &
    local rec=$!
    timeout -s INT "$secs" ros2 launch lhr_gazebo gazebo_demo.launch.py \
        gui:=false rviz:=false track_style:="$track" seed:="$seed" \
        perception:=lidar estimator:=ekf safety:=true \
        v_max:="$vmax" ${world:+"$world"} \
        $EXTRA "$@" > "$OUT/bags/$id.launch.log" 2>&1
    # A background job ignores SIGINT in a non-interactive shell; TERM is
    # what makes the recorder close the bag.
    kill -TERM "$rec"
    wait "$rec" 2>/dev/null
    cleanup
    python3 "$HERE/analyze_run.py" "$bag" --id "$id" --case "$kind" \
        --track "$track" --seed "$seed" --v-max "$vmax" --fence "$FENCE" \
        --csv "$OUT/results.csv" || failed="$failed $id"
}

cleanup
for kind in $CASES; do
    for v in $SPEEDS; do
        for r in $(seq 1 "$REPEATS"); do
            id="${kind}_v${v}_r${r}"
            case $kind in
                geofence)
                    one_run "$id" "$kind" "$TRACK" 1 "$v" "$RUN_S" \
                        safety_geofence_radius_m:="$FENCE" ;;
                controller_pause)
                    one_run "$id" "$kind" "$TRACK" 1 "$v" "$RUN_S" \
                        fault:="$kind" fault_after_s:="$AFTER" \
                        fault_duration_s:=3 ;;
                *)
                    one_run "$id" "$kind" "$TRACK" 1 "$v" "$RUN_S" \
                        fault:="$kind" fault_after_s:="$AFTER" ;;
            esac
        done
    done
done
for s in $CLEAN_SEEDS; do
    one_run "clean_s$s" clean autocross "$s" 5 "$CLEAN_S"
done
python3 "$HERE/summarize.py" "$OUT/results.csv" > "$OUT/summary.md"
if [ -n "$failed" ]; then
    printf '\nRuns with no world or a failed analysis:%s\n' "$failed" \
        >> "$OUT/summary.md"
fi
cat "$OUT/summary.md"
