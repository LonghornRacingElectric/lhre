"""Adapt BobSim's public 3 DOF model to rear-axle commands and state."""

import math
import os
from pathlib import Path
import sys

import numpy as np
from scipy.integrate import solve_ivp


def dependency_paths():
    """Locate the external model and canonical dynamics vehicle file."""
    return (Path(os.environ.get('BOBSIM_ROOT', '/opt/lhr/simulation/bobsim')),
            Path(os.environ.get('BOBSIM_VEHICLE', '/opt/lhr/vehicle/vehicle.yml')))


class BobSimPlant:
    """Advance planar dynamics with limited steering and wheel torque."""

    def __init__(self, geometry, pose, *, max_accel=2., max_decel=3., speed_gain=2.):
        root, vehicle_file = dependency_paths()
        if not (root / '_0_Utils/dyn_py').is_dir() or not vehicle_file.is_file():
            raise RuntimeError('BobSim unavailable: initialize simulation/bobsim and set '
                               'BOBSIM_ROOT and BOBSIM_VEHICLE (see lhr_sim_bobsim README)')
        sys.path.insert(0, str(root))
        from _0_Utils.dyn_py import ModelInputs, Vehicle
        self.inputs_type = ModelInputs
        vehicle = Vehicle.from_yaml(vehicle_file)
        self.model = vehicle.model(3)
        self.parameters = vehicle.parameters
        p = self.parameters
        wheelbase = p.corner_positions_m[0][0] - p.corner_positions_m[2][0]
        if not math.isclose(wheelbase, geometry.wheelbase_m, abs_tol=.001):
            raise ValueError('BobSim and autonomy wheelbases differ')
        if not all(math.isclose(radius, geometry.wheel_radius_m, abs_tol=.001)
                   for radius in p.wheel_radius_m):
            raise ValueError('BobSim and autonomy wheel radii differ')
        self.rear_x = (p.corner_positions_m[2][0] + p.corner_positions_m[3][0]) / 2
        self.state = vehicle.initial_state(3, 0.)
        x, y, yaw = pose
        self.state[:3] = (x - self.rear_x * math.cos(yaw),
                          y - self.rear_x * math.sin(yaw), yaw)
        self.geometry = geometry
        self.max_accel, self.max_decel, self.speed_gain = max_accel, max_decel, speed_gain
        if min(max_accel, max_decel, speed_gain) <= 0:
            raise ValueError('acceleration, deceleration and speed gain must be positive')
        self.steering = 0.
        self.time = 0.
        self.last_torques = (0., 0., 0., 0.)

    def step(self, dt, target_speed, target_steer):
        """Integrate one control period without setting velocity directly."""
        if not all(math.isfinite(v) for v in (dt, target_speed, target_steer)) or dt <= 0:
            raise ValueError('step and commands must be finite, with positive dt')
        g, p = self.geometry, self.parameters
        target_speed = float(np.clip(target_speed, 0., g.max_speed_mps))
        target_steer = float(np.clip(target_steer, -g.max_steer_rad, g.max_steer_rad))
        self.steering += float(np.clip(target_steer - self.steering,
                                       -g.max_steer_rate_rad_s * dt,
                                       g.max_steer_rate_rad_s * dt))
        u = float(self.state[3])
        acceleration = float(np.clip(self.speed_gain * (target_speed - u),
                                     -self.max_decel, self.max_accel))
        force = p.mass_kg * acceleration
        if force >= 0:
            force = min(force, p.peak_drive_force_n,
                        p.peak_drive_power_w / max(abs(u), .1))
            front = p.drive_distribution_front
        else:
            front = p.brake_distribution_front
        fractions = (front / 2, front / 2, (1 - front) / 2, (1 - front) / 2)
        self.last_torques = tuple(force * f * r for f, r in zip(fractions, p.wheel_radius_m))
        inputs = self.inputs_type(steering_rad=self.steering,
                                  wheel_torques_nm=self.last_torques)
        solution = solve_ivp(lambda t, x: self.model.derivative(t, x, inputs),
                             (self.time, self.time + dt), self.state,
                             method='RK45', rtol=1e-6, atol=1e-8)
        if not solution.success or not np.all(np.isfinite(solution.y[:, -1])):
            raise RuntimeError(f'BobSim integration failed: {solution.message}')
        self.state = solution.y[:, -1]
        # The planar model has no parking brake; hold after forward braking reaches rest.
        if target_speed == 0 and self.state[3] < .03:
            self.state[3:] = 0.
        self.time += dt
        return self.rear_state()

    def rear_state(self):
        """Transform center-of-mass pose and body velocity to rear-axle base_link."""
        x, y, yaw, u, v, rate = map(float, self.state)
        return (x + self.rear_x * math.cos(yaw), y + self.rear_x * math.sin(yaw),
                yaw, u, v + rate * self.rear_x, rate)
