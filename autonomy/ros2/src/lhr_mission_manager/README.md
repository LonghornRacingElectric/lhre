# lhr_mission_manager

Coordinate the demo's OFF, READY, DRIVING, FINISHED and EMERGENCY states.
The controller reads `/lhr/mission/status`; an available centerline lets the
manager enter READY. Go, emergency and reset commands arrive on the matching
`/lhr/mission/*` Boolean topics. Lap completion comes from metrics.

Run it through [the demo launcher](../lhr_demo/README.md), or use
`ros2 run lhr_mission_manager mission_manager` in a built ROS workspace.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `mission` | `autocross` | Mission selection |
| `auto_go` | `true` | Start after the READY hold without an explicit go command |
| `ready_hold_sec` | `5.0` | Time to hold READY |
| `status_hz` | `10.0` | Status update rate |

The supported mission names are inspection, manual, ebs_test, acceleration,
skidpad and autocross. The state machine uses the node clock; demo launches
select simulated time. `/lhr/debug/mission_state` publishes the numeric state
for plotting. Node cleanup tolerates an already-stopped ROS context after
launch shutdown.
