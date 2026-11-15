# Dev container for the autonomy stack on non-Ubuntu hosts (macOS, Windows).
# Base: Ubuntu 24.04 + ROS 2 Jazzy desktop + a lightweight desktop served over
# noVNC (browser at http://localhost:6080). Works on arm64 (Apple Silicon) and amd64.
FROM tiryoh/ros2-desktop-vnc:jazzy

RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-ros-gz \
    ros-jazzy-ackermann-msgs \
    ros-jazzy-tf2-ros \
    ros-jazzy-foxglove-bridge \
    ros-jazzy-plotjuggler-ros \
    python3-colcon-common-extensions \
    python3-numpy \
    python3-scipy \
    python3-opencv \
    python3-pandas \
    && rm -rf /var/lib/apt/lists/*

# The base image ships a pip numpy 2.x in /usr/local that shadows apt's numpy,
# breaking apt's scipy and OpenCV (built against numpy 1.x). Shadow both with
# wheels that support the base image's NumPy ABI.
RUN pip install --no-cache-dir --break-system-packages --ignore-installed \
    "fmpy==0.3.32" "scipy>=1.14" "pandas>=2.2" "opencv-python-headless>=4.10,<5"
