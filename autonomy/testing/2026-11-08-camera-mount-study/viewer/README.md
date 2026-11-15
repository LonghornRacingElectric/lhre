# Camera-placement viewer

Working copy of the Orion Camera Placement HTML from the attachment on the
[Notion study](https://app.notion.com/p/413e26707bba8146b5cac8cd5166a296),
archived 2026-11-09. The two embedded mesh arrays are replaced by
placeholders in `template.html`. `build.py` restores them from the shared
Orion STL using the same millimetre quantization and packing as the
[LiDAR viewer](../../2026-11-08-lidar-mount-study/README.md#viewer).
On 2026-11-14 the presets and initial view changed to study above-LiDAR
placement: (0.625, 0, 1.125) m in `base_link`, 5° down, above the fixed
LiDAR height of 1.060 m. The earlier LiDAR-lowering proposal is withdrawn. This housing-geometry candidate is not an accepted
mount; see the study for its calculated clearances and assumptions. The baseline
preset retains the below-LiDAR sim pose. Geometry/optics calculations are
unchanged; no driver or LiDAR/bracket collision model has been added.
The original template is preserved in git commit `5257aaa0`; current builds
no longer match the source attachment hash below.

From this directory, with Python 3 and NumPy (available in the autonomy
development container):

```bash
python3 build.py ../../../ros2/src/lhr_vehicle/meshes/orion.stl \
    template.html orion-camera-placement.html
```

Open the generated HTML in a browser. The mesh is embedded; Three.js
0.160.0, its OrbitControls, three-mesh-bvh 0.7.3 and Google Fonts load over
the internet. The generated HTML is ignored by git. The source attachment
is 866,479 bytes, SHA-256
`f6f1d868343ddb5985d9a9f5f30609107ac7975459833fe727157eb352c08931`.

The [study](../README.md) records the geometry assumptions, recommendation
and limitations. In particular, the mesh has no driver, and pixel estimates
are whole-cone outlines, not measured HSV patches or on-car acceptance.
