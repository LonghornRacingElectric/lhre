"""Keep both committed vehicle models in sync with the generator and vehicle.yaml."""

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml

PKG_DIR = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location(
        'generate_vehicle_model',
        PKG_DIR / 'scripts' / 'generate_vehicle_model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('stereo, filename', [
    (False, 'model.sdf'), (True, 'model_stereo.sdf'),
])
def test_model_sdf_is_in_sync_with_vehicle_yaml(stereo, filename):
    gen = _generator()
    rendered = gen.render(gen.load_source_vehicle(), enable_stereo_camera=stereo)
    committed = (gen.OUTPUT.parent / filename).read_text(encoding='utf-8')
    assert committed == rendered, (
        f'models/fsae_vehicle/{filename} is out of date: '
        'run scripts/generate_vehicle_model.py and commit the result')


@pytest.mark.parametrize('filename', ['model.sdf', 'model_stereo.sdf'])
def test_model_sdf_uses_lf_line_endings(filename):
    gen = _generator()
    assert b'\r' not in (gen.OUTPUT.parent / filename).read_bytes()


@pytest.mark.parametrize('filename', ['model.sdf', 'model_stereo.sdf'])
def test_check_rejects_either_stale_model(filename, tmp_path, monkeypatch):
    """Fail the CLI check even when only the opt-in variant is stale."""
    gen = _generator()
    monkeypatch.setattr(gen, 'SRC_DIR', tmp_path / 'src')
    monkeypatch.setattr(gen, 'OUTPUT', tmp_path / 'model.sdf')
    monkeypatch.setattr(gen, 'STEREO_OUTPUT', tmp_path / 'model_stereo.sdf')
    assert gen.main([]) == 0
    assert gen.main(['--check']) == 0
    (tmp_path / filename).write_text('stale', encoding='utf-8')
    assert gen.main(['--check']) == 1


def test_default_model_has_no_camera_rendering_sensors():
    """Keep LiDAR-only runs free of the stereo rendering load (#123)."""
    gen = _generator()
    root = ET.fromstring(gen.render(gen.load_source_vehicle()))
    assert root.findall(".//sensor[@type='camera']") == []
    assert root.find("./model/link[@name='zed2i_link']") is None
    assert root.find("./model/joint[@name='zed2i_mount']") is None
    assert root.find(".//sensor[@type='gpu_lidar']") is not None
    assert root.find(".//sensor[@type='imu']") is not None


def test_steering_joint_limit_admits_the_inner_ackermann_angle():
    gen = _generator()
    veh = gen.load_source_vehicle()
    assert gen.inner_steer_limit(veh) > veh.max_steer_rad


def test_stereo_pair_has_shared_rectified_optics():
    """The rendered pair must have physical separation and usable calibration."""
    gen = _generator()
    root = ET.fromstring(gen.render(gen.load_source_vehicle(), enable_stereo_camera=True))
    rig = root.find("./model/link[@name='zed2i_link']")
    assert rig is not None
    sensors = rig.findall("sensor[@type='camera']")
    assert len(sensors) == 2
    for sensor, side, y in zip(sensors, ('left', 'right'), (0.06, -0.06)):
        assert [float(v) for v in sensor.findtext('pose').split()] == [0, y, 0, 0, 0, 0]
        assert sensor.findtext('topic') == f'/zed2i/{side}/image_rect'
        assert float(sensor.findtext('update_rate')) == 30
        camera = sensor.find('camera')
        assert camera.findtext('camera_info_topic') == f'/zed2i/{side}/camera_info'
        assert camera.findtext('optical_frame_id') == f'zed2i_{side}_camera_optical_frame'
        assert camera.findtext('image/format') == 'R8G8B8'
        assert camera.findtext('image/width') == '1280'
        assert camera.findtext('image/height') == '720'
        assert float(camera.findtext('horizontal_fov')) == pytest.approx(math.radians(110))
        assert float(camera.findtext('lens/intrinsics/fx')) == pytest.approx(448.132824)
        assert float(camera.findtext('lens/intrinsics/fy')) == pytest.approx(448.132824)
        assert float(camera.findtext('lens/intrinsics/cx')) == 640
        assert float(camera.findtext('lens/intrinsics/cy')) == 360
        assert camera.find('distortion') is None


def test_mount_rotation_preserves_local_baseline():
    """Camera baseline must stay in rig y rather than the rotated chassis y."""
    gen = _generator()
    veh = gen.load_source_vehicle()
    camera = replace(veh.stereo_camera, position_m=(1.0, 0.2, 0.8),
                     rpy_rad=(0.1, 0.2, 0.3), baseline_m=0.2)
    root = ET.fromstring(gen.render(
        replace(veh, stereo_camera=camera), enable_stereo_camera=True))
    rig = root.find("./model/link[@name='zed2i_link']")
    assert rig is not None
    pose = rig.find('pose')
    assert pose.attrib['relative_to'] == 'chassis'
    assert [float(v) for v in pose.text.split()] == pytest.approx(
        [1.0 - veh.chassis_center_x_m, 0.2, 0.8 - veh.chassis_z_m, 0.1, 0.2, 0.3])
    ys = [float(s.findtext('pose').split()[1]) for s in rig.findall('sensor')]
    assert ys == pytest.approx([0.1, -0.1])
    joint = root.find("./model/joint[@name='zed2i_mount']")
    assert joint.attrib['type'] == 'fixed'
    assert joint.findtext('parent') == 'chassis'
    assert joint.findtext('child') == 'zed2i_link'


def test_odometry_tf_publisher_matches_bridge():
    """Vehicle map TF must reach the same topic consumed by the ROS bridge."""
    gen = _generator()
    root = ET.fromstring(gen.render(gen.load_source_vehicle()))
    plugin = root.find("./model/plugin[@name='gz::sim::systems::OdometryPublisher']")
    mappings = yaml.safe_load((PKG_DIR / 'config' / 'ros_gz_bridge.yaml').read_text())
    bridge = next(m for m in mappings if m['ros_topic_name'] == '/tf')
    published = plugin.findtext('tf_topic', default='/model/fsae_vehicle/pose')
    assert published == bridge['gz_topic_name']
