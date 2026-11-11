#!/usr/bin/env bash
# Runs the VCU firmware against the car model in the SIL dev container.
#
#   boards/VCU/Sil/run.sh --fmu PATH [--scenario launch] [--out DIR] [vcusil options]
#   boards/VCU/Sil/run.sh --test [--fmu PATH]
#   boards/VCU/Sil/run.sh --view RUN_DIR [--port 18080]
#
# The first form builds //boards/VCU:libvcu_sil.so and the CAN decode table
# from this checkout and runs one scenario; other options go to python -m
# vcusil (README.md lists them). --test runs the SIL's C++ tests and the
# host's pytest suite instead; with an FMU the end-to-end tests run too.
# --view serves a run's run.mcap with the SIL layout in Lichtblick, an
# open-source Foxglove viewer, on localhost. The FMU (GPL, never committed)
# may also come from VCU_SIL_FMU. Exit status: 0 completed, 1 the car model
# failed, 2 anything else, including a firmware that does not build.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
image=lhre-vcu-sil:local
viewer=ghcr.io/lichtblick-suite/lichtblick:1.30.0@sha256:25333a8f57737c8c5866abf75670b8dcf2ed6c2cb5ce3d64f3c63f210a5177e9
min_free_gb=10
targets="//boards/VCU:libvcu_sil.so //boards/VCU:vcu_sil_can_table"

usage() { sed -n '2,15s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
die() { echo "run.sh: $*" >&2; exit 2; }

fmu="${VCU_SIL_FMU:-}"
out=""
scenario=launch
mode=run
view_dir=""
port=18080
pass=()
while (($#)); do
  case "$1" in
    --fmu) (($# >= 2)) || die "--fmu needs a path"; fmu="$2"; shift 2 ;;
    --out) (($# >= 2)) || die "--out needs a path"; out="$2"; shift 2 ;;
    --scenario) (($# >= 2)) || die "--scenario needs a name"; scenario="$2"; pass+=("$1" "$2"); shift 2 ;;
    --test) mode='test'; shift ;;
    --view) (($# >= 2)) || die "--view needs a run directory"; mode=view; view_dir="$2"; shift 2 ;;
    --port) (($# >= 2)) || die "--port needs a number"; port="$2"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) pass+=("$1"); shift ;;
  esac
done

command -v docker >/dev/null || die "docker is not installed"
docker info >/dev/null 2>&1 || die "docker is not running"

if [[ "$mode" == view ]]; then
  [[ -f "$view_dir/run.mcap" ]] || die "no run.mcap in $view_dir"
  [[ "$port" =~ ^[0-9]+$ ]] || die "--port must be a number"
  view_dir="$(cd "$view_dir" && pwd)"
  name="lhre-sil-view-$(id -u)"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --rm --name "$name" -p "127.0.0.1:$port:8080" \
    -v "$here/foxglove/vcu_sil.json:/lichtblick/default-layout.json:ro" \
    -v "$view_dir:/src/data:ro" "$viewer" >/dev/null ||
    die "the viewer did not start"
  echo "Open http://127.0.0.1:$port/?ds=remote-file&ds.url=http%3A%2F%2F127.0.0.1%3A$port%2Fdata%2Frun.mcap"
  echo "and press play to see every event. Stop it with: docker stop $name"
  exit 0
fi

free_kb="$(df -Pk "$here" | awk 'NR == 2 { print $4 }')"
((free_kb > min_free_gb * 1024 * 1024)) ||
  die "less than ${min_free_gb} GB free; the Bazel cache needs room (docker system df shows what to prune)"

# One cache per host user: a volume's files belong to whoever created it.
mounts=(
  -v "$repo:/work/lhre:ro"
  -v "lhre-sil-bazel-$(id -u):/home/dev/.cache/bazel"
  -v "lhre-sil-bazelisk-$(id -u):/home/dev/.cache/bazelisk"
)
fmu_in_container=""
if [[ -n "$fmu" ]]; then
  [[ -f "$fmu" ]] || die "no FMU at $fmu"
  fmu_dir="$(cd "$(dirname "$fmu")" && pwd)"
  mounts+=(-v "$fmu_dir:/fmu:ro")
  fmu_in_container="/fmu/$(basename "$fmu")"
elif [[ "$mode" == run ]]; then
  die "--fmu PATH (or VCU_SIL_FMU) is required"
fi

docker build --quiet --tag "$image" \
  --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
  --file "$here/Dockerfile" "$here/host" >/dev/null ||
  die "the SIL image did not build (rerun without --quiet in run.sh to see why)"

if [[ "$mode" == test ]]; then
  exec docker run --rm "${mounts[@]}" -e VCU_SIL_FMU="$fmu_in_container" \
    -e TARGETS="$targets" "$image" bash -c '
    set -euo pipefail
    bazel test --config=local --symlink_prefix=/ --test_output=errors \
      //boards/VCU:vcu_sil_rig_test //boards/VCU:vcu_sil_test \
      //boards/VCU:vcu_sil_gen_can_table_test
    bazel build --config=local --symlink_prefix=/ $TARGETS
    bin="$(bazel info --config=local bazel-bin)"
    export VCU_SIL_LIB="$bin/boards/VCU/libvcu_sil.so"
    export VCU_SIL_CAN_TABLE="$bin/boards/VCU/vcu_sil_can.json"
    cd /work/lhre/boards/VCU/Sil/host
    exec python3 -m pytest -p no:cacheprovider -q tests "$@"
  ' run.sh "${pass[@]+"${pass[@]}"}"
fi

out="${out:-$here/out/$scenario-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
status=0
docker run --rm "${mounts[@]}" -v "$out:/out" -e TARGETS="$targets" "$image" bash -c '
  set -euo pipefail
  if ! bazel build --config=local --symlink_prefix=/ --noshow_progress \
      --ui_event_filters=-info $TARGETS; then
    echo "run.sh: libvcu_sil.so did not build (see the compiler errors above)" >&2
    exit 2
  fi
  bin="$(bazel info --config=local bazel-bin)" || exit 2
  cd /work/lhre/boards/VCU/Sil/host
  exec python3 -m vcusil --lib "$bin/boards/VCU/libvcu_sil.so" \
    --can-table "$bin/boards/VCU/vcu_sil_can.json" --fmu "$0" --out /out "$@"
' "$fmu_in_container" "${pass[@]+"${pass[@]}"}" || status=$?
# vcusil exits 0, 1 or 2; any other status (docker, a signal) is the harness.
case "$status" in 0 | 1 | 2) ;; *) status=2 ;; esac
[[ -f "$out/meta.json" ]] && echo "  wrote $out (open it with: $0 --view $out)"
exit "$status"
