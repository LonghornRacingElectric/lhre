"""Pack Orion's mesh into the original camera-placement viewer template."""

import base64
from pathlib import Path
import struct
import sys

import numpy as np


stl, template, out = (Path(p) for p in sys.argv[1:4])
data = stl.read_bytes()
n = struct.unpack('<I', data[80:84])[0]
tri = np.frombuffer(data[84:84 + n * 50], dtype=np.dtype([
    ('n', '<3f4'), ('v', '<9f4'), ('a', '<u2')]))['v']
mm = np.round(tri.reshape(-1, 3) * 1000).astype(np.int32)
verts, index = np.unique(mm, axis=0, return_inverse=True)
assert len(verts) < 65536 and np.abs(verts).max() < 32767
faces = index.reshape(-1, 3)
faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2])
              & (faces[:, 0] != faces[:, 2])]

page = template.read_text(encoding='utf-8')
for key, array in (('__V64__', verts.astype('<i2')), ('__I64__', faces.astype('<u2'))):
    assert page.count(key) == 1, f'Expected one {key} placeholder'
    page = page.replace(key, base64.b64encode(array.tobytes()).decode())
out.write_text(page, encoding='utf-8', newline='\n')
print(f'{len(verts)} vertices, {len(faces)} faces, page {len(page.encode())} bytes')
