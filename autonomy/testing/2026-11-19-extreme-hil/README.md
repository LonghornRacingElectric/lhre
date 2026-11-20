# Extreme autocross on HIL, 2026-11-19

Ran all seven fixed worlds at `dea7f991` with the Gazebo plant, Mid-360,
stereo fusion, EKF and safety enabled at the default LiDAR timeout. The
[gate runner](../tools/gate/README.md) used seed 1, one run per course and
a 180 simulated second cap. HIL used the Jazzy container and Quadro M4000.

| Course | Outcome |
| --- | --- |
| Narrow | Pass |
| Hairpin | Pass |
| Decreasing radius | Clean lap, then `STEER_TRACKING` while braking. A repeat with steering recording reached `FINISHED`. |
| Slalom and chicane | Pass |
| Doubleback | Clean lap, then `STEER_TRACKING` while braking |
| 500 m | Pass |
| Combined | Contact with `cone_yellow_91` invalidated the lap. Still in `DRIVING` at the cap. |

Four of seven runs passed the first campaign. No run left the course.
The repeat used the same driving settings and recorded
`/lhr/vehicle/cmd` and `/lhr/vehicle/steering_measured` alongside the gate
topics. Gray's separate campaign at the same revision passed five of seven
and also reproduced the doubleback and combined failures.
[Gray's run notes](https://github.com/LonghornRacingElectric/lhre/pull/218#pullrequestreview-5986891459).
