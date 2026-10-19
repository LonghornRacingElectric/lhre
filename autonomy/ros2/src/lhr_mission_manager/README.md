# Mission Manager

`mission_manager` publishes Off, Ready, Driving, Finished, and Emergency status.
See the [stack reference](../../README.md#lhr_mission_manager-mission_manager) for its parameters.

For the acceleration mission, `/lhr/acceleration/complete` is accepted only
while Driving. The acceleration planner confirms a stop near its configured
exit-lane target, using timestamped pose differences and a 0.5 s standstill
period. The manager then transitions to Finished. Reset clears the stored
completion flag. Autocross completion still comes from the lap metrics topic.

The simulator's default `auto_go=true` is a demo convenience; hardware
startup checks and the physical Remote Stop System remain separate work.
