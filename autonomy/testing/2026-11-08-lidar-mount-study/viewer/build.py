"""
Pack orion.stl and the Mid-360 scan-table density into the viewer page.

    cd autonomy/testing/2026-11-08-lidar-mount-study/viewer
    python3 build.py ../../../ros2/src/lhr_vehicle/meshes/orion.stl \
        ../../../ros2/src/lhr_lidar_sim/lhr_lidar_sim/patterns/mid360.csv.gz \
        template.html roll-hoop-lidar.html

The mesh goes in as int16 millimetre vertices and uint16 indices, the
density as float32 beams per square degree per frame in 0.2 degree bins.
The output is one self-contained page, about 0.9 MB.
"""
import base64
import gzip
import struct
import sys
from pathlib import Path

import numpy as np

stl, scan, template, out = (Path(p) for p in sys.argv[1:5])
data = stl.read_bytes()
n = struct.unpack('<I', data[80:84])[0]
tri = np.frombuffer(data[84:84 + n * 50], dtype=np.dtype([('n', '<3f4'), ('v', '<9f4'), ('a', '<u2')]))['v']
mm = np.round(tri.reshape(-1, 3) * 1000).astype(np.int32)
verts, index = np.unique(mm, axis=0, return_inverse=True)
assert len(verts) < 65536 and np.abs(verts).max() < 32767
faces = index.reshape(-1, 3)
faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]

# Livox's Mid-360 scan table: 800,000 beams = 40 frames of 20,000. Elevation = 90 - zenith.
rows = np.loadtxt(gzip.open(scan, 'rt'), delimiter=',', skiprows=1)
el = 90.0 - rows[:, 2]
lo, step = -7.5, 0.2
edges = np.arange(lo, 52.5 + step / 2, step)
counts, _ = np.histogram(el, edges)
dens = (counts / (len(rows) / 20000.0) / (360.0 * step)).astype('<f4')   # beams per sq.deg per frame

b64 = lambda a: base64.b64encode(a.tobytes()).decode()
page = template.read_text(encoding='utf-8')
for key, val in (('__V64__', b64(verts.astype('<i2'))), ('__I64__', b64(faces.astype('<u2'))), ('__D64__', b64(dens)),
                 ('__DLO__', repr(lo)), ('__DBIN__', repr(step)), ('__ELMIN__', f'{el.min():.4f}'), ('__ELMAX__', f'{el.max():.4f}')):
    page = page.replace(key, val)
out.write_text(page, encoding='utf-8', newline='\n')
print(f'{len(verts)} vertices, {len(faces)} faces, {len(dens)} density bins (max {dens.max():.2f}), page {len(page) / 1e6:.2f} MB')
