"""Advance the exported BobLib vehicle with driver pedals and handwheel angle."""

import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

from lhr_sim_bobsim.plant import dependency_paths

INPUTS = ('steeringAngleCommand', 'acceleratorPedalCommand', 'brakePedalCommand')
OUTPUTS = ('rearX', 'rearY', 'rearYaw', 'rearU', 'rearV', 'rearYawRate')
REFERENCE = ('rearReferenceX', 'rearReferenceY', 'rearReferenceZ')


def load_provenance(path, vehicle_file, bobsim_root):
    """Refuse an artifact whose bytes or canonical vehicle identity changed."""
    path = Path(path)
    manifest = json.loads(path.with_name('provenance.json').read_text())
    if manifest.get('schema') != 'lhre.fmu.v1':
        raise ValueError('Unsupported FMU provenance schema; rebuild with make -C simulation fmu')
    for source, expected in ((path, manifest['fmu_sha256']),
                             (vehicle_file, manifest['vehicle_sha256'])):
        if hashlib.sha256(Path(source).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Stale or modified FMU input: {source}; rebuild the FMU')
    for relative, expected in manifest['tires'].items():
        tire = (vehicle_file.parent/relative).resolve()
        if not tire.is_relative_to(vehicle_file.parent.resolve()):
            raise ValueError('Tire provenance escapes the canonical vehicle directory')
        if hashlib.sha256(tire.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Stale FMU tire: {tire}; rebuild the FMU')
    try:
        sha = subprocess.check_output(
            ['git', '-C', str(bobsim_root), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f'Cannot read BobSim calibration commit at {bobsim_root}; '
            'use an initialized Git checkout and check BOBSIM_ROOT') from exc
    if sha != manifest['bobsim_sha']:
        raise ValueError('BobSim calibration differs from the FMU build; rebuild the FMU')
    return manifest


def interface_references(description):
    """Require the exported driver/rear-axle contract, including SI units."""
    expected_units = dict(zip(
        (*INPUTS, *OUTPUTS, *REFERENCE),
        ('rad', '1', '1', 'm', 'm', 'rad', 'm/s', 'm/s', 'rad/s', 'm', 'm', 'm')))
    if description.fmiVersion != '2.0' or description.coSimulation is None:
        raise ValueError('The plant needs an FMI 2.0 co-simulation FMU')
    variables = {v.name: v for v in description.modelVariables}
    for name in (*INPUTS, *OUTPUTS, *REFERENCE):
        variable = variables.get(name)
        causality = 'input' if name in INPUTS else 'output'
        if (variable is None or variable.type != 'Real' or variable.causality != causality
                or variable.unit != expected_units[name]):
            raise ValueError(f'FMU interface mismatch for {name}')
    return {name: variables[name].valueReference for name in (*INPUTS, *OUTPUTS, *REFERENCE)}


class FmiSession:
    """Own an FMI 2.0 instance and fail rather than advance after a rejected step."""

    def __init__(self, path):
        from fmpy import extract, read_model_description
        from fmpy.fmi2 import FMU2Slave
        description = read_model_description(str(path))
        references = interface_references(description)
        self.inputs = [references[name] for name in INPUTS]
        self.outputs = [references[name] for name in OUTPUTS]
        self.directory, self.slave, self.initialized = None, None, False
        self.time, self.instantiated = 0., False
        try:
            self.directory = extract(str(path))
            self.slave = FMU2Slave(
                guid=description.guid, unzipDirectory=self.directory,
                modelIdentifier=description.coSimulation.modelIdentifier, instanceName='orion')
            self.slave.instantiate()
            self.instantiated = True
            self.slave.setupExperiment(startTime=0., tolerance=1e-6)
            self.slave.enterInitializationMode()
            self.slave.setReal(self.inputs, [0., 0., 0.])
            self.slave.exitInitializationMode()
            self.initialized = True
            self.initial = self.state()
            self.reference = self.slave.getReal([references[name] for name in REFERENCE])
            if not all(map(math.isfinite, self.reference)) or abs(self.reference[1]) > 1e-9:
                raise ValueError(
                    'FMU reference must be the rear axle midpoint, not a wheel center')
        except BaseException:
            self.close()
            raise

    def state(self):
        """Read actual exported pose and body twist, never an integrated approximation."""
        state = tuple(self.slave.getReal(self.outputs))
        if not all(map(math.isfinite, state)):
            raise RuntimeError('FMU returned nonfinite vehicle state')
        return state

    def step(self, dt, controls):
        """Commit time only after the FMU accepts the step and returns finite outputs."""
        try:
            self.slave.setReal(self.inputs, controls)
            status = self.slave.doStep(
                currentCommunicationPoint=self.time, communicationStepSize=dt)
            if status not in (None, 0, 1):
                raise RuntimeError(f'FMU rejected step with status {status}')
            state = self.state()
            self.time += dt
            return state
        except Exception as error:
            try:
                self.close()
            except Exception as cleanup:
                raise RuntimeError(f'FMU failed at {self.time:.6f} s: {error}; '
                                   f'cleanup also failed: {cleanup}') from error
            raise RuntimeError(f'FMU integration failed at {self.time:.6f} s: {error}') from error

    def close(self):
        """Release native resources even when initialization or stepping failed."""
        slave, self.slave = self.slave, None
        try:
            if slave is not None and self.instantiated:
                try:
                    if self.initialized:
                        slave.terminate()
                finally:
                    slave.freeInstance()
        finally:
            if self.directory is not None:
                shutil.rmtree(self.directory, ignore_errors=True)
                self.directory = None


class FmuPlant:
    """Convert speed/road-wheel requests to the public driver interface."""

    def __init__(self, geometry, pose, path, *, pedal_gain=.1):
        import sys
        root, vehicle_file = dependency_paths()
        self.provenance = load_provenance(path, vehicle_file, root)
        sys.path.insert(0, str(root))
        from common.vehicle_io import load_yaml
        from engines.dynpy import Vehicle
        vehicle = Vehicle.from_yaml(vehicle_file)
        p = vehicle.parameters
        if not math.isclose(p.corner_positions_m[0][0]-p.corner_positions_m[2][0],
                            geometry.wheelbase_m, abs_tol=.001):
            raise ValueError('FMU and autonomy wheelbases differ')
        if not all(math.isclose(r, geometry.wheel_radius_m, abs_tol=.001)
                   for r in p.wheel_radius_m):
            raise ValueError('FMU and autonomy wheel radii differ')
        if not all(math.isclose(track, geometry.track_m, abs_tol=.001)
                   for track in (p.track_front_m, p.track_rear_m)):
            raise ValueError('FMU and autonomy tracks differ')
        if not math.isfinite(pedal_gain) or pedal_gain <= 0:
            raise ValueError('pedal_gain must be finite and positive')
        self.kinematics = vehicle.kinematics
        data = load_yaml(vehicle_file)
        self.rack_travel = data['front']['steering']['rack_travel_per_rev_m']
        self.geometry, self.pose, self.gain = geometry, pose, pedal_gain
        self.steering, self.time = 0., 0.
        self.session = FmiSession(path)
        rear = data['rear']['suspension']['wheel_center_m']
        expected = (rear[0], 0., rear[2])
        if not all(math.isclose(a, b, abs_tol=1e-8)
                   for a, b in zip(self.session.reference, expected)):
            self.session.close()
            raise ValueError('FMU rear-axle reference differs from the canonical vehicle')
        self.native = self.session.initial
        self.origin = self.native[:3]
        self.controls = (0., 0., 0.)

    def step(self, dt, target_speed, target_steer):
        """Step with pedals, preserving the model's own rest and brake dynamics."""
        if not all(math.isfinite(v) for v in (dt, target_speed, target_steer)) or dt <= 0:
            raise ValueError('Step and commands must be finite, with positive dt')
        target_speed = max(0., min(self.geometry.max_speed_mps, target_speed))
        target_steer = max(-self.geometry.max_steer_rad,
                           min(self.geometry.max_steer_rad, target_steer))
        delta = self.geometry.max_steer_rate_rad_s*dt
        self.steering += max(-delta, min(delta, target_steer-self.steering))
        rack = self.kinematics.rack_from_steering(self.steering)
        handwheel = 2*math.pi*rack/self.rack_travel
        error = self.gain*(target_speed-self.native[3])
        self.controls = (handwheel, max(0., min(1., error)), max(0., min(1., -error)))
        self.native = self.session.step(dt, self.controls)
        self.time = self.session.time
        return self.rear_state()

    def rear_state(self):
        """Apply one fixed map transform to the FMU's native rear-axle pose."""
        x, y, yaw, u, v, rate = self.native
        ox, oy, heading = self.origin
        px, py, initial_yaw = self.pose
        angle = initial_yaw-heading
        c, s = math.cos(angle), math.sin(angle)
        return px+c*(x-ox)-s*(y-oy), py+s*(x-ox)+c*(y-oy), yaw+angle, u, v, rate

    def close(self):
        """Release the native FMU instance."""
        self.session.close()
