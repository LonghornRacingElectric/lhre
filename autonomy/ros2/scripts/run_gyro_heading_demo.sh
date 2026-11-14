#!/usr/bin/env bash
# Experimental calibrated gyro heading + GNSS position; trailing args override defaults.
set -euo pipefail
cd "$(dirname "$0")/.."
unset FONTCONFIG_FILE WAYLAND_DISPLAY
export QT_QPA_PLATFORM=xcb
export PATH="/usr/bin:$PATH"
exec ./scripts/run_gazebo_demo.sh \
  gui:=true rviz:=true foxglove:=true \
  track_style:=autocross seed:=3 perception:=lidar estimator:=ekf \
  imu_noise:=true use_wheel_speeds:=false \
  use_gnss:=true simulate_gnss:=true \
  calibrate_gyro:=true gnss_heading_gain:=0.0 use_stationary_updates:=true \
  safety:=true safety_lidar_timeout_s:=1.5 \
  output_csv:=data/gyro-heading-visual.csv "$@"
