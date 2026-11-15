FROM ros:jazzy-ros-base@sha256:00b8d47a6e6384a2834e3d01fa87ae767876543baa574f2d64a75c04ea33c698
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-ros-gz ros-jazzy-ackermann-msgs ros-jazzy-foxglove-bridge \
    ros-jazzy-rosbag2-storage-mcap python3-colcon-common-extensions \
    python3-scipy python3-opencv python3-pandas libegl1 libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*
COPY autonomy/ /workspace/autonomy/
COPY apps/job_runner/campaign.py /runner/campaign.py
WORKDIR /workspace/autonomy/ros2
RUN . /opt/ros/jazzy/setup.sh && colcon build --merge-install \
    --event-handlers console_cohesion+
# The existing launch starts a server without a GUI, but needs EGL explicitly
# on a machine with no X server. Keep this adaptation local to this image.
RUN printf '#!/bin/sh\nif [ "$1" = sim ]; then exec /usr/bin/gz "$@" --headless-rendering; fi\nexec /usr/bin/gz "$@"\n' > /usr/local/bin/gz \
    && chmod 755 /usr/local/bin/gz
ENV HOME=/tmp/home ROS_LOG_DIR=/tmp/ros-log ROS_LOCALHOST_ONLY=1
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
ENV SOURCE_REVISION=$SOURCE_REVISION
ENTRYPOINT ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && source /workspace/autonomy/ros2/install/setup.bash && exec python3 /runner/campaign.py \"$@\"", "campaign"]
