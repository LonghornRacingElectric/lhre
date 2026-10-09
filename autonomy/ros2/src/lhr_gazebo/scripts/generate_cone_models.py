#!/usr/bin/env python3
"""Generate static collision cones and stripe visuals from the shared profile."""

import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lhr_trackgen'))
from lhr_trackgen.cone_geometry import CONE_SPECS, cone_triangles  # noqa: E402


MODELS = Path(__file__).resolve().parents[1] / 'models'


def stl(faces):
    """Render deterministic ASCII STL with outward surface normals."""
    lines = ['solid cone']
    for face in faces:
        a, b, c = face
        u, v = ([end[i] - a[i] for i in range(3)] for end in (b, c))
        normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                  u[0] * v[1] - u[1] * v[0])
        norm = math.sqrt(sum(value * value for value in normal))
        line = 'facet normal ' + ' '.join(f'{value / norm:.9f}' for value in normal)
        lines.extend([line, 'outer loop'])
        lines.extend('vertex ' + ' '.join(f'{v:.9f}' for v in point) for point in face)
        lines.extend(['endloop', 'endfacet'])
    return '\n'.join(lines + ['endsolid cone', ''])


def render(kind):
    """Return all generated mesh and SDF contents for a cone type."""
    spec = CONE_SPECS[kind]
    model = 'cone_' + kind
    faces, colors = cone_triangles(spec)
    body = [face for face, color in zip(faces, colors) if color == spec.color]
    stripe = [face for face, color in zip(faces, colors) if color == spec.stripe_color]
    visuals = []
    for name, color in [('body', spec.color), ('stripe', spec.stripe_color)]:
        rgb = ' '.join(str(v) for v in color) + ' 1'
        visuals.append(f"""      <visual name="{name}">
        <pose>0 0 {-spec.height_m / 2} 0 0 0</pose>
        <geometry><mesh><uri>model://{model}/meshes/{name}.stl</uri></mesh></geometry>
        <material><ambient>{rgb}</ambient><diffuse>{rgb}</diffuse></material>
      </visual>""")
    sdf = f"""<?xml version="1.0" ?>
<sdf version="1.9">
  <model name="{model}">
    <static>true</static>
    <link name="link">
      <collision name="collision">
        <pose>0 0 {-spec.height_m / 2} 0 0 0</pose>
        <geometry><mesh><uri>model://{model}/meshes/cone.stl</uri></mesh></geometry>
      </collision>
{chr(10).join(visuals)}
    </link>
  </model>
</sdf>
"""
    return {'model.sdf': sdf, 'meshes/cone.stl': stl(faces),
            'meshes/body.stl': stl(body), 'meshes/stripe.stl': stl(stripe)}


def main():
    """Write all four nominal competition cone assets."""
    for kind in CONE_SPECS:
        for path, content in render(kind).items():
            target = MODELS / ('cone_' + kind) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)


if __name__ == '__main__':
    main()
