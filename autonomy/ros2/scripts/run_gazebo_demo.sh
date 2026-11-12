#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

source "$(dirname "$0")/_ros_env.sh"
source install/setup.bash || true

# The launch resolves the world from track_style and seed (autocross seed 1
# by default), so the world, cones, mission and pairing width always agree.
# Picking the first .sdf here used to load acceleration_seed1 under an
# autocross configuration once that world sorted first.
ros2 launch lhr_gazebo gazebo_demo.launch.py "$@"
