#!/usr/bin/env bash
# Run the kinematic stack with no display and exit on the run's verdict.
#
# ros2 launch always exits 0: LaunchService only returns non-zero when
# launch itself raises, never because a node it managed failed. So the
# verdict cannot come from the launch exit code, and this script reads it
# out of the metrics row instead.
#
#   ./scripts/run_headless.sh seed:=3 track_style:=oval
#
# Exit 0 only when the run reached `lap` or `mission_finished`.
set -e
cd "$(dirname "$0")/.."

source "$(dirname "$0")/_ros_env.sh"
source install/setup.bash || true

CSV="${METRICS_CSV:-data/metrics.csv}"
rm -f "$CSV"

# output_csv has to reach the node too, or this reads one path while the
# node writes another.
ros2 launch lhr_demo mvs_demo.launch.py output_csv:="$CSV" "$@" || true

if [ ! -f "$CSV" ]; then
    echo "FAIL: the run wrote no metrics row to $CSV" >&2
    exit 1
fi

python3 - "$CSV" <<'PY'
import csv
import sys

CLEAN = ('lap', 'mission_finished')
with open(sys.argv[1], newline='') as f:
    rows = list(csv.DictReader(f))

if not rows:
    print('FAIL: metrics file has a header and no rows', file=sys.stderr)
    sys.exit(1)

row = rows[-1]
summary = '  '.join(
    f'{k}={row[k]}' for k in
    ('outcome', 'duration_s', 'max_cte', 'off_track_count', 'git_sha')
    if k in row)
print(summary)

if row.get('outcome') not in CLEAN:
    print(f"FAIL: run ended as {row.get('outcome')!r}", file=sys.stderr)
    sys.exit(1)
PY
