#!/usr/bin/env bash
# Benchmark CMake+Ninja vs Bazel building the VCU firmware on this machine.
#
#   ./bench_cmake_vs_bazel.sh [iterations] [bazel-mode]
#     iterations  default 3
#     bazel-mode  "local" (default): --config=local, everything on this machine
#                 "remote": repo-default config — BuildBuddy remote cache +
#                 remote execution, so cold numbers measure cache hits and
#                 network, not local compilation
#                 "cache": remote cache but LOCAL execution (BES streaming
#                 off too, to isolate the cache effect) — cold builds
#                 download hits, everything else runs on this machine
#
# Scenarios, timed per build system:
#   cold  — from nothing: CMake configure+build into a fresh build dir;
#           `bazel clean` then build (--config=local, so no remote cache).
#   incr  — touch Core/Src/gpio.c (compiled by both) and rebuild.
#   null  — rebuild with nothing changed (no-op detection overhead).
#
# Caveats for honest reading:
#   * Same source tree for the ~46 HAL/FreeRTOS/USB C files and lhal's
#     can/uart C++, but Bazel's :vcu also compiles usb_cdc.cpp (newer USB
#     lib) + App/Board C++ and generates build_info (~132 actions vs
#     CMake's 50), so Bazel does somewhat more work.
#   * Compilers are near-identical, not identical: CMake uses whatever
#     arm-none-eabi-gcc is on PATH (ST GNU tools 13.3), Bazel its hermetic
#     GCC 13.2.
#   * The Bazel server JVM is warmed up before timing; cold numbers measure
#     analysis+execution, not daemon startup.

set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
VCU="$REPO/boards/VCU"
CMAKE_BUILD_DIR="$VCU/build/bench-gcc"
TOUCH_FILE="$VCU/Core/Src/gpio.c"
ITERATIONS="${1:-3}"
BAZEL_MODE="${2:-local}"
LOG="$(mktemp -t bench_build)"

case "$BAZEL_MODE" in
  local)  BAZEL_ARGS=(build --config=local //boards/VCU:vcu) ;;
  remote) BAZEL_ARGS=(build //boards/VCU:vcu) ;;
  cache)  BAZEL_ARGS=(build --remote_executor= --extra_execution_platforms=
                      --jobs=auto --bes_backend= --bes_results_url=
                      //boards/VCU:vcu) ;;
  *) echo "unknown bazel-mode '$BAZEL_MODE' (local|remote|cache)" >&2; exit 1 ;;
esac
CMAKE_CONFIGURE=(cmake -G Ninja -B "$CMAKE_BUILD_DIR" -DCMAKE_BUILD_TYPE=Debug
                 -DCMAKE_TOOLCHAIN_FILE=cmake/gcc-arm-none-eabi.cmake)
CMAKE_BUILD=(cmake --build "$CMAKE_BUILD_DIR")

# run <label> <cmd...>: run in $VCU, log output, die loudly on failure.
run() {
  local label="$1"; shift
  if ! (cd "$VCU" && "$@") >>"$LOG" 2>&1; then
    echo "FAILED: $label — last 30 log lines ($LOG):" >&2
    tail -30 "$LOG" >&2
    exit 1
  fi
}

# timed <label> <cmd...>: echo wall-clock seconds for the command.
timed() {
  local label="$1"; shift
  local t
  TIMEFORMAT='%R'
  t=$( { time run "$label" "$@"; } 2>&1 ) || exit 1
  echo "$t"
}

median() {  # from args (seconds)
  printf '%s\n' "$@" | sort -n | awk '{a[NR]=$1} END {
    if (NR % 2) print a[(NR+1)/2];
    else printf "%.3f\n", (a[NR/2] + a[NR/2+1]) / 2 }'
}

declare -a results_name results_vals
record() {  # <name> <val...>
  results_name+=("$1"); shift
  results_vals+=("$*")
}

echo "Benchmarking: $ITERATIONS iteration(s) per scenario, bazel mode: $BAZEL_MODE. Log: $LOG"
echo "Warming up (bazel server + one full build each)..."
run "bazel warmup" bazel version
run "bazel prebuild" bazel "${BAZEL_ARGS[@]}"
run "cmake preconfigure" "${CMAKE_CONFIGURE[@]}"
run "cmake prebuild" "${CMAKE_BUILD[@]}"

declare -a cm_cold cm_incr cm_null bz_cold bz_incr bz_null

for i in $(seq "$ITERATIONS"); do
  echo "--- iteration $i ---"

  rm -rf "$CMAKE_BUILD_DIR"
  t_conf=$(timed "cmake configure" "${CMAKE_CONFIGURE[@]}")
  t_build=$(timed "cmake cold build" "${CMAKE_BUILD[@]}")
  t=$(awk "BEGIN{printf \"%.3f\", $t_conf + $t_build}")
  cm_cold+=("$t");  echo "cmake  cold: ${t}s (configure ${t_conf}s + build ${t_build}s)"

  touch "$TOUCH_FILE"
  t=$(timed "cmake incr build" "${CMAKE_BUILD[@]}"); cm_incr+=("$t")
  echo "cmake  incr: ${t}s"

  t=$(timed "cmake null build" "${CMAKE_BUILD[@]}"); cm_null+=("$t")
  echo "cmake  null: ${t}s"

  run "bazel clean" bazel clean
  t=$(timed "bazel cold build" bazel "${BAZEL_ARGS[@]}"); bz_cold+=("$t")
  echo "bazel  cold: ${t}s"

  touch "$TOUCH_FILE"
  t=$(timed "bazel incr build" bazel "${BAZEL_ARGS[@]}"); bz_incr+=("$t")
  echo "bazel  incr: ${t}s"

  t=$(timed "bazel null build" bazel "${BAZEL_ARGS[@]}"); bz_null+=("$t")
  echo "bazel  null: ${t}s"
done

echo
echo "=== Median of $ITERATIONS run(s), wall-clock seconds ==="
printf '%-10s %10s %10s\n' scenario cmake bazel
printf '%-10s %10s %10s\n' cold  "$(median "${cm_cold[@]}")" "$(median "${bz_cold[@]}")"
printf '%-10s %10s %10s\n' incr  "$(median "${cm_incr[@]}")" "$(median "${bz_incr[@]}")"
printf '%-10s %10s %10s\n' null  "$(median "${cm_null[@]}")" "$(median "${bz_null[@]}")"
echo
echo "cmake builds 50 actions (stub main, lhal can/uart); bazel ~132 (full app + lhal)."
