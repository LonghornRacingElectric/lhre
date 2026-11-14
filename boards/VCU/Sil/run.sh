#!/usr/bin/env bash
# Runs the VCU firmware against the car model in the SIL dev container.
#
#   boards/VCU/Sil/run.sh --fmu PATH [--scenario NAME|FILE] [--out DIR] [vcusil options]
#   boards/VCU/Sil/run.sh --check [--strict] [--explain] [--errors-only]
#                         [--brake-psi-full PSI] [NAME|FILE ...]
#   boards/VCU/Sil/run.sh --test [--fmu PATH]
#   boards/VCU/Sil/run.sh --view RUN_DIR [--port 18080]
#
# The first form checks the scenario (default launch) for errors, builds
# //boards/VCU:libvcu_sil.so and the CAN decode table from this checkout and
# runs it; other options go to python -m vcusil (README.md lists them). A
# scenario FILE, a .yml file, must be inside this checkout: the container
# sees only the checkout and the FMU's folder. --check runs
# python3 -m vcusil.check on scenario files (with no names, every file in
# scenarios/; --scenario X adds X). It needs Docker and builds or reuses the
# SIL image, but never builds the firmware; python3 -m vcusil.check in host/
# needs neither, just Python 3.11+ with PyYAML. --test runs the SIL's C++
# tests and the host's pytest suite instead; with an FMU the end-to-end tests
# run too. --view serves a run's run.mcap with the SIL layout in Lichtblick,
# an open-source Foxglove viewer, on localhost. The FMU (GPL, never
# committed) may also come from VCU_SIL_FMU. A value may follow its option
# after a space or an = (--out=DIR). Exit status: 0 completed with every
# check passed, 1 the car model failed, 3 a check failed (an invariant or
# one of the scenario's expectations), 2 anything else, including a firmware
# that does not build or a scenario that cannot run. A run that stops before
# it starts leaves no run directory, and a reused --out as it was.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Physical (pwd -P), as container_scenario compares paths against it.
repo="$(cd "$here/../../.." && pwd -P)"
image=lhre-vcu-sil:local
viewer=ghcr.io/lichtblick-suite/lichtblick:1.30.0@sha256:25333a8f57737c8c5866abf75670b8dcf2ed6c2cb5ce3d64f3c63f210a5177e9
min_free_gb=10
targets="//boards/VCU:libvcu_sil.so //boards/VCU:vcu_sil_can_table"

usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"; }
die() { echo "run.sh: $*" >&2; exit 2; }

fmu="${VCU_SIL_FMU:-}"
out=""
scenario=launch
named=()  # every --scenario: --check checks each, a run the last
mode=run
view_dir=""
port=18080
pass=()
while (($#)); do
  case "$1" in
    --fmu=* | --out=* | --scenario=* | --view=* | --port=*)
      set -- "${1%%=*}" "${1#*=}" "${@:2}" ;;
  esac
  case "$1" in
    --fmu) (($# >= 2)) && [[ -n "$2" ]] || die "--fmu needs a path"; fmu="$2"; shift 2 ;;
    --out) (($# >= 2)) && [[ -n "$2" ]] || die "--out needs a path"; out="$2"; shift 2 ;;
    --scenario)
      (($# >= 2)) && [[ -n "$2" ]] || die "--scenario needs a name or a .yml file"
      scenario="$2"; named+=("$2"); shift 2 ;;
    --check) mode=check; shift ;;
    --test) mode='test'; shift ;;
    --view)
      (($# >= 2)) && [[ -n "$2" ]] || die "--view needs a run directory"
      mode=view; view_dir="$2"; shift 2 ;;
    --port) (($# >= 2)) && [[ -n "$2" ]] || die "--port needs a number"; port="$2"; shift 2 ;;
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

# What the container's vcusil gets for a scenario: a name as it is, a file
# as its path under /work/lhre. The container sees only the checkout. Both
# paths are physical, so a symlinked spelling of the checkout (or /tmp
# against /private/tmp) is still inside it.
container_scenario() {
  if [[ "$1" != */* && "$1" != *.yml ]]; then printf '%s\n' "$1"; return; fi
  [[ -f "$1" ]] || { echo "run.sh: no scenario file at $1" >&2; return 2; }
  local abs
  abs="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
  if [[ "$abs" != "$repo/"* ]]; then
    echo "run.sh: $1 is outside this checkout, the only folder the SIL container sees; move it under boards/VCU/Sil/scenarios/" >&2
    return 2
  fi
  printf '/work/lhre/%s\n' "${abs#"$repo/"}"
}

if [[ "$mode" != check ]]; then
  free_kb="$(df -Pk "$here" | awk 'NR == 2 { print $4 }')"
  ((free_kb > min_free_gb * 1024 * 1024)) ||
    die "less than ${min_free_gb} GB free; the Bazel cache needs room (docker system df shows what to prune)"
fi

# One cache per host user: a volume's files belong to whoever created it.
mounts=(
  -v "$repo:/work/lhre:ro"
  -v "lhre-sil-bazel-$(id -u):/home/dev/.cache/bazel"
  -v "lhre-sil-bazelisk-$(id -u):/home/dev/.cache/bazelisk"
)
fmu_in_container=""
# --check needs no FMU, so a stale VCU_SIL_FMU does not stop it.
if [[ -n "$fmu" && "$mode" != check ]]; then
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

if [[ "$mode" == check ]]; then
  args=()
  for a in ${pass[@]+"${pass[@]}"} ${named[@]+"${named[@]}"}; do
    if [[ "$a" == -* ]]; then args+=("$a"); else c="$(container_scenario "$a")" || exit 2; args+=("$c"); fi
  done
  exec docker run --rm -v "$repo:/work/lhre:ro" "$image" bash -c \
    'cd /work/lhre/boards/VCU/Sil/host && exec python3 -m vcusil.check "$@"' run.sh ${args[@]+"${args[@]}"}
fi

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

scenario_c="$(container_scenario "$scenario")" || exit 2
out="${out:-$here/out/$(basename "$scenario" .yml)-$(date +%Y%m%d-%H%M%S)}"
made_out=0
[[ -e "$out" ]] || made_out=1
mkdir -p "$out" || die "cannot make the run directory $out"
out="$(cd "$out" && pwd)"
# A run that stops before it starts (a scenario error, bad options, a
# firmware that does not build) writes nothing, so a reused --out still
# holds its previous run. A meta.json is this run's only in a directory it
# made, or when it is newer than this stamp.
stamp="$(mktemp)"
trap 'rm -f "$stamp"' EXIT
status=0
docker run --rm "${mounts[@]}" -v "$out:/out" -e TARGETS="$targets" "$image" bash -c '
  set -euo pipefail
  fmu="$1" scenario="$2"
  shift 2
  # A scenario that cannot run costs seconds here, not a cold build. Errors
  # only: vcusil prints the warnings, with the --brake-psi-full in use.
  (cd /work/lhre/boards/VCU/Sil/host && python3 -m vcusil.check --errors-only "$scenario") ||
    exit 2
  if ! bazel build --config=local --symlink_prefix=/ --noshow_progress \
      --ui_event_filters=-info $TARGETS; then
    echo "run.sh: libvcu_sil.so did not build (see the compiler errors above)" >&2
    exit 2
  fi
  bin="$(bazel info --config=local bazel-bin)" || exit 2
  cd /work/lhre/boards/VCU/Sil/host
  exec python3 -m vcusil --lib "$bin/boards/VCU/libvcu_sil.so" \
    --can-table "$bin/boards/VCU/vcu_sil_can.json" --fmu "$fmu" --out /out \
    --scenario "$scenario" "$@"
' run.sh "$fmu_in_container" "$scenario_c" "${pass[@]+"${pass[@]}"}" || status=$?
# vcusil exits 0, 1, 2 or 3; any other status (docker, a signal) is the
# harness.
case "$status" in 0 | 1 | 2 | 3) ;; *) status=2 ;; esac
# find -newer, not [[ -nt ]]: macOS's bash 3.2 compares whole seconds.
wrote_meta() {
  [[ -f "$out/meta.json" ]] || return 1
  ((made_out)) || [[ -n "$(find "$out/meta.json" -newer "$stamp")" ]]
}
if wrote_meta; then
  echo "  wrote $out (open it with: $0 --view $out)"
elif ((made_out)); then
  rmdir "$out" 2>/dev/null || true  # nothing ran: leave no empty run directory
fi
exit "$status"
