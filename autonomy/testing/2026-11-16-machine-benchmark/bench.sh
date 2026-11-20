#!/usr/bin/env bash
# Machine benchmark (11/16/26): the full default gate on one machine, with the
# machine described and its CPU/GPU/memory sampled every 5 s while it runs.
#   WS=<autonomy/ros2 dir> OUT=<dir> [EXTRA=...] bench.sh <label>
# Same commit and gate stack everywhere; only EXTRA may differ, and it is recorded.
LABEL=${1:?label}
WS=${WS:-$HOME/lhre/autonomy/ros2}
OUT=${OUT:-$HOME/lhr-test-data/2026-11-16-bench-$LABEL}
mkdir -p "$OUT"
cd "$WS" || exit 2
source scripts/_ros_env.sh
source install/setup.bash

smi() { command -v nvidia-smi >/dev/null && echo nvidia-smi || { [ -x /usr/lib/wsl/lib/nvidia-smi ] && echo /usr/lib/wsl/lib/nvidia-smi; }; }
SMI=$(smi)
{
    echo "label=$LABEL"
    echo "host=$(hostname)"
    echo "commit=$(git -C .. rev-parse HEAD)"
    echo "kernel=$(uname -r)"
    lscpu | grep -E '^(Model name|CPU\(s\)|Thread\(s\) per core)' | sed 's/  */ /g'
    echo "mem_total_mb=$(free -m | awk '/Mem/{print $2}')"
    [ -n "$SMI" ] && echo "gpu=$($SMI --query-gpu=name,memory.total,driver_version --format=csv,noheader)"
    command -v eglinfo >/dev/null && EGL_PLATFORM=surfaceless timeout 10 eglinfo -B 2>/dev/null | grep -m1 "core profile renderer" | sed "s/^ *OpenGL core profile renderer: /OpenGL renderer string: /"
    echo "EXTRA=${EXTRA:-}"
    env | grep -E '^(GALLIUM_DRIVER|MESA_|LIBGL_)' || true
    echo "start=$(date -Is)"
} > "$OUT/machine.txt"

# Samplers: vmstat for CPU and memory, nvidia-smi for the GPU where there is one.
vmstat -n -t 5 > "$OUT/vmstat.log" 2>&1 &
pids=$!
if [ -n "$SMI" ]; then
    $SMI --query-gpu=timestamp,utilization.gpu,memory.used,temperature.gpu --format=csv,noheader -l 5 > "$OUT/gpu.csv" 2>&1 &
    pids="$pids $!"
fi

t0=$(date +%s)
OUT="$OUT/gate" EXTRA="${EXTRA:-}" ../testing/tools/gate/run_gate.sh > "$OUT/gate.log" 2>&1
code=$?
t1=$(date +%s)
kill $pids 2>/dev/null
{ echo "end=$(date -Is)"; echo "gate_exit=$code"; echo "wall_s=$((t1 - t0))"; } >> "$OUT/machine.txt"
echo "bench $LABEL done: exit $code in $((t1 - t0)) s"
