# 2026-10-04 Mid-360 acceptance test: running log

Kept during the test, unedited. Results and conclusions are in the [README](README.md). Tool paths are the test laptop's (`~/lidar-test`, `~/lhr-test-data`).

- Unit: Livox Mid-360 S/N 47MCN860030034, fw 13.18.2.40, loader 13.17.99.20, IP 192.168.1.134.
- Host: MacBook Air (blue-crest), USB Realtek RTL8153 adapter (en5), 192.168.1.50, 100BASE-TX.
- Tools: ~/lidar-test (livox_mid360.py, livox_live.py streamer to Foxglove, geom.py, cone.py, cone_run.py).

## Timeline (CDT)

- 14:24 First contact. Probe: diag 0x0000, state WAKEUP, 44 C, 514 power-ups.
- 14:26 Indoor health capture, 10 s (raw/indoor_health_1426.npz): 199,995 pts/s, 0 packets lost,
  IMU 200.0 Hz, accel |a| 0.994 g, gyro bias x/y/z -1.21/-0.10/+1.02 deg/s at 46 C.
- 14:33 Geometry from that capture (geom.py): wall and ceiling RMS 0.54 to 0.74 cm (plan: < 2 cm);
  main corner 89.68 deg (plan: 90 +- 0.5); ceiling 0.17 deg off level and walls 0.06 to 0.29 deg off
  plumb against IMU gravity (plan: < 1 deg). All pass.
- Gyro X bias moves with temperature: -1.21 deg/s at 46 C, -2.8 at 56 C (9/27), about -3.4 at 60 to 63 C.
  The EKF has to estimate it online; a one-time startup calibration will not hold.
- 14:39:50, 15:01:21, 15:22:08 Data stopped for 5 to 6 s each time. USB Ethernet adapter re-enumerated
  (its USB session ID changed 21.5 min apart, matching 14:39 and 15:01; host counters reset). Sensor
  state stayed NORMAL and no power-up was counted. Host-side fault, not the LiDAR.
- 15:14:16 Sensor power cycled (power-ups 514 -> 515) when the setup was moved. Not a fault.
- 15:16 Mac idle-slept on battery and killed the streamer. Now run under caffeinate.
- No clean 60 min endurance run yet; longest clean stretch about 13 min (15:01 to 15:14).

## Cone test (indoors, garage)

- Sensor 0.58 m above the floor (tape; to the sensor base or window not yet confirmed). Cones placed
  along -x (bearing 180) because the laptop sits on the +x side.
- First 5 m attempt (15:27) unusable: the laptop about 13 cm from the window blocked about 100 deg on +x.
- 5 m level (15:32): cone found at bearing 177, 5.19 m. IMU says the sensor was tipped 2.5 deg, cone side
  up, so the bottom ~12 cm of the cone was below the field of view. Tape reference unclear (+19 cm bias).
- 15:47 Shimmed: IMU 0.9 deg, cone side low. All later runs at this setting.
- 5 m level re-run: 935 points (9.3 per frame) vs 433 when tipped 2.5 deg; whole cone visible incl. base.
- Tape read 10 to 18 cm short of the sensor at every position (consistent offset in how it was taped).
  Gray: trust the sensor. Figures are labelled with the sensor-measured range; runs.csv keeps both.
- Level series (sensor range / tape / points per frame / first 5 s): 1.43 m / 50 in / 10.4 / 513 (top 14 cm only);
  2.66 / 100 in / 15.3 / 756 (top 32 cm); 3.93 / 150 in / 12.1 / 614; 5.18 / 200 in / 9.5 / 477 (repeat of the
  5 m run within 2%); 6.46 / 250 in / 6.9 / 342; 7.73 / 300 in / 5.4 / 269; 9.00 / 350 in / 4.0 / 204;
  10.27 / 400 in / 3.4 / 162 (PASS, plan line 50); 11.53 / 450 in / 1.7 / 86 (85% of frames hit).
- 11.53 m: hits stop at 39 cm (cone ~52 cm). Nothing between sensor and cone; back wall 0.7 m behind. Gray inspected
  the figures and the cone and judged the run accurate. Cause of the missing top not identified.
- Picker fix: the 350 in run first locked onto a 46 cm wide object at 7.61 m; cone.py now requires a cone-shaped
  cluster (< 35 cm wide, top 35 to 65 cm). All runs re-analyzed; none changed except 350 in (now 9.00 m).
- Garage back wall at 12.25 m along -x: 11.5 m is the longest indoor position. 15 and 20 m need the door open or outside.
- Adapter dropouts also at 15:42:24 and 15:45:28 (none landed in a recording).
- 16:13 Small-cone run at 450 in discarded: recorded before the cone was placed (files in raw/discarded/).
- Small FSAE cone series (level, same line; sensor range / tape / per frame / frames hit / first 5 s):
  50 in: NOT VISIBLE (cone entirely below the field-of-view floor; zero returns under 45 cm at 0.8 to 2.2 m);
  2.65 m / 100 in / 5.8 / 100% / 287 (top 17 cm only); 3.88 / 150 in / 6.0 / 100% / 298; 5.15 / 200 in / 5.4 / 100% / 267;
  6.45 / 250 in / 4.0 / 100% / 209; 7.72 / 300 in / 3.4 / 99% / 166; 8.99 / 350 in / 2.4 / 91% / 119;
  10.27 / 400 in / 2.1 / 90% / 103 (PASS, plan line 50); 11.54 / 450 in / 1.2 / 67% / 59.
- Small/large ratio once both are fully visible: 0.49 at 3.9 m rising to 0.62 at 7.7 m (side areas: 0.51).
- At 11.5 m both cones lose their top (large: hits to 39 of ~52 cm; small: to 29 of ~33 cm). Not explained.

## Outdoors (driveway in front of the garage), from 16:37
- Moved outside; sensor power cycled. Same height 0.58 m; IMU 1.3 deg cone-side low. USB adapter now on the Mac's other port.
- Weather (Open-Meteo for the Pickle campus, 16:30): overcast, 84% low cloud, direct radiation 0 W/m2, diffuse 250 W/m2,
  22.8 C, 89% RH. Sun at 16:45: elevation 30 deg, azimuth 244 deg, off to the side of the cone line. Not a direct-sun test.
- 16:40 outdoor baseline (raw/outdoor_baseline_1640), compared with the garage (envcompare.py): sensor noise flags unchanged
  (1.4%/0.2% vs 1.5%/0.3%); floor noise at 5-9 m by 0.5 m patch fits 0.9 cm vs 1.9 cm in the garage; returns to 41 m
  (99th pct), some to 60 m. Isolated points 840/s vs 81/s, but grouped at consistent 23-60 m ranges a few degrees above
  the horizon: distant trees and buildings, not sunlight noise. Gyro X bias -2.8 deg/s at 57 C (was -4.3 at 66 C).
- Small cone outdoors (sensor range / tape / per frame / frames hit / first 5 s): 50 in NOT VISIBLE; 2.65 / 100 in / 5.5 / 100% / 276;
  3.92 / 150 in / 6.2 / 100% / 319; 5.18 / 200 in / 4.6 / 100% / 226; 6.45 / 250 in / 3.7 / 100% / 186; 7.72 / 300 in / 2.4 / 99% / 124;
  9.00 / 350 in / 1.3 / 72% / 66; 10.28 / 400 in: run1 49, run2 51, run3 46 in 5 s (61-62% of frames hit), average 49 = marginal FAIL.
  One more 400 in try with people in view (46) is in raw/discarded/.
- Daylight effect: cone-body hits outdoors vs garage -5%, +10%, -7% at 2.7 to 5.2 m, then -23%, -35%, -49%, about -58% at 6.5, 7.7,
  9.0, 10.3 m. Returned points are just as bright (median reflectivity ~30 to 36 both). Consistent with ambient daylight (even
  overcast, ~110 to 250 W/m2 modelled) raising the noise floor so weak, far and grazing returns drop out. "Less sun" runs did not differ.
- Core temperature outdoors climbed with no plateau: 57.8 C (16:41) to 72.0 C (17:01). Garage plateau was ~66 C.
- 17:01:08 Ethernet link to the sensor went down (media none) at the start of the 450 in run; run discarded (no data). Cause TBD.
- 17:01:08 to 17:02:51 outage: the bench power supply output read 0 V / 0 A (supply shut its output off; protection trip suspected, cause unknown). Restarting the supply restored the sensor. Not a sensor fault.
- 17:06 Small cone 400 in run 4, dimmer (cone casts no shadow), sensor 67.5 C after the supply restart: 65 in 5 s, 72% of
  frames hit. 450 in (after restart): 11.56 m, 49 in 5 s, 62%. Light level drives the outdoor drop; the ~4 C cooler sensor
  may also have helped, not separated.
- Far range, small cone, 20 s runs, confirmed new against the 10 m run: 15.30 m / 597 in: 0.41 per frame, 34% of frames,
  24 in 5 s. 17.85 m / 697 in: 0.14 per frame, 14% of frames, 5 in 5 s.
- 17:14 to 17:27 Large cone outdoors at dusk (sensor range / tape / per frame / frames hit / first 5 s): 1.45 / 50 in / 11.4
  (top only); 2.68 / 100 in / 16.4; 3.94 / 150 in / 12.5; 5.20 / 200 in / 8.5; 6.48 / 250 in / 7.0 (first try had people in
  view, re-run); 7.78 / 300 in / 4.4; 9.07 / 350 in / 3.5; 10.35 / 400 in / 2.4 / 95% / 113 (PASS); 11.61 / 450 in / 1.7 / 89%
  / 92 (whole cone visible: the garage 11.5 m top cut was the garage position); 15.38 / 597 in / 0.49 / 44% (20 s);
  17.91 / 697 in / 0.65 / 52% (20 s; light fading during the run, dusk numbers are best case).
- 17:29 Stopped. Streamer stopped, sensor set to WAKEUP (motor off). Not run: 60 min endurance + connector wiggle, IMU
  axis rolls, visual inspection, 3 deg down-tilt series, direct sun, 790 in (20 m).
