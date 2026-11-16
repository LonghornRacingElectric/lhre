"""Launch-local native IMU overrides without modifying installed models."""

import math
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from lhr_gazebo.stereo_camera import stereo_model_resources


def imu_model_resources(models_dir, *, stereo=False, noise_enabled=True,
                        gyro_bias=0.0) -> TemporaryDirectory:
    """Overlay a vehicle model; gyro_bias is an additional signed z-rate offset."""
    if not math.isfinite(gyro_bias):
        raise ValueError('gyro_bias must be finite')
    source = Path(models_dir).resolve() / 'fsae_vehicle'
    tree = ET.parse(source / ('model_stereo.sdf' if stereo else 'model.sdf'))
    imu = tree.find(".//sensor[@type='imu']/imu")
    if imu is None or len(imu.findall('.//noise')) != 6:
        raise ValueError('Vehicle model lacks native IMU noise; regenerate and rebuild')
    if not noise_enabled:
        for noise in imu.findall('.//noise'):
            for value in noise:
                value.text = '0'
    # Gaussian mean is signed; Gazebo's startup bias sampling randomizes sign.
    imu.find('angular_velocity/z/noise/mean').text = format(gyro_bias, '.17g')
    resources = (stereo_model_resources(models_dir) if stereo
                 else TemporaryDirectory(prefix='lhr-imu-'))
    try:
        model = Path(resources.name) / 'fsae_vehicle'
        if stereo:
            # Replace the overlay's symlink, never write through it to the installed SDF.
            (model / 'model.sdf').unlink()
        else:
            model.mkdir()
            for name in ('model.config', 'meshes'):
                (model / name).symlink_to(source / name, target_is_directory=name == 'meshes')
        tree.write(model / 'model.sdf', encoding='unicode')
    except Exception:
        resources.cleanup()
        raise
    return resources
