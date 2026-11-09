# Camera-placement viewer

Original Orion Camera Placement HTML from the attachment on the
[Notion study](https://app.notion.com/p/413e26707bba8146b5cac8cd5166a296),
archived 2026-11-09. Only the two embedded mesh arrays have been replaced by
placeholders in `template.html`. `build.py` restores them from the shared
Orion STL using the same millimetre quantization and packing as the
[LiDAR viewer](../../2026-11-08-lidar-mount-study/README.md#viewer).
The viewer logic, controls and optics are unchanged.

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
