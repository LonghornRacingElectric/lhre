# On-car architecture (target)

The stack as it should look on Orion for the April 2027 demo. Nothing to the
right of the CAN bus exists in software yet; the point of this page is to
have one picture to build toward. Hardware is the 8/2 decided order and the
node names follow the Software Architecture page in Notion — if this page
and Notion disagree, Notion wins.

Solid boxes exist today (ours, or off-the-shelf drivers). Dashed boxes are
not written yet.

```mermaid
flowchart LR
    classDef planned stroke-dasharray: 6 4

    subgraph sensors["Sensors — mast and chassis"]
        lidar["Velodyne VLP-16<br>Ethernet, 10 Hz"]
        cam["Stereolabs ZED 2i<br>USB3"]
        gnss["ArduSimple F9P heading kit<br>dual-antenna RTK, 5–8 Hz"]
        imu["IMU"]
    end

    subgraph jetson["Jetson Orin NX 16 GB — ROS 2 Jazzy"]
        subgraph drivers["Drivers"]
            dlidar["velodyne_driver"]
            dcam["zed-ros2-wrapper"]
            dgnss["ublox_gps"]
            dcan["ros2_socketcan"]
        end
        subgraph perception["Perception"]
            det["lidar_cone_detector"]
            cls["cone_color_classifier"]:::planned
            fus["cone_fusion"]:::planned
        end
        est["lhr_state_estimation<br>EKF: IMU + GNSS + wheel speeds + steer angle"]:::planned
        subgraph pnc["Planning & control"]
            tb["track_builder"]
            pp["pure_pursuit"]
            lon["longitudinal controller<br>speed → throttle / brake"]:::planned
        end
        mm["mission_manager"]
        safe["safety_node<br>heartbeat · watchdog · e-stop mapping"]:::planned
        vif["vehicle_interface<br>ROS ↔ CAN"]:::planned
        ops["rosbag2 logging · Foxglove telemetry · PPS/PTP time sync"]:::planned
    end

    subgraph can["CAN"]
        steer["Kraken X60 steering motor<br>2.5:1 on the column · CTRE Phoenix"]
        brake["Thomson Electrak HD brake actuator<br>J1939 · master cylinder"]
        vcu["VCU<br>torque request → inverter · line valves · wheel speeds · mode"]
    end

    subgraph loop["Hardware safety loop — no software in the path"]
        res["RES: Gross Funk GF2000i<br>(fallback: DIY heartbeat link)"]
        relay["normally-energized relay chain"]
        spring["fail-safe spring back-drives<br>the master cylinder"]
    end

    lidar --> dlidar --> det
    cam --> dcam --> cls
    det --> fus
    cls --> fus
    fus -- "/lhr/sensor/cones_detected<br>(classified)" --> tb
    gnss --> dgnss --> est
    imu --> est
    dcan -- "wheel speeds, steer angle" --> est
    est -- "/lhr/vehicle/odom + TF" --> det
    est --> tb
    est --> pp
    tb -- "/lhr/track/centerline" --> pp
    pp --> lon
    mm -- "/lhr/mission/status" --> pp
    lon -- "/lhr/vehicle/cmd<br>steer · throttle · brake" --> vif
    safe -- "heartbeat, mode" --> vif
    vif <--> dcan
    dcan <--> steer
    dcan <--> brake
    dcan <--> vcu
    res --> relay --> spring
    relay -- "torque enable" --> vcu
```

## What changes between sim and car

The upper stack — `track_builder`, `pure_pursuit`, `mission_manager` — is
the same code that laps in Gazebo today. Everything under the common topic
interface swaps (see the
[architecture comparison](../../ros2/README.md#architecture-comparison)):

| Sim today | On the car | Owner |
|-----------|------------|-------|
| Gazebo GPU LiDAR / `sensor_sim` cones | VLP-16 through `velodyne_driver`, same `lidar_cone_detector` | Perception |
| No camera; unclassified cones | ZED 2i → `cone_color_classifier` → `cone_fusion`; color-aware Delaunay | Perception |
| Ground-truth `OdometryPublisher` | `lhr_state_estimation` EKF on IMU + RTK GNSS + wheel speeds + steer angle | State estimation |
| `pure_pursuit` publishes a speed; Gazebo sets wheel velocity directly | Longitudinal controller turns speed into throttle and brake (with software brake bias) | Planning & control |
| `joint_cmd_adapter` → Gazebo joints | `vehicle_interface` → CAN: column angle to the steering motor, actuator position to the brake, torque request to the VCU; feedback back in | Sim & test infra + ELC |
| `auto_go` timer | Physical go from the RES; `safety_node` heartbeat that the VCU watchdogs | Lead + ELC |
| `map → base_link` only | `map → odom → base_link` plus sensor frames with measured extrinsics | State estimation |
| `use_sim_time` | PPS from GNSS into the LiDAR, PTP/chrony on the Jetson | Sim & test infra |

Two things deliberately have no software in the loop: the remote emergency
stop and the fail-safe brake. Power loss or a dropped heartbeat de-energizes
the relay chain, which removes torque enable and lets the spring apply the
brakes. `safety_node` maps stack-side faults onto the same chain; it never
replaces it.

## Open decisions this picture depends on

- The CAN message set between the Jetson and the VCU (commands, feedback,
  heartbeat) — to be written as an ADR with ELC before the bench rig.
- Steering command semantics: column angle versus road-wheel angle, and the
  measured rack ratio. `vehicle.yaml` is the home for the numbers.
- Brake command semantics: actuator position versus pressure, and how bias
  is commanded through the line valves.
- Whether the RES is the sponsored GF2000i or the DIY heartbeat link
  (decision by ~November per the hardware page).

Related: [retrofit roadmap](retrofit-roadmap-2026-27.md) (dates),
[camera fusion](camera-fusion.md) (perception detail),
`lhr_vehicle/config/vehicle.yaml` (vehicle numbers; lands with PR #47).
