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
#   SEEDS=1 REPEATS=1 ACCEL_REPEATS=1 ../testing/tools/gate/run_gate.sh
#   EXTRA="safety_lidar_timeout_s:=1.5" ...   # CPU-rendered machines (#123)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)

SEEDS=${SEEDS-"1 2 3 4 5"}
REPEATS=${REPEATS:-3}
ACCEL_REPEATS=${ACCEL_REPEATS-3}   # empty or 0 skips acceleration
BASELINE=${BASELINE:-1}            # 0 skips the truth-stack baselines
RUN_S=${RUN_S:-180}                # wall-second cap per run
SETTLE_S=${SETTLE_S:-3}            # keep recording this long after FINISHED
OUT=${OUT:-$HOME/lhr-test-data/$(date +%F)-mvp-gate}
EXTRA=${EXTRA:-}

TOPICS="/clock /lhr/mission/status /lhr/mission/complete /lhr/metrics/lap_complete
/lhr/vehicle/odom /lhr/vehicle/odom_truth /lhr/safety/status"
mkdir -p "$OUT/runs"

cleanup() {
    # [x] keeps pkill -f from matching this script's own command line.
    pkill -f '[g]z sim'
    pkill -f '[p]arameter_bridge'
    pkill -f '[-]-params-file /tmp/launch_params'
    for _ in $(seq 15); do
        pgrep -f '[-]-params-file /tmp/launch_params' >/dev/null || break
        sleep 1
    done
    sleep 2
}

one_run() {  # id [launch args...]
    local id=$1
    shift
    local dir="$OUT/runs/$id"
    echo "=== $id $(date +%T)"
    if [ -e "$dir" ]; then
        echo "$dir exists, skipping (move it aside to rerun)" >&2
        return
    fi
    mkdir -p "$dir"
    ros2 bag record -s mcap -o "$dir/bag" --topics $TOPICS \
        > "$dir/record.log" 2>&1 &
    local rec=$!
    # A background job ignores SIGINT in a non-interactive shell, so the
    # launch is stopped with TERM, which it handles the same way.
    timeout -s TERM "$RUN_S" ros2 launch lhr_gazebo gazebo_demo.launch.py \
        gui:=false rviz:=false safety:=true output_csv:="$dir/metrics.csv" \
        $EXTRA "$@" > "$dir/launch.log" 2>&1 &
    local launch=$!
    # End the run once the mission is over, after the car has had time to
    # stop on the recording.
    # The explicit type lets the echo subscribe before the launch publishes.
    ( timeout "$RUN_S" ros2 topic echo --field data /lhr/mission/status \
          std_msgs/msg/String 2>/dev/null | grep -q -m1 -E 'FINISHED|EMERGENCY' \
      && sleep "$SETTLE_S" && kill -TERM "$launch" 2>/dev/null ) &
    local watch=$!
    wait "$launch"
    kill "$watch" 2>/dev/null
    pkill -f "[r]os2 topic echo --field data /lhr/mission/status"
    kill -TERM "$rec"
    wait "$rec" 2>/dev/null
    printf '%s\n' "$@" > "$dir/args.txt"
    cleanup
}

cleanup
for s in $SEEDS; do
    if [ "$BASELINE" != 0 ]; then
        one_run "baseline_autocross_s$s" track_style:=autocross seed:="$s" \
            perception:=sim estimator:=truth
    fi
    for r in $(seq 1 "$REPEATS"); do
        one_run "autocross_s${s}_r$r" track_style:=autocross seed:="$s" \
            perception:=lidar estimator:=ekf
    done
done
for r in $(seq 1 "${ACCEL_REPEATS:-0}"); do
    one_run "acceleration_r$r" track_style:=acceleration \
        perception:=lidar estimator:=ekf
done
python3 "$HERE/score_gate.py" "$OUT"
