#!/usr/bin/env bash
# N gate campaigns at once in black-ridge's WSL (run as root). WSL has one PID
# namespace, and run_gate.sh's cleanup pkills every gz sim it can see, so each
# campaign gets its own PID, mount and network namespace (loopback only), plus
# its own ROS domain and Gazebo partition. Gazebo renders on the 4070 via d3d12.
#   par-wsl.sh N
N=${1:?N}
TAG=par$N
for i in $(seq 1 "$N"); do
    unshare --pid --net --fork --mount-proc bash -c "
        ip link set lo up
        su - gray -c 'export ROS_DOMAIN_ID=$((30 + i)) GZ_PARTITION=$TAG-$i ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
            GALLIUM_DRIVER=d3d12 MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA
            WS=\$HOME/lhre/autonomy/ros2 OUT=\$HOME/lhr-test-data/2026-11-16-$TAG-black-ridge-$i \$HOME/bench.sh $TAG-black-ridge-$i'
    " > "/tmp/$TAG-$i.out" 2>&1 &
done
wait
for i in $(seq 1 "$N"); do echo "== $i"; tail -1 "/tmp/$TAG-$i.out"; done
