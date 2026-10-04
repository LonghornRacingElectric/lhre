#!/usr/bin/env bash
# Repeat one run N times and fail if the gated metrics drift too far.
#
# A regression gate is only meaningful if an unchanged stack scores the
# same twice. Bit-identical output is not on offer: every node is a
# separate process with its own executor, so message interleaving varies
# between runs. What can be held is a bound on how far the numbers move,
# and that bound is what tells you whether a diff in a later run came
# from the code or from the scheduler.
#
#   ./scripts/check_stability.sh                  # 5 runs, seed 3
#   RUNS=8 SEED=1 ./scripts/check_stability.sh
#   TOL=0.03 ./scripts/check_stability.sh         # one band for all
#
# Extra arguments pass through to the launch file.
set -e
cd "$(dirname "$0")/.."

source "$(dirname "$0")/_ros_env.sh"
source install/setup.bash || true

RUNS="${RUNS:-5}"
SEED="${SEED:-3}"
TOL="${TOL:-}"
CSV="${METRICS_CSV:-data/stability.csv}"

rm -f "$CSV" "$CSV.old"
for i in $(seq 1 "$RUNS"); do
    echo "--- run $i/$RUNS (seed=$SEED) ---"
    # Appends to the same CSV, so the comparison below sees every run.
    ros2 launch lhr_demo mvs_demo.launch.py \
        output_csv:="$CSV" seed:="$SEED" "$@" >/dev/null 2>&1 || true
done

if [ ! -f "$CSV" ]; then
    echo "FAIL: no run wrote a metrics row to $CSV" >&2
    exit 1
fi

python3 - "$CSV" "$RUNS" "$TOL" <<'PY'
import csv
import sys

path, expected = sys.argv[1], int(sys.argv[2])
override = float(sys.argv[3]) if sys.argv[3] else None
CLEAN = ('lap', 'mission_finished')

# Per-metric bands, and only arc-length-weighted metrics: a
# sample-weighted mean moves with the odom publish rate, so its spread
# would measure the scheduler rather than the stack.
#
# An integral over the lap averages its own noise away and can be held
# tight. A maximum cannot: it is an extreme-value statistic, so its
# observed spread only grows as runs are added and any tight band is a
# promise that breaks on run six. Whether max_cte is *acceptable* is a
# separate, absolute question, and that ceiling belongs in the per-run
# gate in run_headless.sh.
BANDS = {
    'mean_cte': 0.02,
    'path_length_m': 0.01,
    'max_cte': 0.05,
}

with open(path, newline='') as f:
    rows = list(csv.DictReader(f))

if len(rows) != expected:
    print(f'FAIL: expected {expected} rows, found {len(rows)}', file=sys.stderr)
    sys.exit(1)

dirty = [r['outcome'] for r in rows if r['outcome'] not in CLEAN]
if dirty:
    print(f'FAIL: runs ended as {dirty}', file=sys.stderr)
    sys.exit(1)

failed = []
print(f'\n{"metric":<16}{"min":>11}{"max":>11}{"mean":>11}'
      f'{"spread":>9}{"band":>8}')
for name, band in BANDS.items():
    limit = override if override is not None else band
    vals = [float(r[name]) for r in rows]
    lo, hi = min(vals), max(vals)
    mean = sum(vals) / len(vals)
    # Relative to the mean, so a band reads the same whatever the unit.
    spread = (hi - lo) / mean if mean else 0.0
    if spread > limit:
        failed.append(f'{name} {spread:.2%} > {limit:.2%}')
    print(f'{name:<16}{lo:>11.4f}{hi:>11.4f}{mean:>11.4f}'
          f'{spread:>8.2%}{limit:>8.1%}')

print(f'\n{expected} runs at the same seed')
if failed:
    print('FAIL: ' + '; '.join(failed), file=sys.stderr)
    sys.exit(1)
print('PASS: repeat runs agree within their bands')
PY
