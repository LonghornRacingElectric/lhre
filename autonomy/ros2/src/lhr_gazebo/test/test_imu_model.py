"""Native IMU configuration, signed faults, and launch-to-sensor routing."""

from pathlib import Path
import xml.etree.ElementTree as ET

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, ExecuteProcess, RegisterEventHandler
from launch.events import Shutdown
from launch.utilities import perform_substitutions
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_gazebo.imu_model import imu_model_resources
import pytest

from test_launch_param_types import _launch
from test_vehicle_model import _generator


@pytest.mark.parametrize('stereo', [False, True])
def test_all_six_axes_use_datasheet_noise_without_rounding_to_zero(stereo):
    gen = _generator()
    veh = gen.load_source_vehicle()
    root = ET.fromstring(gen.render(veh, enable_stereo_camera=stereo))
    sensor = root.find(".//sensor[@type='imu']")
    assert float(sensor.findtext('update_rate')) == veh.imu.update_rate_hz
    for channel, sigma, bias in (
        ('angular_velocity', veh.imu.gyro_stddev_rad_s, veh.imu.gyro_bias_stddev_rad_s),
        ('linear_acceleration', veh.imu.accel_stddev_m_s2, veh.imu.accel_bias_stddev_m_s2),
    ):
        for axis in 'xyz':
            noise = sensor.find(f'imu/{channel}/{axis}/noise')
            assert noise.attrib['type'] == 'gaussian'
            assert float(noise.findtext('stddev')) == pytest.approx(sigma)
            assert sigma > 0
            assert float(noise.findtext('bias_stddev')) == pytest.approx(bias)
            assert float(noise.findtext('bias_mean')) == 0
            assert float(noise.findtext('mean')) == 0


@pytest.mark.parametrize('stereo', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('bias', [-0.03, 0.0, 0.03])
def test_overlay_preserves_source_and_applies_signed_bias(stereo, enabled, bias):
    models = Path(__file__).parents[1] / 'models'
    source = models / 'fsae_vehicle' / ('model_stereo.sdf' if stereo else 'model.sdf')
    original = source.read_bytes()
    with imu_model_resources(models, stereo=stereo, noise_enabled=enabled,
                             gyro_bias=bias) as resource:
        root = ET.parse(Path(resource) / 'fsae_vehicle/model.sdf')
        imu = root.find(".//sensor[@type='imu']/imu")
        assert float(imu.findtext('angular_velocity/z/noise/mean')) == bias
        for noise in imu.findall('.//noise'):
            for field in ('stddev', 'bias_stddev'):
                assert (float(noise.findtext(field)) > 0) is enabled
        assert len(root.findall(".//sensor[@type='camera']")) == (2 if stereo else 0)
    assert not Path(resource).exists()
    assert source.read_bytes() == original


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('enabled,bias', [('true', '0'), ('false', '-0.03')])
def test_launch_routes_native_settings_and_records_them(
        monkeypatch, perception, estimator, enabled, bias):
    # Evaluate all launch actions, including the server's resource overlay.
    import importlib.util
    pkg = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        'imu_launch', pkg / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: str(pkg))
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(
        perception=perception, estimator=estimator, imu_noise=enabled, gyro_bias=bias, seed='7')
    actions = module._launch_setup(context)
    server = next(a for a in actions if type(a) is ExecuteProcess)
    env = {perform_substitutions(context, k): perform_substitutions(context, v)
           for k, v in server.additional_env}
    resource = env['GZ_SIM_RESOURCE_PATH'].split(':')[0]
    imu = ET.parse(Path(resource) / 'fsae_vehicle/model.sdf').find(
        ".//sensor[@type='imu']/imu")
    assert float(imu.findtext('angular_velocity/z/noise/mean')) == float(bias)
    assert (float(imu.findtext('angular_velocity/z/noise/stddev')) > 0) == (enabled == 'true')
    command = [perform_substitutions(context, part) for part in server.cmd]
    assert command[-2:] == ['--seed', '7']
    nodes = [a for a in actions if isinstance(a, Node)]
    metrics = next(n for n in nodes if n.node_executable == 'metrics_node')
    params = evaluate_parameters(context, metrics._Node__parameters)[0]
    assert params['imu_noise'] is (enabled == 'true')
    assert params['gyro_bias'] == float(bias)
    for action in actions:
        if isinstance(action, RegisterEventHandler):
            action.event_handler.handle(Shutdown(reason='test complete'), context)


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_metrics_node_accepts_native_noise_provenance(monkeypatch, perception):
    from lhr_metrics.metrics_node import MetricsNode
    from test_launch_param_types import _start
    context, nodes = _launch(
        monkeypatch, perception=perception, estimator='ekf', imu_noise='false')
    metrics = next(n for n in nodes if n.node_executable == 'metrics_node')
    params = evaluate_parameters(context, metrics._Node__parameters)[0]
    # Match the launch default as well as the non-default override at node startup.
    for enabled in (True, params['imu_noise']):
        assert _start(MetricsNode, {'imu_noise': enabled}, ['imu_noise']) == {'imu_noise': enabled}
