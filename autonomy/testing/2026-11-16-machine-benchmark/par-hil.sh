#!/usr/bin/env bash
# N gate campaigns at once on hil, one GPU container each. run_gate.sh's cleanup
# pkills every gz sim and bridge it can see, so each campaign needs its own PID
# namespace (a container), and --network none plus its own ROS domain and
# Gazebo partition keeps their DDS and gz-transport traffic apart.
#   par-hil.sh N
N=${1:?N}
TAG=par$N
for i in $(seq 1 "$N"); do
    docker rm -f "lhr-$TAG-$i" >/dev/null 2>&1
    docker run -d --name "lhr-$TAG-$i" --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all \
        --shm-size 2g --network none \
        -e ROS_DOMAIN_ID=$((20 + i)) -e GZ_PARTITION="$TAG-$i" -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
        -v "$HOME/lhre-bench/autonomy:/home/ubuntu/autonomy" -v "$HOME/lhre-bench:/opt/lhr:ro" \
        lhr-autonomy-dev >/dev/null
    docker cp /tmp/bench.sh "lhr-$TAG-$i:/tmp/bench.sh"
    docker exec -u root "lhr-$TAG-$i" chmod 755 /tmp/bench.sh
done
sleep 5
for i in $(seq 1 "$N"); do
    docker exec -d -u ubuntu "lhr-$TAG-$i" bash -lc \
        "WS=/home/ubuntu/autonomy/ros2 OUT=/home/ubuntu/autonomy/ros2/data/2026-11-16-$TAG-hil-$i /tmp/bench.sh $TAG-hil-$i > /home/ubuntu/autonomy/ros2/data/$TAG-hil-$i.out 2>&1"
done
sleep 10
for i in $(seq 1 "$N"); do
    echo "lhr-$TAG-$i: $(docker exec "lhr-$TAG-$i" bash -c 'pgrep -f run_gate.sh | head -1') domain=$((20 + i))"
done
