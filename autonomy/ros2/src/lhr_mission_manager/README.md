# lhr_mission_manager

Mission state and Go/emergency gating for the ROS 2 stack. The controller
only drives under `DRIVING`. Autocross consumes the metrics lap signal;
skidpad consumes `/lhr/skidpad/complete` from the ordered route planner.

Skidpad completion is accepted only during `DRIVING`. The planner confirms
all four laps, exit and a one-second standstill before publishing it; the
manager also checks vehicle speed before entering `FINISHED`. Autocross's
lap flag cannot finish skidpad. Emergency reset clears completion, but the
skidpad planner itself requires a fresh simulation/node restart after a
latched route fault or a completed run.

Run package tests from the ROS workspace with
`colcon test --packages-select lhr_mission_manager`.
