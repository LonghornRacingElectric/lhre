#!/usr/bin/env bash
# Run the 10/28 Software MVP gate (#109) in Gazebo and score it.
#
# Run from autonomy/ros2 after ./scripts/build.sh, on Ubuntu or inside the
# Docker container. Every gate run uses the no-shortcuts stack from
# docs/plans/mvp-pass-criteria.md: perception:=lidar estimator:=ekf with
# safety on, the Mid-360 caster and stereo camera fusion (#206).
# Each autocross seed also gets one truth-stack
# baseline (perception:=sim estimator:=truth) for the lap time check. A run
# ends a few seconds after the mission reaches FINISHED or EMERGENCY, or at
# RUN_S wall seconds. score_gate.py then writes $OUT/results.csv and
# $OUT/summary.md.
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
BASELINE=${BASELINE:-1}            # 0 skips the truth-stack baselines
RUN_S=${RUN_S:-180}                # wall-second cap per run
SETTLE_S=${SETTLE_S:-3}            # keep recording this long after FINISHED
OUT=${OUT:-$HOME/lhr-test-data/$(date +%F)-mid360-fusion-gate}
EXTRA=${EXTRA:-}
TRACK_STYLE=${TRACK_STYLE:-autocross}
case "$TRACK_STYLE" in
    autocross|rules_autocross) ;;
    *) echo "unsupported gate TRACK_STYLE=$TRACK_STYLE" >&2; exit 2 ;;
esac
ROS2=$(cd "$HERE/../../../ros2" && pwd)

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
    [ -n "$WATCH" ] && kill "$WATCH" 2>/dev/null
    pkill -f "[r]os2 topic echo --field data /lhr/mission/status"
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
        gui:=false rviz:=false safety:=true output_csv:="$dir/metrics.csv"
        ${world:+"world:=$world"} $EXTRA "$@")
    ros2 bag record -s mcap -o "$dir/bag" --topics $TOPICS \
        > "$dir/record.log" 2>&1 &
    REC=$!
    # A background job ignores SIGINT in a non-interactive shell, so the
    # launch is stopped with TERM, which it handles the same way.
    timeout -s TERM "$RUN_S" "${launch_cmd[@]}" > "$dir/launch.log" 2>&1 &
    LAUNCH=$!
    # End the run once the mission is over, after the car has had time to
    # stop on the recording.
    # The explicit type lets the echo subscribe before the launch publishes.
    ( timeout "$RUN_S" ros2 topic echo --field data /lhr/mission/status \
          std_msgs/msg/String 2>/dev/null | grep -q -m1 -E 'FINISHED|EMERGENCY' \
      && sleep "$SETTLE_S" && kill -TERM "$LAUNCH" 2>/dev/null ) &
    WATCH=$!
    wait "$LAUNCH"
    stop_run
    printf '%s\n' "${launch_cmd[@]}" > "$dir/args.txt"
    cleanup
}

plan() {  # one line per run: the id, then its launch arguments
    local sensors='lidar_source:=mid360 enable_stereo_camera:=true enable_camera_lidar_fusion:=true'
    for s in $SEEDS; do
        if [ "$BASELINE" != 0 ]; then
            echo "baseline_${TRACK_STYLE}_s$s track_style:=$TRACK_STYLE seed:=$s perception:=sim estimator:=truth lidar_source:=gazebo enable_stereo_camera:=false enable_camera_lidar_fusion:=false"
        fi
        for r in $(seq 1 "$REPEATS"); do
            echo "${TRACK_STYLE}_s${s}_r$r track_style:=$TRACK_STYLE seed:=$s perception:=lidar estimator:=ekf $sensors"
        done
    done
    for r in $(seq 1 "${ACCEL_REPEATS:-0}"); do
        echo "acceleration_r$r track_style:=acceleration perception:=lidar estimator:=ekf $sensors"
    done
    if [ -e "$SKIDPAD_WORLD" ]; then
        for r in $(seq 1 "${SKIDPAD_REPEATS:-0}"); do
            echo "skidpad_r$r track_style:=skidpad mission:=skidpad perception:=lidar estimator:=ekf $sensors"
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
