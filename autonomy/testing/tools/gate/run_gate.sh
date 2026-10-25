#!/usr/bin/env bash
# Run the 10/28 Software MVP gate (#109) in Gazebo and score it.
#
# Run from autonomy/ros2 after ./scripts/build.sh, on Ubuntu or inside the
# Docker container. Every gate run uses the no-shortcuts stack from
# docs/plans/mvp-pass-criteria.md: perception:=lidar estimator:=ekf with
# safety on and launch defaults. Each autocross seed also gets one truth-stack
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
OUT=${OUT:-$HOME/lhr-test-data/$(date +%F)-mvp-gate}
EXTRA=${EXTRA:-}

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
    ros2 bag record -s mcap -o "$dir/bag" --topics $TOPICS \
        > "$dir/record.log" 2>&1 &
    REC=$!
    # A background job ignores SIGINT in a non-interactive shell, so the
    # launch is stopped with TERM, which it handles the same way.
    timeout -s TERM "$RUN_S" ros2 launch lhr_gazebo gazebo_demo.launch.py \
        gui:=false rviz:=false safety:=true output_csv:="$dir/metrics.csv" \
        $EXTRA "$@" > "$dir/launch.log" 2>&1 &
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
    printf '%s\n' "$@" > "$dir/args.txt"
    cleanup
}

plan() {  # one line per run: the id, then its launch arguments
    for s in $SEEDS; do
        if [ "$BASELINE" != 0 ]; then
            echo "baseline_autocross_s$s track_style:=autocross seed:=$s perception:=sim estimator:=truth"
        fi
        for r in $(seq 1 "$REPEATS"); do
            echo "autocross_s${s}_r$r track_style:=autocross seed:=$s perception:=lidar estimator:=ekf"
        done
    done
    for r in $(seq 1 "${ACCEL_REPEATS:-0}"); do
        echo "acceleration_r$r track_style:=acceleration perception:=lidar estimator:=ekf"
    done
    if [ -e "$SKIDPAD_WORLD" ]; then
        for r in $(seq 1 "${SKIDPAD_REPEATS:-0}"); do
            echo "skidpad_r$r track_style:=skidpad mission:=skidpad perception:=lidar estimator:=ekf"
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
