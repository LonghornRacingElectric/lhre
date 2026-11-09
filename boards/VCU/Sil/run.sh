#!/usr/bin/env bash
# Runs the VCU firmware against the car model in the SIL dev container.
#
#   boards/VCU/Sil/run.sh --fmu PATH [--scenario launch] [--out DIR] [vcusil options]
#   boards/VCU/Sil/run.sh --test [--fmu PATH]
#
# The first form builds //boards/VCU:libvcu_sil.so from this checkout and
# runs one scenario; other options go to python -m vcusil (README.md lists
# them). --test runs the SIL's C++ tests and the host's pytest suite instead;
# with an FMU the end-to-end tests run too. The FMU (GPL, never committed)
# may also come from VCU_SIL_FMU. Exit status: 0 completed, 1 the car model
# failed, 2 anything else, including a firmware that does not build.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
image=lhre-vcu-sil:local
min_free_gb=10

usage() { sed -n '2,12s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
die() { echo "run.sh: $*" >&2; exit 2; }

fmu="${VCU_SIL_FMU:-}"
out=""
scenario=launch
mode=run
pass=()
while (($#)); do
  case "$1" in
    --fmu) (($# >= 2)) || die "--fmu needs a path"; fmu="$2"; shift 2 ;;
    --out) (($# >= 2)) || die "--out needs a path"; out="$2"; shift 2 ;;
    --scenario) (($# >= 2)) || die "--scenario needs a name"; scenario="$2"; pass+=("$1" "$2"); shift 2 ;;
    --test) mode=test; shift ;;
    -h | --help) usage; exit 0 ;;
    *) pass+=("$1"); shift ;;
  esac
done

command -v docker >/dev/null || die "docker is not installed"
docker info >/dev/null 2>&1 || die "docker is not running"
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
  exec docker run --rm "${mounts[@]}" -e VCU_SIL_FMU="$fmu_in_container" "$image" bash -c '
    set -euo pipefail
    bazel test --config=local --symlink_prefix=/ --test_output=errors \
      //boards/VCU:vcu_sil_rig_test //boards/VCU:vcu_sil_test
    bazel build --config=local --symlink_prefix=/ //boards/VCU:libvcu_sil.so
    export VCU_SIL_LIB="$(bazel info --config=local bazel-bin)/boards/VCU/libvcu_sil.so"
    cd /work/lhre/boards/VCU/Sil/host
    exec python3 -m pytest -p no:cacheprovider -q tests "$@"
  ' run.sh "${pass[@]+"${pass[@]}"}"
fi

out="${out:-$here/out/$scenario-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
status=0
docker run --rm "${mounts[@]}" -v "$out:/out" "$image" bash -c '
  set -euo pipefail
  if ! bazel build --config=local --symlink_prefix=/ --noshow_progress \
      --ui_event_filters=-info //boards/VCU:libvcu_sil.so; then
    echo "run.sh: libvcu_sil.so did not build (see the compiler errors above)" >&2
    exit 2
  fi
  bin="$(bazel info --config=local bazel-bin)" || exit 2
  cd /work/lhre/boards/VCU/Sil/host
  exec python3 -m vcusil --lib "$bin/boards/VCU/libvcu_sil.so" --fmu "$0" --out /out "$@"
' "$fmu_in_container" "${pass[@]+"${pass[@]}"}" || status=$?
# vcusil exits 0, 1 or 2; any other status (docker, a signal) is the harness.
case "$status" in 0 | 1 | 2) ;; *) status=2 ;; esac
[[ -f "$out/meta.json" ]] && echo "  wrote $out"
exit "$status"
