# meshes

`orion.stl` is Orion's own geometry, not a stand-in. It is what
`vehicle_viz` draws on `/lhr/vehicle/body`, and it replaced the `ASSUMED`
chassis box that used to stand for the body.

Binary STL, 84,101 triangles, 4.2 MB, sha256 `6e700c0cbbb6`. Units are
**metres** already, so the marker scale is 1. It spans 2.8713 x 1.4154 x
1.1183 m.

`build_mesh.py` builds it from the two CAD exports and is the only thing
that should. Re-run it rather than editing the mesh.

## Frame

The CAD origin is the **front axle centreline at ground level**, x
forward, y left, z up. That is REP-105's orientation with a different
origin, so placing it in `base_link` (rear axle at ground) is one
translation:

```
base_link_x = cad_x + wheelbase_m
```

No rotation, no sign flip, no scaling. `vehicle_viz` reads the wheelbase
from `config/vehicle.yaml` rather than repeating 1.5494 here.

This was not taken on trust. The wheel export resolves into four
separable bodies, each a 0.4064 x 0.2032 x 0.4064 m box, and the front
pair sits at x = 0.000, which is what fixes the origin at the front axle.
Their centres give a wheelbase of 1.549400 m and a track of 1.212222 m
against `vehicle.yaml`'s 1.5494 and 1.2122. The two exports are separate
CAD documents, and they agree: the assembly's roll hoop tops out at
1.11504 m, which is 1.115 m measured independently from the rear-frame
STEP export through a different toolchain.

One number disagrees and is worth knowing: the CAD wheel is 0.4064 m
outside diameter (16.0 in exactly), so a 0.2032 m radius, against
`wheel_radius_m: 0.2045`. 1.3 mm, most likely unloaded against effective
rolling radius. The wheel's axle also sits at z = 0.1999 rather than
0.2032, so the modelled tyre dips 3.3 mm below the ground plane.

Those wheels are a simplified analysis body, so do not read
`wheel_width_m` off them. Their 0.2032 m width is 8.00 in exactly, as the
diameter is 16.00 in exactly, which is a round placeholder rather than a
measured section width. `vehicle.yaml` keeps `wheel_width_m` as `ASSUMED`
for that reason.

## Where it came from

Two binary STL exports out of SOLIDWORKS, from the SVN working copy at
revision 11436 (checked against r11440 for newer 2026 season files):

| | source | last changed | sha256 |
| --- | --- | --- | --- |
| body | `_26-000.SLDASM` | r9480 | `d2e29db10a22` |
| wheels | `26-AER/26-AER-1000 (Analysis)/26-AER-1000 (Full Car CAD)/26-AER-1000-03 (Tires).SLDPRT` | r9936 | `f44a776b874f` |

Both were opened read-only, exported with no recentering or scaling, and
the source CAD was not saved. Nothing went to a cloud converter.

The exports themselves are not committed: the body one alone is 543 MB.
Re-exporting them needs SOLIDWORKS and the SVN working copy, so the
recipe above is the reproduction path, and the sha256 of each is recorded
so a copy that turns up later can be checked rather than assumed.

The wheels are a second file because **the saved full-car configuration
suppresses all four instances of the detailed mounted-tyre part**. The
assembly export contains no wheel-shaped body at all, which is checkable
rather than taken on faith: no connected body in it has the square x/z,
thin-y bounding box a wheel has. So the four analysis wheels are appended
at full resolution, and they are the only wheels in the mesh.

### Reduction

The body export is 10.9M triangles and 543 MB, which is not something a
viewer should fetch over a websocket. `build_mesh.py` drops coincident
duplicate triangles, drops solid bodies under 40 mm across (fasteners and
fittings: 3.6M of 10.3M triangles, 2% of the surface area), and reduces
the rest by vertex clustering on a 20 mm grid. 5500 bodies become 46, and
10.9M triangles become 84,101.

Measured against the full original, sampling both ways:

- **Committed to original: p99 7.4 mm, worst 13.7 mm.** Nothing in the
  mesh sits further than 13.7 mm outside the real surface, which is the
  direction that matters for measuring clearances with it.
- **Original to committed: p99 8.5 mm, worst 166 mm.** 99.6% of the real
  surface is within 10 mm of the mesh. The tail is the dropped small
  bodies, which have no counterpart at all.

It is reduced by clustering rather than by quadric edge collapse, which
is the usual choice and is wrong here. Quadric decimation on this mesh
both stalls, refusing to go below roughly 250k triangles however it is
asked, and places new vertices off the surface where parts touch: it put
a vertex 73 mm above the roll hoop and reached a worst case of 311 mm by
deleting whole regions. Clustering's error is bounded by the grid, which
is what makes the result safe to measure the car with.

## What it is not

**It is visualization geometry.** It is not a B-Rep reconstruction, it
carries no materials or assembly hierarchy, and it has not been validated
as collision geometry.

**It is quantized to 20 mm.** Features smaller than that survive only
approximately, and 0.1% of the original surface has no counterpart
within 20 mm. Do not measure anything off it that a 20 mm error would
change, and prefer the source exports for anything that matters.

**Assembly references were not fully validated.** The full-car document
loaded with a missing-file error flag and warnings for sharing violation,
rebuild needed, and base part not loaded. Four unsuppressed components
had saved paths pointing at another workstation; the corresponding files
exist locally, but not every reference was repaired or checked. A
successful export and a recognizable car do not prove every component
resolved, and SOLIDWORKS' own hidden-component report cannot prove it
either, because it reports lightweight components as hidden.

**It is not an answer for lidar occlusion.** It is good enough to have
caught a sensor mount buried inside the nose, and the bound it gives for
that agrees with the previous, independently produced mesh to 0.4 mm
(0.5965 m against 0.5961 m). It is not good enough to conclude that a
beam gets through a gap.

## Replacing it

A **STEP** export of the full-car assembly into `_fab/` still supersedes
all of this: real surfaces rather than a tessellation, no suppressed
wheels to paste back in, and no 20 mm grid. The useful ask of the chassis
team is a STEP export of the configuration with the mounted-tyre
instances unsuppressed and the stale references repaired.
