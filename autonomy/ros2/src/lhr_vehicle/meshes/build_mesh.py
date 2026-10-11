#!/usr/bin/env python3
r"""
Build ``orion.stl`` from the two SOLIDWORKS STL exports.

Not part of the ROS package and not installed by ``setup.py``. It lives
here because a reduced mesh whose reduction nobody can reproduce is a
mesh nobody can correct, and the numbers in ``README.md`` come from this
script's output.

Needs numpy, scipy and open3d, none of which the ROS packages depend on:

    uv run --with numpy --with scipy --with open3d python build_mesh.py \\
        Orion_full_car_mm.STL Orion_aero_model_tires_mm.STL orion.stl

The full-car export is 10.9M triangles and 543 MB, which is not something
a viewer should fetch over a websocket. Three steps get it to a usable
size, and the order matters.

1. Coincident duplicate triangles are dropped. Parts of the assembly are
   exported twice in the same place, which leaves every edge of those
   surfaces shared by four faces instead of two. On the worst body every
   one of its 195,663 edges is non-manifold, and no quadric decimator
   will collapse through a non-manifold edge.
2. Solid bodies under ``--min-body`` across are dropped whole. They are
   fasteners, inserts and fittings: 3.6M of the 10.3M triangles sit in
   bodies under 40 mm, and together those are 2% of the car's surface
   area.
3. What is left is reduced by vertex clustering, which snaps vertices to
   a grid and averages each cell.

Step 3 is deliberately not quadric edge collapse, which is the usual
choice and is wrong here. On this mesh quadric decimation both stalls,
refusing to go below roughly 250k triangles however it is asked, and
places new vertices off the surface where parts touch: it put a vertex
73 mm above the roll hoop and reached a worst-case error of 311 mm by
deleting whole regions. Vertex clustering has error bounded by the grid
instead, which is what makes the result safe to measure the car with.

The four wheels come from the second export and are appended at full
resolution. They are only 1683 triangles, the full-car assembly
suppresses all four mounted-tyre instances so nothing else supplies
them, and ``test/test_mesh.py`` identifies them by their exact bounding
boxes.
"""

import argparse
import struct

import numpy as np
import open3d as o3d
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def read_stl(path):
    """Return an (n, 3, 3) array of triangle vertices from a binary STL."""
    raw = np.memmap(path, dtype=np.uint8, mode='r')
    count = int(np.frombuffer(raw[80:84].tobytes(), dtype='<u4')[0])
    if raw.size != 84 + 50 * count:
        raise SystemExit(f'{path}: not a binary STL of {count} triangles')
    return np.frombuffer(raw[84:84 + 50 * count].reshape(count, 50)[:, 12:48]
                         .copy().tobytes(), dtype='<f4').reshape(count, 3, 3)


def write_stl(path, tri, header):
    """Write triangles as a binary STL, recomputing the facet normals."""
    count = len(tri)
    out = bytearray(84 + 50 * count)
    out[:len(header)] = header
    out[80:84] = struct.pack('<I', count)
    rec = np.zeros((count, 50), dtype=np.uint8)
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    unit = np.where(length > 0, normal / np.where(length == 0, 1, length), 0.0)
    rec[:, 0:12] = unit.astype('<f4').view(np.uint8).reshape(count, 12)
    rec[:, 12:48] = tri.astype('<f4').view(np.uint8).reshape(count, 36)
    out[84:] = rec.tobytes()
    with open(path, 'wb') as handle:
        handle.write(bytes(out))


def weld(tri):
    """Index the triangle soup, dropping degenerate and duplicate faces."""
    flat = np.ascontiguousarray(tri.reshape(-1, 3))
    uniq, inv = np.unique(flat.view([('v', 'V12')]).ravel(),
                          return_inverse=True)
    pts = uniq.view('<f4').reshape(-1, 3).astype(np.float64)
    faces = inv.reshape(-1, 3)
    good = ((faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2])
            & (faces[:, 0] != faces[:, 2]))
    faces = faces[good]
    # A triangle is the same triangle whatever its winding or start vertex.
    key = np.ascontiguousarray(
        np.sort(faces, axis=1).astype(np.int64)).view([('k', 'V24')]).ravel()
    _, firsts = np.unique(key, return_index=True)
    return pts, faces[np.sort(firsts)]


def body_spans(pts, faces):
    """Return each connected body's bounding-box diagonal and area."""
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    count, label = connected_components(
        coo_matrix((np.ones(len(rows), np.int8), (rows, cols)),
                   shape=(len(pts), len(pts))), directed=False)
    span = np.zeros(count)
    for axis in range(3):
        hi = np.full(count, -np.inf)
        lo = np.full(count, np.inf)
        np.maximum.at(hi, label, pts[:, axis])
        np.minimum.at(lo, label, pts[:, axis])
        span += (hi - lo) ** 2
    area = np.linalg.norm(np.cross(pts[faces[:, 1]] - pts[faces[:, 0]],
                                   pts[faces[:, 2]] - pts[faces[:, 0]]),
                          axis=1) / 2.0
    per_body = np.bincount(label[faces[:, 0]], weights=area, minlength=count)
    return label, np.sqrt(span), per_body


def main():
    """Reduce the full-car export, add the wheels, and write the mesh."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('full_car', help='full-car assembly STL, millimetres')
    ap.add_argument('tyres', help='four-wheel STL, millimetres')
    ap.add_argument('out', help='mesh to write, metres')
    ap.add_argument('--min-body', type=float, default=40.0,
                    help='drop solid bodies smaller than this, mm')
    ap.add_argument('--voxel', type=float, default=20.0,
                    help='vertex clustering grid, mm')
    args = ap.parse_args()

    pts, faces = weld(read_stl(args.full_car))
    print(f'cleaned   {len(faces)} triangles, {len(pts)} vertices')

    label, span, area = body_spans(pts, faces)
    visible = span >= args.min_body
    faces = faces[visible[label[faces[:, 0]]]]
    print(f'bodies    {len(span)} total, {int(visible.sum())} at least '
          f'{args.min_body:.0f} mm across, keeping {len(faces)} triangles '
          f'and {100 * area[visible].sum() / area.sum():.1f}% of the '
          f'surface area')

    mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(pts),
        o3d.utility.Vector3iVector(faces.astype(np.int32)))
    mesh.remove_unreferenced_vertices()
    mesh = mesh.simplify_vertex_clustering(
        voxel_size=args.voxel,
        contraction=o3d.geometry.SimplificationContraction.Average)
    body = np.asarray(mesh.vertices)[np.asarray(mesh.triangles)] / 1000.0
    print(f'reduced   {len(body)} triangles on a {args.voxel:.0f} mm grid')

    wheels = read_stl(args.tyres).astype(np.float64) / 1000.0
    tri = np.concatenate([body, wheels])
    lo = tri.reshape(-1, 3).min(axis=0)
    hi = tri.reshape(-1, 3).max(axis=0)
    print(f'combined  {len(tri)} triangles, {len(wheels)} of them wheels, '
          f'{84 + 50 * len(tri)} bytes')
    print(f'bbox      {np.round(lo, 4)} to {np.round(hi, 4)} m')
    write_stl(args.out, tri,
              b'Orion full car: SOLIDWORKS STL export, reduced, metres')
    print(f'wrote     {args.out}')


if __name__ == '__main__':
    main()
