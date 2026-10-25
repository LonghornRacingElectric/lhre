#!/usr/bin/env bash
# Check the committed Foxglove layout against a recorded bag.
#
# A layout references topics and frames by name, and nothing rebuilds it
# when one is renamed: it just comes up with empty panels, which reads
# as "the sim is broken" rather than "the layout is stale". This answers
# which it is before anyone opens the app.
#
#   ./scripts/check_layout.sh data/bags/<run_id>
#
# Exit 0 only when every topic and frame the layout names is in the bag.
set -e
cd "$(dirname "$0")/.."

source "$(dirname "$0")/_ros_env.sh"
source install/setup.bash || true

BAG="${1:-}"
LAYOUT="${LAYOUT:-foxglove/lhr_sim.json}"

if [ -z "$BAG" ]; then
    echo "usage: $0 <bag directory>" >&2
    echo "  record one with: ros2 launch lhr_demo mvs_demo.launch.py \\" >&2
    echo "                     record:=true lidar:=true" >&2
    exit 2
fi

python3 - "$BAG" "$LAYOUT" <<'PY'
import glob
import json
import os
import sys

from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message

bag_arg, layout_path = sys.argv[1], sys.argv[2]

# Accept the bag directory, or anything inside it, since tab completion
# lands on the .mcap file about as often as on its parent.
if os.path.isdir(bag_arg):
    found = glob.glob(os.path.join(bag_arg, '**', '*.mcap'), recursive=True)
else:
    found = [bag_arg]
if not found:
    print(f'FAIL: no .mcap under {bag_arg}', file=sys.stderr)
    sys.exit(1)
bag_dir = os.path.dirname(found[0])

layout = json.load(open(layout_path))

reader = SequentialReader()
reader.open(StorageOptions(uri=bag_dir, storage_id='mcap'),
            ConverterOptions('', ''))
types = {t.name: t.type for t in reader.get_all_topics_and_types()}

# Frames have to be read out of the messages: a bag lists topics, and
# the tf tree only exists inside them.
frames = set()
while reader.has_next():
    topic, data, _ = reader.read_next()
    if topic in ('/tf', '/tf_static'):
        msg = deserialize_message(data, get_message(types[topic]))
        for tf in msg.transforms:
            frames.add(tf.header.frame_id)
            frames.add(tf.child_frame_id)

missing = []


def check(kind: str, name: str, present: bool):
    print(f'  {"ok  " if present else "MISS"}  {kind:<6} {name}')
    if not present:
        missing.append(f'{kind} {name}')


print(f'{os.path.basename(bag_dir)}: {len(types)} topics, '
      f'{len(frames)} frames')
for panel in layout['configById'].values():
    for topic in panel.get('topics', {}):
        check('topic', topic, topic in types)
    for frame in panel.get('transforms', {}):
        # Keys are written 'frame:<name>' in an exported layout.
        name = frame.split(':', 1)[-1]
        check('frame', name, name in frames)
    for series in panel.get('paths', []):
        value = series['value']
        check('topic', value, value.split('.')[0] in types)
    if 'topicPath' in panel:
        value = panel['topicPath']
        check('topic', value, value.split('.')[0] in types)
    follow = panel.get('followTf')
    if follow:
        check('frame', follow, follow in frames)

if missing:
    print(f'\nFAIL: the layout names {len(missing)} thing(s) this bag '
          f'does not have:', file=sys.stderr)
    for item in missing:
        print(f'  {item}', file=sys.stderr)
    sys.exit(1)
print('\nPASS: every topic and frame the layout names is in the bag')
PY
