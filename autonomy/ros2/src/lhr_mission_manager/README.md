# lhr_mission_manager

Coordinate the demo's OFF, READY, DRIVING, FINISHED and EMERGENCY states.
The controller reads `/lhr/mission/status`; an available centerline lets the
manager enter READY. Go, emergency and reset commands arrive on the matching
`/lhr/mission/*` Boolean topics.

Completion depends on the mission: autocross waits for metrics' verified lap
on `/lhr/metrics/lap_complete`, acceleration for 75.30 m of travel along the
start heading (the nose on the finish line), inspection for 28 s. Skidpad,
ebs_test and manual never complete yet. Once complete, the manager publishes
`true` on `/lhr/mission/complete` every tick and stays in DRIVING so pursuit
can brake; FINISHED means the car is below 0.5 m/s.

Run it through [the demo launcher](../lhr_demo/README.md), or use
`ros2 run lhr_mission_manager mission_manager` in a built ROS workspace.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `mission` | `autocross` | Mission selection |
| `auto_go` | `true` | Start after the READY hold without an explicit go command |
| `ready_hold_sec` | `5.0` | Time to hold READY |
| `status_hz` | `10.0` | Status update rate |
| `require_safety_heartbeat` | `false` | Also wait for `/lhr/safety/heartbeat` before READY |
| `safety_settle_s` | `2.0` | How long the heartbeat must arrive without a gap |

With `require_safety_heartbeat`, READY waits until `safety_node`'s
heartbeat has arrived for `safety_settle_s` with no gap over 0.25 s.
Arming on the first centerline would arm in the busiest moment of startup,
when a sensor that is merely late reads as stale and safety latches an
emergency ([#164](https://github.com/LonghornRacingElectric/lhre/issues/164)).
A check that never passes keeps the car in OFF instead.

The supported mission names are inspection, manual, ebs_test, acceleration,
skidpad and autocross. The state machine uses the node clock; demo launches
select simulated time. `/lhr/debug/mission_state` publishes the numeric state
for plotting. Node cleanup tolerates an already-stopped ROS context after
launch shutdown.
