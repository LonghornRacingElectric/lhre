# LiDAR mount study, 2026-11-08

## Question

Where on Orion should the Livox Mid-360 go, and at what angle?

## Answer

Just inside the main hoop and a little forward, **upside down, pitched
7° nose down**, on a bracket that adjusts from 4 to 10° in 1° steps.

- **The hoop is the only feasible mount** (Gray, 11/8). The sim's mount at
  `base_link` (1.8, 0, 0.55) is inside the nose.
- **Upside down** keeps the ground beside and just ahead of the car in view.
  The densest band of beams then points just below the horizon, ahead of the
  car. The team picked this over an upright mount tipped down.
- **7°** puts that band 15 m past the nose. At 6° it lands 22 m out, beyond
  the detector's 20 m range. Past 7.5° the top of the field of view starts to
  cut off far cones. A ±1° change in body pitch moves the band 12 to 22 m at
  7°, but 15 to 39 m at 6°.
- **Sim A/B:** at 7° the full stack passes 6 of 6 gate runs with no false
  cones, and maps cones a median 12 to 16 m past the nose. The 6°, 8° and
  nose-mount arms are still running.

The real angle still needs a field check with the sensor at hoop height (see
[Zeroing in](#zeroing-in)).

## Viewer

[Orion LiDAR Placement](https://claude.ai/artifact/1EQMk4p6uuaGGTxj8XcV3H) is a
3D view of the Mid-360 on Orion's CAD geometry. It shows where each elevation
band lands, the ground it covers and how many beams a small cone catches, for
any mount and pitch. The presets compare upright 6° down with upside down at
level, 6°, 7° and 8°. The source is in [`viewer/`](#files). Rebuild it with:

```bash
cd autonomy/testing/2026-11-08-lidar-mount-study/viewer
python3 build.py ../../../ros2/src/lhr_vehicle/meshes/orion.stl \
    ../../../ros2/src/lhr_lidar_sim/lhr_lidar_sim/patterns/mid360.csv.gz \
    template.html roll-hoop-lidar.html
```

The output is one self-contained page (about 0.9 MB) that opens in a browser.
It is generated, so it is not committed. This copy rebuilds byte for byte the
published version of 11/8.

## Geometry

From `orion.stl` in `lhr_vehicle/meshes` (CAD frame: front axle centreline at
the ground, x forward; `base_link` x = CAD x + 1.5494 m):

| | CAD x (m) | Height (m) |
| --- | --- | --- |
| Main hoop top | -0.995 (front face) | 1.115 |
| Rear wing top, right behind the hoop | | 1.083 |
| Nose tip | 0.955 | |
| Proposed sensor centre | -0.94 | 1.06 |

The proposed centre is `base_link` (0.609, 0, 1.06). The sensor top sits 25 mm
below the hoop top. The hoop is narrow at the top (about ±45 mm). The mesh has
no driver, helmet or head restraint, so the space claim belongs in SOLIDWORKS.

## Beam density

The Mid-360 sees -7.2° to +52.2° in its own frame. Its beams are not spread
evenly: a dense band peaks at -3.5° and a second bump sits at +42 to +48°,
with about one beam per square degree per frame between them. The 10/4 garage
capture (only the azimuth sectors where at least 97% of beams came back)
matches Livox's scan table in shape (correlation 0.84):

| Band (sensor frame) | Table | Real | Real / table |
| --- | --- | --- | --- |
| -5 to -1° | 1.51 | 1.82 | 1.21 |
| -1 to +2° | 1.18 | 1.34 | 1.14 |
| +10 to +30° | 0.97 | 0.74 | 0.76 |
| +42 to +49° | 1.06 | 1.31 | 1.24 |

Beams per square degree per 10 Hz frame. The real dense band is 21% denser
than the table and peaks 0.4° lower. The pitch sweep below uses the real
profile; the viewer and the sim use the table.

## Pitch sweep

Upside down, a beam at sensor elevation e leaves at world elevation -(e + pitch)
straight ahead. Distances are metres past the nose tip, sensor centre 1.06 m up.
"Reach" is the farthest distance where a small cone (325 mm) still gets at least
1 or 0.3 beams per frame. Full table: [results.csv](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-lidar-mount-study/results.csv).

| Pitch | Dense peak lands | Reach, 1 pt/frame | Reach, 0.3 pt/frame | Far cone tops cut off past | Peak at body pitch -1° / +1° |
| --- | --- | --- | --- | --- | --- |
| 5° | 38.6 | 14.8 | 32.0 | never | sky / 22.4 |
| 6° | 22.4 | 14.5 | 25.8 | never | 38.6 / 15.4 |
| 6.5° | 18.3 | 16.0 | 22.8 | never | 28.5 / 13.3 |
| **7°** | **15.4** | **15.0** | **21.8** | **never** | **22.4 / 11.6** |
| 7.5° | 13.3 | 13.5 | 20.5 | 143 | 18.3 / 10.2 |
| 8° | 11.6 | 12.2 | 18.2 | 51 | 15.4 / 9.1 |
| 9° | 9.1 | 10.5 | 14.5 | 22 | 11.6 / 7.4 |

6.5 to 7.5° is the sweet spot: the band lands inside the detector's 20 m and
stays there when the body pitches. At 5 m past the nose every pitch from 5 to
8° puts 3 to 4.5 beams per frame on a cone, so close-up visibility does not
depend on the choice.

## Sim A/B

The full Gazebo stack (Mid-360 caster, camera fusion, EKF, safety) ran the
rules-compliant autocross seeds 1 to 5 and one acceleration run per mount. The
Mid-360 sits at `base_link` (0.609, 0, 1.06), upside down, pitched 6, 7 or 8°
nose down. The control is today's sim mount in the nose. `detection_range.py`
measures how far past the nose each cone first enters the detector's map.

| Mount | Gate | Lap / reference | Max CTE (m) | Cones hit | False cones | First mapped, median past the nose (m) | Mapped by 12 m past the nose |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Upside down, 7° | 6 of 6 | 0.99 to 1.00 | 0.51 to 0.57 | 0 | 0 | 12.3 to 15.9 | 51 to 97% |
| Upside down, 6° | running | | | | | | |
| Upside down, 8° | running | | | | | | |
| Nose (today's sim) | running | | | | | | |

The detector stops at 20 m from the sensor, which is 18.1 m past the nose from
the hoop, so a band that lands farther out than that (6°) cannot help it.
Ranges are per run, over the six runs of each arm.

## Zeroing in

1. **Set the range requirement.** Latency plus braking distance at autocross
   speed says how far ahead a cone must be mapped. That sets where the band
   should land.
2. **Sim A/B** (above), in the full stack at 6, 7 and 8°.
3. **Static field test.** Sensor at hoop height (1.06 m) on a stand, upside
   down, small cones at 5, 10, 15 and 20 m. Read pitch off the Mid-360's own
   IMU, step through 6, 7 and 8°, and count points per cone.
4. **Measure body pitch** on hard braking and acceleration, once the car
   runs. If it moves more than about 1°, prefer the steeper setting.
5. **Adjustable bracket.** 4 to 10° in 1° steps, so the field result can be
   set without new parts.

## Files

| File | What it does |
| --- | --- |
| `viewer/template.html`, `viewer/build.py` | The 3D viewer and the script that packs the car mesh and beam density into it |
| `viewer/check_estimate.py` | Checks the viewer's points-on-cone estimate against `lhr_lidar_sim`'s full model |
| `real_profile.py` | Real returns by elevation from the 10/4 capture against Livox's table |
| `pitch_sweep.py` | Writes [results.csv](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-lidar-mount-study/results.csv), the upside-down pitch sweep |
| `detection_range.py` | Scores the sim A/B: where each cone first enters the detector's map |

## Method and provenance

- **Car geometry:** `orion.stl`, exported from the SOLIDWORKS full-car assembly
  `_26-000.SLDASM` (SVN r9480) and reduced on a 20 mm grid.
- **Beam pattern:** Livox's Mid-360 scan table, as replayed by `lhr_lidar_sim`.
- **Real profile:** 10/4 garage run `cone_05m_level`, from the
  [Mid-360 acceptance](../2026-10-04-mid360-acceptance/README.md) raw data on
  SharePoint.
- **Sim A/B:** this branch's mount arguments, on red-mesa from 18:07 CST on
  11/8. `run_gate.sh` with `TRACK_STYLE=rules_autocross SEEDS="1 2 3 4 5"
  REPEATS=1 ACCEL_REPEATS=1 SKIDPAD_REPEATS=0`, its `TOPICS` extended locally
  with `/lhr/sensor/cones_detected` and `/lhr/perception/cones_classified`, and
  `EXTRA` set to `mid360_mount_x_m:=0.609 mid360_mount_z_m:=1.06
  mid360_mount_roll_rad:=3.14159… mid360_mount_pitch_rad:=<pitch>
  lidar_sensor_transform_source:=tf lidar_height_filter_frame:=base_link
  lidar_ground_z_min:=0.15 lidar_ground_z_max:=1.05`. A first 7° try with a
  0.05 m ground cut was discarded: range noise on ground hits near the car made
  123 false cones on one lap. Data is on red-mesa under
  `~/lhr-test-data/2026-11-08-lidar-mount-ab`, upload pending.

## Limits

- No driver, helmet, head restraint, bracket or cable in the mesh.
- The sim's caster does not model the car's body, so the hoop mount sees
  ground the nose hides on the real car. The viewer does model it.
- Gazebo odometry is planar, so the sim A/B has no body pitch.
- Points on a cone are counted before range dropout. The 10/4 outdoor runs
  lost weak returns in daylight, so far cones get fewer points than this.
