# Stereo camera mount study, 2026-11-08

## Question

Where should Orion's ZED 2i sit relative to the Mid-360 on the main hoop?
Companion to the [LiDAR mount study](../2026-11-08-lidar-mount-study/README.md)
and follow-up to [#242](https://github.com/LonghornRacingElectric/lhre/issues/242).

## Answer

**Below the LiDAR, on the same stiff bracket, on the centreline, about 5°
nose down.** Leave about 12 cm from the LiDAR's optical centre to the
camera's top face, so the camera sits below the LiDAR's field rather than
blocking it. This puts the proposed camera centre near 0.92 m above ground:
approximately `base_link` (0.609, 0, 0.92) m, `rpy_rad: [0, 0.087, 0]`.

- Make camera pitch adjustable from 0 to 10°. Pitch mainly trades sky and
  glare for car in the image, with little change in cone visibility.
- Keep the camera front face at or ahead of the LiDAR front face, so neither
  sensor appears in the other's view.
- Above the LiDAR does not fit inside the primary-structure envelope
  (DT.2.3.a) without lowering the LiDAR and revisiting its study.
- **The mesh has no driver.** The proposed position is behind the driver's
  head; a seated driver may block most of the forward image. Packaging and
  driver visibility remain unresolved, not demonstrated by the viewer.

This records the design study, not a measured mount or acceptance test.
No camera-mount sim A/B or field test has been run. The proposed mount has
not been applied to `vehicle.yaml` or the generated Gazebo models here.

## Viewer

[Orion Camera Placement](https://claude.ai/artifact/ATy1MdmPQi7G36eYykjWZd)
places the ZED 2i on Orion's `orion.stl` geometry. It shows the left-image
view, ground where camera–LiDAR fusion can colour a cone, edge-cone pixel
counts, and matching `vehicle.yaml` values for different mounts and pitches.
It uses the stack's assumed 1280 × 720, 110° horizontal pinhole model.

The tables below are the viewer results recorded in the source study on
11/8. They have not been independently reproduced in this import. The
original viewer source is archived in [viewer/](viewer/README.md); rebuild
it with Python 3 and NumPy:

```bash
cd autonomy/testing/2026-11-08-camera-mount-study/viewer
python3 build.py ../../../ros2/src/lhr_vehicle/meshes/orion.stl \
    template.html orion-camera-placement.html
```

Open the generated page in a browser with internet access for its pinned
Three.js/BVH dependencies and fonts. The shared car mesh is embedded. The
generated HTML is ignored by git. This rebuild matches the Notion attachment
byte for byte; see [viewer provenance](viewer/README.md) for its SHA-256.

## Above or below the LiDAR

The LiDAR centre is at 1.06 m, upside down, with its top 25 mm below the
1.115 m hoop top. The ZED 2i housing is approximately 175 × 30 × 43 mm.

| | Above LiDAR | Below, about 4 cm gap | Below, about 12 cm gap |
| --- | --- | --- | --- |
| Camera centre height | 1.105 m | 1.005 m | 0.92 m |
| Fits DT.2.3.a envelope | No; top about 5 mm over hoop with zero clearance | Yes | Yes |
| Blocks LiDAR beams | No | Yes, steep side beams | No in the study's field-edge estimate |
| Edge cone pixels at 8 / 12 m, level | 91 / 46 | 91 / 46 | 91 / 46 |
| Estimated colour range | To 16.8 m | To 16.8 m | To 16.8 m |
| Car in level image | 8% | 10% | 14% |

Within this height range, the viewer reports nearly identical cone pixels,
colour range and ground coverage. Lowering the camera puts more car in the
bottom of the image. The choice is driven by packaging and LiDAR shadowing.

**Above:** the camera is 30.3 mm tall but only 25 mm remains under the hoop
top. Even with no bracket clearance it protrudes about 5 mm. Lowering the
LiDAR about 20 mm could make room for camera and plate, but reopens the
LiDAR geometry decision. The upside-down LiDAR sees no higher than about
+7° to its sides and +14° behind, so a camera above it is outside that field.

**Close below:** at about 4 cm drop, the camera shadows steep side rays,
roughly 25–52° down and within about ±15–30° of straight left/right. Those
rays cover ground about 0.8–2.3 m beside the hoop. An obstruction inside the
Mid-360's 0.1 m minimum range still prevents returns from behind it.

**About 12 cm below:** the top corners are about 88–90 mm from the LiDAR
axis. Clearing the field's bottom edge, 52° down at the sides and roughly
54–59° toward the front corners, needs about 113–122 mm of drop. Measure
from the optical centre, not the housing; verify with the bracket, ZED STEP
and Livox field-of-view model in SOLIDWORKS. This is an approximate clearance,
not a mechanical tolerance or a beam-table occlusion test.

## Why about 5° down

Reported sweep at camera centre height 1.005 m:

| Nose-down pitch | Edge cone pixels at 4 / 8 / 12 m | Estimated colour range | Car in image | Ground ahead colourable, to 15 m |
| --- | --- | --- | --- | --- |
| Level | 262 / 91 / 46 | 0–16.8 m | 10–14% | 73% |
| 5° | 255 / 90 / 45 | 0–16.8 m | 14% | 73% |
| 10° | 254 / 91 / 46 | 0–17.0 m | 17% | 73% |
| 20° | 269 / 99 / 51 | 0–18.0 m | 21% | 74% |

The source reports that the 0.92 m position matches within a pixel and adds
about four percentage points of car in the image. The level row's 10–14%
range is retained from the source rather than treated as a new measurement.

A few degrees down reduces sky and the chance of sun in frame. Beyond about
10°, the car occupies more image for little range benefit. The study's
projected cone outline falls below 25 pixels near 17 m at the recommended
pitches, before the 20 m fusion range gate binds. **Outline area is an upper
bound on the HSV colour patch**, not proof that the colour classifier passes
its 25-pixel region threshold.

## Calibration and bracket

Put both sensors on one stiff bracket and measure their relative pose after
installation and whenever either mounting interface is disturbed. The
fusion's 6 px matching margin at the assumed 448 px focal length corresponds
to about 0.8°, or 21 cm laterally at 15 m. The study flags roughly half a
degree of relative flex/slip as a concern; this is not a validated allowable
deflection or a complete association-error budget.

Use the actual rectified camera calibration. The sim's 110° horizontal
pinhole produces about 77.5° vertical coverage; Stereolabs lists a maximum
110° × 70° for the wide lens. The source expects the real rectified image to
be narrower. Read the SDK's intrinsics rather than treating advertised lens
coverage as calibrated image coverage.

## ZED 2i hardware

The source identifies the team's 2.1 mm lens (confirmed 10/11), with fixed
built-in circular polarizer, part `ZED2i21MMP`. Selected specifications from
the datasheet linked in the source:

| Item | Specification reported in the study |
| --- | --- |
| Focal length / aperture | 2.12 mm / f/2.0 |
| Maximum field of view | 110° H × 70° V × 120° diagonal |
| Stereo baseline | 120 mm |
| Depth range | 0.3–20 m; ideal 0.3–12 m |
| Depth accuracy | <1% to 3 m, <5% to 15 m |
| Sensors / shutter | Dual 1/3-inch CMOS, synchronized electronic rolling shutter |
| Per-eye modes | 2208 × 1242 at 15 Hz; 1920 × 1080 at 15/30 Hz; 1280 × 720 at 15/30/60 Hz; 672 × 376 at 15–100 Hz |
| Output / dynamic range | YUV 4:2:2, 8 bit / 64.6 dB |
| IMU | 400 Hz, ±8 g accelerometer, ±1000°/s gyro |
| Interface / power | USB 3.0 Type-C with screw locks / 5 V, 380 mA |
| Dimensions / mass | 175.3 × 30.3 × 43.1 mm / 229 g |
| Environment | IP66; −10 to +45°C in the source datasheet (source notes website says +50°C) |
| Mounting | 1/4-inch-20 UNC, two bottom M3, four back M3 (website) |
| Host | NVIDIA Pascal or newer; Jetson supported |

The stack models 1280 × 720 at 30 Hz. Rolling shutter can skew cones under
fast yaw. Colour-only fusion still needs alignment; it is not exempt from
exposure timing or motion blur. LiDAR remains the range source. Shade the
camera from sun and hot structure, check exposure with its fixed polarizer,
and tune HSV thresholds on footage from this actual camera under different
lighting conditions.

## If a driver is in the seat

The proposed mount is about 55 mm ahead of the main hoop's front face,
behind the likely helmet/head-restraint position. A helmet top near 1.06 m
would put a camera centred at 0.92 m at or below it. The LiDAR at 1.06 m can
also lose forward/downward coverage. Neither study's CAD mesh includes a
driver, helmet or head restraint.

Decide whether autonomous testing/events will have a seated person. If so,
place the actual helmet/head restraint or the rules' 95th-percentile template
in the assembly before accepting this location. If obstructed, study a camera
ahead of the driver near the front hoop or nose, inside the allowed envelope.
Moving above the current LiDAR would improve helmet clearance but breaches
the envelope in this layout. The viewer alone cannot settle this packaging.

## Zeroing in

1. Check the approximately 12 cm drop against the Livox FOV model, ZED STEP,
   bracket and driver/head-restraint geometry in SOLIDWORKS.
2. Design the shared stiff bracket with camera pitch adjustable 0–10°.
3. Static field test at 0.92 m with blue/yellow small cones at 5, 10, 15 and
   20 m. Measure HSV patch area against the 25 px minimum and check glare,
   polarizer response and exposure across sun angles.
4. Measure camera–LiDAR extrinsics and use the real rectified intrinsics.
5. Apply the accepted mount to `stereo_camera` in `vehicle.yaml`, regenerate
   both Gazebo vehicle models, and rerun the fusion gate. A passing LiDAR
   mount campaign does not validate this camera mount.

## Method, provenance and limits

- **Source:** [Stereo Camera Mount Study in Notion](https://app.notion.com/p/413e26707bba8146b5cac8cd5166a296),
  created/updated 2026-11-08, imported 2026-11-09. Notion remains the live
  design source; this page preserves its recommendation and reported results.
- **Artifact:** [Orion Camera Placement](https://claude.ai/artifact/ATy1MdmPQi7G36eYykjWZd),
  source study's 11/8 viewer, archived from its Notion HTML attachment on
  2026-11-09. Local rebuild matches the original bytes. No browser-based
  sweep or raw sim/field campaign was performed for this import.
- **Car geometry:** the shared `orion.stl` mesh described in the
  [LiDAR study](../2026-11-08-lidar-mount-study/README.md#method-and-provenance).
  No driver, helmet, head restraint, bracket or cables are included.
- **Optics:** ideal sim pinhole; real rectified calibration and lighting are
  unverified. Whole-cone pixel counts overestimate usable colour pixels.
- **LiDAR shadow:** field-edge geometry, not scan-table counts; assumes the
  LiDAR optical centre coincides with its housing centre.
- **Rules:** DT.2.3.a packaging conclusions are reported from the source
  study, not a fresh rules compliance approval.
- **Vendor references:** [ZED 2i datasheet/CAD/docs](https://www.stereolabs.com/zed-2i)
  and [Mid-360 CAD/FOV downloads](https://www.livoxtech.com/mid-360/downloads).
  The specification table preserves the study's values; vendor revisions
  were not independently reconciled in this import.
