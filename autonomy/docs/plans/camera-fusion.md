# Phase 3 — Camera Fusion for Cone Classification

## Goal

Add one ZED 2i stereo camera with the 2.1 mm wide lens to the Gazebo model and fuse its output with the existing LiDAR pipeline to classify cones as left (blue) or right (yellow). Use color labels to strengthen Delaunay-based boundary pairing rather than replace it.

The camera unit and lens were confirmed on 2026-10-11. The first milestone is
stereo images and calibration in ROS/RViz; its design and implementation scope are in
[ZED 2i Stereo Simulation](zed2i-stereo.md). The stereo input interface is implemented; runtime validation is recorded
in the [Gazebo README](../../ros2/src/lhr_gazebo/README.md#stereo-validation).
Camera-only HSV region classification is implemented as an opt-in debug view;
see [classifier usage](../../ros2/src/lhr_perception/README.md#camera-color-classification).
Celina has visually exercised classification in VNC. The next milestone is a
separate colored LiDAR map for inspection; its approach was approved on
2026-10-12. The [detailed fusion design](camera-lidar-fusion-design.md) awaits
review before implementation. Track-builder integration remains a later step.

## Background

LiDAR gives accurate 3D positions but no color. The Delaunay track builder filters edges by track-width distance, but on tight autocross corners same-side cones can be ~3.5m apart, producing false cross-track pairs and bad midpoints. Knowing cone color eliminates these false pairs.

## Approach: Color-Augmented Delaunay

Keep Delaunay triangulation as the core pairing strategy. Add cone color as an additional edge filter:

1. LiDAR detects cone positions (existing pipeline, unchanged)
2. Camera classifies each cone as blue / yellow / unknown
3. Fusion step assigns a color label to each cone in the map
4. Delaunay triangulation (existing)
5. Filter edges by track-width band (existing)
6. **New**: reject edges where both endpoints share the same color
7. Unclassified cones pass through the existing distance-only filter

Color is purely additive — if the camera misses a cone, behavior falls back to current Delaunay-only filtering.

## Simulation Shortcut

In Gazebo, cones are rendered with distinct colors. We can use simple color segmentation (or ground-truth object labels from Gazebo) instead of a trained YOLO model. A real YOLO model is only needed for the physical car.

## How Other Teams Do It

Most FSAE driverless teams use LiDAR-camera fusion:

- YOLO (v5/v8) detects cones in 2D and classifies by color in one pass
- LiDAR points are projected into the camera frame using extrinsic calibration
- Points inside a bounding box inherit that box's color label
- Teams train on the FSOCO dataset plus custom data
- IIT Bombay Racing (2024) uses a three-tier fallback: LiDAR-camera fusion primary, monocular depth secondary, stereo keypoint tertiary

For our sim pipeline, the equivalent is: render camera image, segment by color, project LiDAR cone positions into the image, assign the color of the pixel/region at that projection.

## Implementation Tasks

### 3.1 — Add camera sensor to Gazebo model
- Implemented as the ZED 2i stereo pair; the left input is
  `/lhr/sensor/zed2i/left/image_rect`, with a corresponding right stream.
- Stereo validation is recorded in the Gazebo README linked above.

### 3.2 — Cone color classifier (sim)
- Implemented: subscribe to the left camera image and segment blue/yellow/orange
  regions using configurable HSV bounds and minimum connected pixel area.
- Publish a labeled debug image, retaining input timestamps and frames.
- These are color candidates, not cone-object detections. The selected fusion
  approach reuses segmentation on raw images rather than introducing a new
  region transport interface. Physical-camera accuracy remains unvalidated.

### 3.3 — LiDAR-camera fusion node
- Implement the reviewed detailed design linked above: timestamp-aligned
  projection, conservative region matching, and correctable per-ID votes.
- Publish blue/yellow/orange/unknown markers on a separate RViz topic,
  preserving LiDAR positions and IDs. No planning integration in this step.

### 3.4 — Update track builder to use color
- The boundary strategy already has a same-known-color edge filter and keeps
  unknown cones in geometry-based pairing. Future work connects the validated
  fused map to that input and decides how orange cones should affect planning.

### 3.5 — Validate on autocross track
- Run full pipeline on an autocross layout
- Compare centerline quality vs current Delaunay-only approach
- Verify that unclassified cones degrade gracefully

## Dependencies

- Phase 2 LiDAR pipeline must be functional (it is on oval)
- Vehicle model must support camera mount point
- Gazebo bridge must support image transport

## References

- [IIT Bombay Racing Driverless Stack (2024)](https://arxiv.org/html/2408.06113v1)
- [YOLOv8 + ZED2 Cone Clustering](https://www.mdpi.com/2673-4591/79/1/96)
- [FSOCO Dataset](https://ddavid.github.io/fsoco/)
