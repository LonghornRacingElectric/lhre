"""Failures and transforms must preserve the FMI/ROS time and state contract."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
from types import SimpleNamespace

from lhr_sim_fmu.plant import (
    FmiSession, FmuPlant, INPUTS, interface_references, load_provenance, OUTPUTS, REFERENCE)
from lhr_sim_fmu.sim_node import SimFmu
from lhr_vehicle import load_vehicle
import pytest
import rclpy


@pytest.mark.parametrize('status', [2, 3, 4])
def test_rejected_step_never_advances_time_and_releases_native_instance(tmp_path, status):
    released = []
    session = FmiSession.__new__(FmiSession)
    session.time, session.initialized, session.instantiated = 1., True, True
    session.inputs, session.outputs = [], []
    session.directory = str(tmp_path/'extract')
    Path(session.directory).mkdir()
    session.slave = SimpleNamespace(
        setReal=lambda *_: None, doStep=lambda **_: status,
        terminate=lambda: released.append('terminate'),
        freeInstance=lambda: released.append('free'))
    with pytest.raises(RuntimeError, match='rejected step'):
        session.step(.02, (0., 0., 1.))
    assert session.time == 1.
    assert released == ['terminate', 'free']
    assert not (tmp_path/'extract').exists()
    session.close()
    assert released == ['terminate', 'free']


def test_nonfinite_outputs_do_not_commit_a_successful_native_step():
    session = FmiSession.__new__(FmiSession)
    session.time, session.initialized, session.directory = 2., True, None
    session.instantiated = True
    session.inputs, session.outputs = [], []
    session.slave = SimpleNamespace(
        setReal=lambda *_: None, doStep=lambda **_: 0,
        getReal=lambda _: [float('nan')]*6, terminate=lambda: None, freeInstance=lambda: None)
    with pytest.raises(RuntimeError, match='nonfinite'):
        session.step(.02, (0., 0., 0.))
    assert session.time == 2.
    assert session.slave is None


def test_fixed_pose_transform_preserves_measured_body_twist():
    plant = FmuPlant.__new__(FmuPlant)
    plant.origin, plant.pose = (1., 2., .5), (10., 5., 1.)
    plant.native = (1.+3*math.cos(.5), 2.+3*math.sin(.5), .7, 4., .2, .1)
    assert plant.rear_state() == pytest.approx(
        (10.+3*math.cos(1.), 5.+3*math.sin(1.), 1.2, 4., .2, .1))


@pytest.mark.parametrize('speed,accelerator,brake', [(100., 1., 0.), (-100., 0., .4)])
def test_driver_pedals_are_bounded_exclusive_and_do_not_set_vehicle_speed(
        speed, accelerator, brake):
    plant = FmuPlant.__new__(FmuPlant)
    plant.geometry, plant.pose = load_vehicle(), (0., 0., 0.)
    plant.origin, plant.native = (0., 0., 0.), (0., 0., 0., 4., 0., 0.)
    plant.steering, plant.time, plant.gain, plant.rack_travel = 0., 0., .1, .1
    plant.kinematics = SimpleNamespace(rack_from_steering=lambda steer: steer*.01)
    requested = []
    plant.session = SimpleNamespace(
        time=.02, step=lambda dt, values: requested.append(values) or plant.native)
    state = plant.step(.02, speed, 100.)
    assert plant.controls[1:] == pytest.approx((accelerator, brake))
    assert plant.steering <= plant.geometry.max_steer_rate_rad_s*.02
    assert state[3] == 4.
    assert requested == [plant.controls]


def test_modified_artifact_and_vehicle_are_rejected_before_loading_native_code(tmp_path):
    artifact, vehicle = tmp_path/'OrionFMI.fmu', tmp_path/'vehicle.yml'
    artifact.write_bytes(b'compiled artifact')
    vehicle.write_bytes(b'canonical vehicle')

    def fingerprint(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = {'schema': 'lhre.fmu.v1', 'fmu_sha256': fingerprint(artifact),
                'vehicle_sha256': fingerprint(vehicle), 'tires': {}, 'bobsim_sha': 'unused'}
    (tmp_path/'provenance.json').write_text(json.dumps(metadata))
    artifact.write_bytes(b'changed artifact')
    with pytest.raises(ValueError, match='Stale or modified'):
        load_provenance(artifact, vehicle, tmp_path)
    artifact.write_bytes(b'compiled artifact')
    vehicle.write_bytes(b'changed car')
    with pytest.raises(ValueError, match='Stale or modified'):
        load_provenance(artifact, vehicle, tmp_path)


def test_ros_step_failure_cannot_publish_new_time_or_odometry(monkeypatch):
    class FailingPlant:
        def __init__(self, *args, **kwargs):
            self.time, self.provenance, self.closed = 0., {}, False

        def step(self, *args):
            raise RuntimeError('native solver failed')

        def close(self):
            self.closed = True

    monkeypatch.setattr('lhr_sim_fmu.sim_node.FmuPlant', FailingPlant)
    rclpy.init(args=['--ros-args', '-p', 'publish_clock:=true'])
    node = SimFmu()
    messages = []
    node._odom_pub = node._clock_pub = SimpleNamespace(publish=messages.append)
    stamp = node._sim_ns
    try:
        with pytest.raises(RuntimeError, match='native solver failed'):
            node._step()
        assert node._sim_ns == stamp
        assert not messages
    finally:
        node.destroy_node()
        rclpy.shutdown()
    assert node._plant.closed


@pytest.mark.parametrize('defect', ['version', 'missing', 'unit', 'causality', 'type'])
def test_incompatible_fmi_interface_is_rejected_before_instantiation(defect):
    units = ('rad', '1', '1', 'm', 'm', 'rad', 'm/s', 'm/s', 'rad/s', 'm', 'm', 'm')
    variables = [SimpleNamespace(name=name, type='Real', valueReference=i, unit=unit,
                                 causality='input' if name in INPUTS else 'output')
                 for i, (name, unit) in enumerate(zip((*INPUTS, *OUTPUTS, *REFERENCE), units))]
    description = SimpleNamespace(
        fmiVersion='2.0', coSimulation=object(), modelVariables=variables)
    assert len(interface_references(description)) == 12
    if defect == 'version':
        description.fmiVersion = '3.0'
    elif defect == 'missing':
        variables.pop()
    elif defect == 'unit':
        variables[0].unit = 'deg'
    elif defect == 'causality':
        variables[0].causality = 'parameter'
    else:
        variables[0].type = 'Integer'
    with pytest.raises(ValueError):
        interface_references(description)


@pytest.mark.parametrize('error', [
    OSError('git unavailable'), subprocess.CalledProcessError(128, ['git']),
])
def test_missing_bobsim_checkout_has_an_actionable_error(tmp_path, monkeypatch, error):
    artifact, vehicle = tmp_path/'OrionFMI.fmu', tmp_path/'vehicle.yml'
    artifact.write_bytes(b'compiled artifact')
    vehicle.write_bytes(b'canonical vehicle')
    manifest = {'schema': 'lhre.fmu.v1',
                'fmu_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                'vehicle_sha256': hashlib.sha256(vehicle.read_bytes()).hexdigest(),
                'tires': {}, 'bobsim_sha': 'unused'}
    (tmp_path/'provenance.json').write_text(json.dumps(manifest))

    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr('lhr_sim_fmu.plant.subprocess.check_output', fail)
    with pytest.raises(ValueError, match='initialized Git checkout.*BOBSIM_ROOT'):
        load_provenance(artifact, vehicle, tmp_path)
