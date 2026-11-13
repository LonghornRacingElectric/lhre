"""Rigid stereo frames and opt-in ROS actions for the simulated ZED 2i."""

import math
from pathlib import Path
from tempfile import TemporaryDirectory

from lhr_vehicle import StereoCamera


def stereo_model_resources(models_dir) -> TemporaryDirectory:
    """Overlay the stereo variant under the world's existing vehicle URI."""
    source = Path(models_dir).resolve() / 'fsae_vehicle'
    if not (source / 'model_stereo.sdf').is_file():
        raise FileNotFoundError('Missing model_stereo.sdf; regenerate the models and rebuild')
    resources = TemporaryDirectory(prefix='lhr-stereo-')
    model = Path(resources.name) / 'fsae_vehicle'
    model.mkdir()
    for name, target in (('model.config', 'model.config'),
                         ('model.sdf', 'model_stereo.sdf'), ('meshes', 'meshes')):
        (model / name).symlink_to(source / target, target_is_directory=name == 'meshes')
    return resources


def camera_transforms(camera: StereoCamera) -> list[dict]:
    """Describe rig and optical transforms from the same geometry as the SDF."""
    transforms = [{
        'parent': 'base_link', 'child': 'zed2i_link',
        'translation': camera.position_m, 'rpy': camera.rpy_rad,
    }]
    for side, sign in (('left', 1.0), ('right', -1.0)):
        frame = f'zed2i_{side}_camera_frame'
        transforms.extend([
            {'parent': 'zed2i_link', 'child': frame,
             'translation': (0.0, sign * camera.baseline_m / 2.0, 0.0),
             'rpy': (0.0, 0.0, 0.0)},
            {'parent': frame, 'child': f'zed2i_{side}_camera_optical_frame',
             'translation': (0.0, 0.0, 0.0),
             'rpy': (-math.pi / 2.0, 0.0, -math.pi / 2.0)},
        ])
    return transforms


def stereo_actions(camera: StereoCamera, config_dir: str) -> list:
    """Bridge images/calibration and publish the five static rig transforms."""
    # Import ROS only for launch; geometry checks also run without a ROS install.
    from launch_ros.actions import Node

    actions = [Node(
        package='ros_gz_bridge', executable='parameter_bridge',
        name='zed2i_bridge', parameters=[{
            'config_file': str(Path(config_dir) / 'ros_gz_stereo_bridge.yaml'),
            'use_sim_time': True,
        }], output='screen',
    )]
    for transform in camera_transforms(camera):
        x, y, z = transform['translation']
        roll, pitch, yaw = transform['rpy']
        actions.append(Node(
            package='tf2_ros', executable='static_transform_publisher',
            name=transform['child'] + '_tf',
            arguments=[
                '--x', str(x), '--y', str(y), '--z', str(z),
                '--roll', str(roll), '--pitch', str(pitch), '--yaw', str(yaw),
                '--frame-id', transform['parent'],
                '--child-frame-id', transform['child'],
            ], parameters=[{'use_sim_time': True}], output='screen',
        ))
    return actions
