"""Read the flat cone worlds supported by the standalone Mid-360 caster."""

from pathlib import Path
import xml.etree.ElementTree as ET

from lhr_trackgen.cone_geometry import CONE_SPECS
import numpy as np


def _pose(element):
    pose = element.find('pose')
    if pose is not None and pose.attrib:
        raise ValueError('Relative or quaternion SDF poses are unsupported')
    values = np.asarray(element.findtext('pose', '0 0 0 0 0 0').split(), dtype=float)
    if values.shape != (6,) or not np.isfinite(values).all():
        raise ValueError('Expected six finite SDF pose values')
    return values


def load_world_cones(path: str | Path) -> np.ndarray:
    """
    Return exact world positions and nominal cone envelopes as Nx4 values.

    Unsupported objects fail explicitly: silently dropping obstacles would
    let the cloud disagree with the world the vehicle actually drives in.
    Cone meshes are approximated by the caster's existing ideal surfaces.
    """
    world = ET.parse(path).getroot().find('world')
    if world is None:
        raise ValueError('Expected an SDF world')
    if world.find('.//frame') is not None:
        raise ValueError('Named SDF frames are unsupported')
    if world.find('actor') is not None:
        raise ValueError('Animated actors are unsupported')
    ground = world.find("model[@name='ground_plane']")
    if ground is None:
        raise ValueError('Expected a ground_plane model')
    if ground.find('model') is not None or ground.find('include') is not None:
        raise ValueError('Nested ground models are unsupported')
    if ground.find('link/collision/geometry/plane') is None:
        raise ValueError('Expected horizontal ground at z=0')
    for element in [ground, *ground.findall('link'),
                    *ground.findall('link/collision'), *ground.findall('link/visual')]:
        if np.any(_pose(element)):
            raise ValueError('Expected horizontal ground at z=0')
    for geometry in ground.findall('.//geometry'):
        plane = geometry.find('plane')
        normal = np.asarray((plane.findtext('normal', '') if plane is not None
                             else '').split(), dtype=float)
        if (len(geometry) != 1 or normal.shape != (3,)
                or not np.allclose(normal, [0, 0, 1], atol=1e-9, rtol=0)):
            raise ValueError('Only horizontal ground geometry is supported')
    if any(model is not ground for model in world.findall('model')):
        raise ValueError('Only the ground_plane inline model is supported')

    cones = []
    for include in world.findall('include'):
        uri = include.findtext('uri', '')
        if uri == 'model://fsae_vehicle':
            continue
        kind = uri.removeprefix('model://cone_')
        if not uri.startswith('model://cone_') or kind not in CONE_SPECS:
            raise ValueError(f'Unsupported caster model: {uri}')
        spec = CONE_SPECS[kind]
        x, y, z, roll, pitch, yaw = _pose(include)
        if not np.allclose([z, roll, pitch, yaw],
                           [spec.height_m / 2, 0, 0, 0], atol=1e-6, rtol=0):
            raise ValueError('Expected upright, unrotated cones resting on z=0')
        if include.find('placement_frame') is not None or include.find('scale') is not None:
            raise ValueError('SDF placement frames and scaled models are unsupported')
        cones.append([x, y, spec.height_m, spec.base_width_m])
    if not cones:
        raise ValueError('The loaded world has no supported cones')
    return np.asarray(cones)
