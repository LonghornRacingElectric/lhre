"""Periodic GPS controls must reach the real nodes in both perception branches."""

from launch_ros.utilities import evaluate_parameters
from lhr_metrics.metrics_node import MetricsNode
from lhr_sensor_sim.gnss_sim_node import GnssSim
from lhr_state_estimation.ekf_node import EkfNode
import pytest
from test_launch_param_types import _launch, _start


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('override', [False, True])
def test_gnss_parameters_reach_actual_nodes(monkeypatch, perception, override):
    values = {'use_gnss': 'true', 'use_wheel_speeds': 'false'}
    if override:
        values.update(gnss_rate_hz='10', gnss_horizontal_stddev_m='1',
                      gnss_vertical_stddev_m='2', gnss_latency_s='0', gnss_gate_chi2='16',
                      gnss_max_delay_s='2', gnss_topic='/experiment/fix',
                      gnss_frame_id='antenna', gnss_origin_latitude_deg='30',
                      gnss_origin_longitude_deg='-97', gnss_origin_altitude_m='100',
                      gnss_heading_gain='0.25', yaw_rate_variance='0.000003',
                      calibrate_gyro='true', gyro_calibration_duration_s='1',
                      gyro_calibration_settle_s='0', gyro_calibration_timeout_s='5',
                      gyro_calibration_max_gap_s='0.5', gyro_calibration_max_translation_m='0.1',
                      gyro_calibration_max_yaw_deg='0.5',
                      gyro_calibration_max_gyro_std_rad_s='0.02',
                      gyro_calibration_max_accel_std_m_s2='0.2',
                      gyro_calibration_max_gyro_mean_rad_s='0.04',
                      gyro_calibration_min_samples='50', gyro_calibration_min_lidar_points='25',
                      gyro_calibration_lidar_topic='/experiment/raw_cloud',
                      gyro_calibration_lidar_qos='sensor_data')
    context, nodes = _launch(monkeypatch, perception=perception, estimator='ekf', **values)
    for executable, cls in [('gnss_sim', GnssSim), ('ekf_node', EkfNode),
                            ('metrics_node', MetricsNode)]:
        launch_node = next(n for n in nodes if n.node_executable == executable)
        params = evaluate_parameters(context, launch_node._Node__parameters)[0]
        if executable == 'metrics_node':
            # The launch fixture uses a nonexistent world; these assertions
            # concern forwarded controls, not reference-geometry loading.
            params['reference_world'] = ''
        actual = _start(cls, params, list(params))
        assert actual == params
        if override and executable != 'metrics_node':
            assert actual['gnss_topic'] == '/experiment/fix'


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_gnss_disabled_has_no_publisher_and_external_mode_omits_simulator(monkeypatch, perception):
    _, defaults = _launch(monkeypatch, perception=perception, estimator='ekf')
    assert not any(n.node_executable == 'gnss_sim' for n in defaults)
    _, external = _launch(monkeypatch, perception=perception, estimator='ekf',
                          use_gnss='true', simulate_gnss='false')
    assert not any(n.node_executable == 'gnss_sim' for n in external)
