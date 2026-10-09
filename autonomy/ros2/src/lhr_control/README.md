# lhr_control

Pure pursuit steering and curvature-based speed planning. Consumes
`/lhr/track/centerline`, `/lhr/vehicle/odom`, and mission status; publishes
`/lhr/vehicle/cmd`. Vehicle geometry and steering limits come from
[lhr_vehicle](../lhr_vehicle/README.md).

## Closed tracks and local paths

`closed_path=true` retains the existing simplified-mode closed-loop path
behavior. LiDAR perception launches set `closed_path=false`: lookahead and
curvature estimates do not wrap between the two ends of a partial scan-derived
path. Targets behind the vehicle are rejected. Empty paths, expired local
paths, or paths with no forward target produce a zero-speed command.

Open-path speeds respect acceleration limits from zero and decrease with the
available stopping distance to the path end, even below `v_min`. A missing
forward corridor therefore causes a stop rather than a turn back toward the
start of the path. These checks use only the perceived path, not track truth.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `closed_path` | `true` | Wrap lookahead and curvature for a complete circuit |
| `path_timeout_sec` | `1.0` | Stop if an open path is not updated within this time |

Other steering and speed parameters are listed in the
[workspace reference](../../README.md#lhr_control-pursuit_node).
Tests cover path endpoints, short horizons, stale/empty routes, open curvature,
and acceleration from rest. The controller still assumes ideal vehicle state
and actuation; closed-loop simulation does not validate hardware behavior.
