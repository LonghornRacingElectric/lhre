#!/usr/bin/env bash
# Run the 10/28 Software MVP gate (#109) in Gazebo and score it.
#
# Run from autonomy/ros2 after ./scripts/build.sh, on Ubuntu or inside the
# Docker container. Every gate run launches with the gate stack in
# stack.args, which run_faults.sh shares (#237): perception:=lidar
# estimator:=ekf with safety on, the Mid-360 caster and stereo camera fusion.
# Lap time is scored against a reference lap computed from the course (#232).
# BASELINE=1 adds one truth-stack run per seed (perception:=sim
# estimator:=truth) for comparison only, and names its own arguments. A run
# ends SETTLE_S sim seconds after the mission reaches FINISHED or EMERGENCY,
# or at RUN_S sim seconds, so a slow machine runs the same campaign, only
# longer. Wall time only catches a hung sim: /clock stopped for HANG_S
# (#227). score_gate.py then writes $OUT/results.csv and $OUT/summary.md.
#
#   ../testing/tools/gate/run_gate.sh
#   SEEDS=1 REPEATS=1 ACCEL_REPEATS=1 SKIDPAD_REPEATS=1 ../testing/tools/gate/run_gate.sh
#   EXTRA="safety_lidar_timeout_s:=1.5" ...   # CPU-rendered machines (see the gate README)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)

SEEDS=${SEEDS-"1 2 3 4 5"}
REPEATS=${REPEATS:-3}
ACCEL_REPEATS=${ACCEL_REPEATS-3}   # empty or 0 skips acceleration
SKIDPAD_REPEATS=${SKIDPAD_REPEATS-3}  # empty or 0 skips skidpad
BASELINE=${BASELINE:-0}            # 1 adds the truth-stack runs, for comparison
RUN_S=${RUN_S:-180}                # sim-second cap per run
SETTLE_S=${SETTLE_S:-5}            # sim seconds recorded after FINISHED
HANG_S=${HANG_S:-60}               # wall seconds of a stopped /clock: hung
OUT=${OUT:-$HOME/lhr-test-data/$(date +%F)-mid360-fusion-gate}
EXTRA=${EXTRA:-}
TRACK_STYLE=${TRACK_STYLE:-autocross}
case "$TRACK_STYLE" in
    autocross|rules_autocross) ;;
    *) echo "unsupported gate TRACK_STYLE=$TRACK_STYLE" >&2; exit 2 ;;
esac
ROS2=$(cd "$HERE/../../../ros2" && pwd)
source "$HERE/stack.sh"
STACK=$(gate_stack) || exit 2

TOPICS="/clock /lhr/mission/status /lhr/mission/complete /lhr/metrics/lap_complete
/lhr/vehicle/odom /lhr/vehicle/odom_truth /lhr/safety/status"
mkdir -p "$OUT/runs"

REC='' LAUNCH='' WATCH=''

cleanup() {
    # [x] keeps pkill -f from matching this script's own command line.
    pkill -f '[g]z sim'
    pkill -f '[p]arameter_bridge'
    pkill -f '[-]-params-file /tmp/launch_params'
    for _ in $(seq 15); do
        pgrep -f '[-]-params-file /tmp/launch_params' >/dev/null || break
        sleep 1
    done
    # Whatever is still up is killed outright, so it cannot leak into the
    # next run.
    pkill -KILL -f '[g]z sim'
    pkill -KILL -f '[p]arameter_bridge'
    pkill -KILL -f '[-]-params-file /tmp/launch_params'
    sleep 2
}

stop_run() {  # the watcher, then the launch, then the recorder
    [ -n "$WATCH" ] && kill -TERM "$WATCH" 2>/dev/null && wait "$WATCH" 2>/dev/null
    [ -n "$LAUNCH" ] && kill -TERM "$LAUNCH" 2>/dev/null && wait "$LAUNCH" 2>/dev/null
    [ -n "$REC" ] && kill -TERM "$REC" 2>/dev/null && wait "$REC" 2>/dev/null
    REC='' LAUNCH='' WATCH=''
}

on_signal() {
    echo "interrupted, stopping the current run" >&2
    stop_run
    cleanup
    exit 130
}
trap on_signal INT TERM

one_run() {  # id [launch args...]
    local id=$1
    shift
    local dir="$OUT/runs/$id"
    echo "=== $id $(date +%T)"
    local world=''
    if [[ "$id" == *rules_autocross* ]]; then
        local seed
        for arg in "$@"; do
            case "$arg" in seed:=*) seed=${arg#seed:=} ;; esac
        done
        mkdir -p "$OUT/worlds"
        world="$OUT/worlds/rules_autocross_seed${seed}.sdf"
        python3 "$ROS2/src/lhr_gazebo/scripts/generate_world.py" \
            --style rules_autocross --seed "$seed" --output "$world" || return 1
    fi
    # args.txt is written last, so it marks a run that finished.
    if [ -e "$dir/args.txt" ]; then
        echo "$id is done, skipping (move $dir aside to rerun)" >&2
        return
    fi
    if [ -e "$dir" ]; then
        mkdir -p "$OUT/incomplete"
        mv "$dir" "$OUT/incomplete/$id-$(date +%s)"
        echo "$id was cut short last time, moved aside and rerunning" >&2
    fi
    mkdir -p "$dir"
    local launch_cmd=(ros2 launch lhr_gazebo gazebo_demo.launch.py
        gui:=false rviz:=false output_csv:="$dir/metrics.csv"
        ${world:+"world:=$world"} $EXTRA "$@")
    ros2 bag record -s mcap -o "$dir/bag" --topics $TOPICS \
        > "$dir/record.log" 2>&1 &
    REC=$!
    # A background job ignores SIGINT in a non-interactive shell, so the
    # launch is stopped with TERM, which it handles the same way.
    "${launch_cmd[@]}" > "$dir/launch.log" 2>&1 &
    LAUNCH=$!
    # The watcher ends the run in sim time, once the car has had time to
    # stop on the recording, and writes why to end.txt for the scorer.
    python3 "$HERE/watch_run.py" --cap "$RUN_S" --settle "$SETTLE_S" \
        --hang "$HANG_S" --pid "$LAUNCH" --out "$dir/end.txt" &
    WATCH=$!
    wait "$WATCH"
    WATCH=''
    stop_run
    printf '%s\n' "${launch_cmd[@]}" > "$dir/args.txt"
    cleanup
}

plan() {  # one line per run: the id, then its launch arguments
    # The truth baseline is the one run that opts out of the gate stack.
    local truth='perception:=sim estimator:=truth safety:=true lidar_source:=gazebo enable_stereo_camera:=false enable_camera_lidar_fusion:=false'
    for s in $SEEDS; do
        if [ "$BASELINE" != 0 ]; then
            echo "baseline_${TRACK_STYLE}_s$s track_style:=$TRACK_STYLE seed:=$s $truth"
        fi
        for r in $(seq 1 "$REPEATS"); do
            echo "${TRACK_STYLE}_s${s}_r$r track_style:=$TRACK_STYLE seed:=$s $STACK"
        done
    done
    for r in $(seq 1 "${ACCEL_REPEATS:-0}"); do
        echo "acceleration_r$r track_style:=acceleration $STACK"
    done
    if [ -e "$SKIDPAD_WORLD" ]; then
        for r in $(seq 1 "${SKIDPAD_REPEATS:-0}"); do
            echo "skidpad_r$r track_style:=skidpad mission:=skidpad $STACK"
        done
    fi
}

# Skidpad joins the matrix once its world is in the tree. Until then the pass
# criteria record it as a waiver, which the summary states, not as missing
# runs.
SKIDPAD_WORLD=$HERE/../../../ros2/src/lhr_gazebo/worlds/skidpad_seed1.sdf
{
    if [ "${SKIDPAD_REPEATS:-0}" != 0 ] && [ ! -e "$SKIDPAD_WORLD" ]; then
        echo "skidpad: no skidpad_seed1.sdf world in this checkout (#114)"
    fi
} > "$OUT/waived.txt"

# The scorer fails every planned run with no result, so a campaign that was
# cut short can never read as a pass.
plan | cut -d' ' -f1 > "$OUT/expected.txt"
cleanup
while read -r id args <&3; do
    one_run "$id" $args
done 3< <(plan)
python3 "$HERE/score_gate.py" "$OUT"
