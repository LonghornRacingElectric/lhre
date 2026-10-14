"""Orion as BobLib's full vehicle model: the FMU from ``make -C simulation fmu``."""

import logging
import math
import os
from pathlib import Path
import shutil
import tempfile

FMU_RELATIVE = Path('simulation') / 'out' / 'fmu' / 'OrionFMI.fmu'
# Steering wheel : road wheel, measured on the FMU.
STEERING_RATIO = 4.1
# Past about 0.7 rad at the road wheels the FMU's steering linkage has no
# solution, and every step fails.
MAX_STEER_RAD = 0.6
SPEED_KP = 0.25
SPEED_KI = 0.1
# A launch that fails restarts rolling at this speed.
LAUNCH_SPEED_MPS = 1.0
# A pedal is pressed at most this much per second and released at once. A
# stamped pedal is what locks the rear wheels and stalls the solver.
PEDAL_RATE_PER_S = 2.0
# Brake pedal that holds the car at a standstill. Releasing a full brake in
# one step, after the solver stood idle for minutes, fails the rear suspension.
HOLD_BRAKE = 0.3
# BobLib switches regen off above the full pack's voltage, and its battery
# equations have no solution right at that switch. Start below full.
SOC_START = 0.9
# Below this speed the road cannot turn a reversed motor shaft forward again.
STOPPED_MPS = 0.3
# A reset at rest that does not hold within this time is followed by a rolling one.
RETRY_S = 2.0
OUTPUTS = ('chassis.cgFreeMotion.r_rel_a[1]', 'chassis.cgFreeMotion.r_rel_a[2]',
           'chassis.cgFreeMotion.phi[3]', 'velX', 'yawVel', 'motor.motor.shaftDirection',
           'velY')
TIRES = ('frAxleDW.leftTire', 'frAxleDW.rightTire', 'rrAxleDW.leftTire', 'rrAxleDW.rightTire')
FORCES = tuple(f'chassis.detailedChassis.{t}.tireForceWorld[{i}]' for t in TIRES for i in (1, 2))
FORCES += ('Fz_FL', 'Fz_FR', 'Fz_RL', 'Fz_RR', 'leftSteerAngle', 'rightSteerAngle')
# The sprung-mass CG, then the wheelbase, the half tracks and the wheel size.
GEOMETRY = ('chassis.pVehicle.pSprungMass.rCM[1]', 'chassis.pVehicle.pSprungMass.rCM[2]',
            'wheelbase', 'pVehicle.pFrDW.wheelCenter[2]', 'pVehicle.pRrDW.wheelCenter[2]',
            'pVehicle.pFrPartialWheel.R0', 'pVehicle.pFrPartialWheel.rimWidth')


def find_fmu() -> Path:
    """Return ``$ORION_FMU``, or ``simulation/out/fmu/OrionFMI.fmu`` in the repository."""
    candidates = [Path(os.environ['ORION_FMU'])] if os.environ.get('ORION_FMU') else []
    # Above this file in a checkout; /opt/lhr is the container's read-only mount.
    candidates += [parent / FMU_RELATIVE for parent in Path(__file__).resolve().parents]
    candidates.append(Path('/opt/lhr') / FMU_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        'OrionFMI.fmu not found: run `make -C simulation fmu` or set $ORION_FMU')


def _unzipped(fmu: Path) -> str:
    from fmpy import extract

    # One copy per build: Gazebo exits without a hook that could delete a temp copy.
    target = Path.home() / '.cache' / 'lhr_sim_bobsim' / f'{fmu.stem}-{fmu.stat().st_mtime_ns}'
    if not target.is_dir():
        # Extract under another name, then rename: a start that is interrupted,
        # or two starts at once, must not leave half a copy for the next start.
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = tempfile.mkdtemp(dir=target.parent)
        try:
            extract(str(fmu), unzipdir=partial)
            os.rename(partial, target)
        except OSError:
            if not target.is_dir():
                raise
        finally:
            shutil.rmtree(partial, ignore_errors=True)
    return str(target)


class FmuPlant:
    """
    Drive the FMU like a driver: steering wheel, accelerator and brake.

    ``step`` takes a speed and a road-wheel steering command and returns
    base_link's pose and velocity. The car is the one in the FMU: this class
    reads its geometry from the FMU and needs no other vehicle data.
    """

    def __init__(self, x: float = 0.0, y: float = 0.0, yaw: float = 0.0,
                 max_steer_rad: float = MAX_STEER_RAD):
        from fmpy import read_model_description

        path = self.fmu_path = find_fmu()
        self._md = read_model_description(str(path))
        self._vr = {v.name: v.valueReference for v in self._md.modelVariables}
        self._unzip = _unzipped(path)
        self._inputs = [self._vr[n] for n in (
            'steeringAngleCommand', 'acceleratorPedalCommand', 'brakePedalCommand')]
        self._outputs = [self._vr[n] for n in OUTPUTS]
        # Tire forces are protected in BobLib; tools/fmu exports them.
        self._forces = ([self._vr[n] for n in FORCES]
                        if all(n in self._vr for n in FORCES) else None)
        self._max_steer = min(max_steer_rad, MAX_STEER_RAD)
        self.restarts = 0
        self._fmu = None
        self._clock = 0.0
        self._fresh_at = -math.inf
        # Per tire FL, FR, RL, RR: (Fx, Fy, Fz) in base_link, and the front
        # road-wheel angles (left, right). None if the FMU does not export them.
        self.tire_forces = None
        self.steer_angles = None
        self._start(x, y, yaw, 0.0)

    def _start(self, x: float, y: float, yaw: float, speed: float):
        """Start the FMU from its start values, base_link at (x, y, yaw), rolling at speed."""
        from fmpy.fmi2 import FMU2Slave

        if self._fmu is None:
            self._fmu = FMU2Slave(guid=self._md.guid, unzipDirectory=self._unzip,
                                  modelIdentifier=self._md.coSimulation.modelIdentifier,
                                  instanceName='orion')
            self._fmu.instantiate()
        else:
            self._fmu.reset()
        self._fmu.setReal([self._vr['initialVel'], self._vr['pVehicle.pBattery.SOC_start']],
                          [speed, SOC_START])
        self._fmu.setupExperiment(startTime=0.0)
        self._fmu.enterInitializationMode()
        self._fmu.exitInitializationMode()
        cg_x, cg_y, wheelbase, front, rear, self.wheel_radius, self.wheel_width = (
            self._fmu.getReal([self._vr[n] for n in GEOMETRY]))
        # Wheel centres FL, FR, RL, RR in base_link.
        self.wheels = ((wheelbase, front), (wheelbase, -front), (0.0, rear), (0.0, -rear))

        # The model starts facing +x (initialVel is along +x); (x, y, yaw)
        # places it. Its pose outputs track the sprung-mass CG. In BobLib's
        # vehicle coordinates the front axle is the origin, and base_link,
        # the rear-axle centre, is one wheelbase behind it.
        self._offset = (-wheelbase - cg_x, -cg_y)
        self._yaw0 = yaw
        ox, oy = self._rotate(yaw, *self._offset)
        self._cg0 = (x - ox, y - oy)
        self._t = 0.0
        self._integral = 0.0
        self._accel = self._brake = 0.0
        self._forward = True
        self.x, self.y, self.yaw = x, y, yaw
        self.speed, self.yaw_rate, self.lateral_speed = speed, 0.0, 0.0

    @staticmethod
    def _rotate(angle: float, x: float, y: float):
        c, s = math.cos(angle), math.sin(angle)
        return c * x - s * y, s * x + c * y

    def step(self, speed_cmd: float, steer_cmd: float, dt: float):
        """Hold the commands for dt; return (x, y, yaw, speed, yaw_rate) of base_link."""
        if math.isnan(speed_cmd) or math.isnan(steer_cmd):
            # A controller fault must not reach the FMU: stop, wheels straight.
            speed_cmd = steer_cmd = 0.0
        self._clock += dt
        if not self._forward and speed_cmd > 0.0 and abs(self.speed) < STOPPED_MPS:
            # BobLib's motor pushes the way its shaft last turned, and the
            # driveline rings backwards as the car stops. A stopped car cannot
            # turn the shaft forward again; a reset FMU starts forward. If the
            # last reset did not hold, start this one rolling.
            rolling = self._clock - self._fresh_at < RETRY_S
            self._fresh_at = self._clock
            self._start(self.x, self.y, self.yaw, LAUNCH_SPEED_MPS if rolling else 0.0)
        error = speed_cmd - self.speed
        demand = SPEED_KP * error + self._integral
        if -1.0 < demand < 1.0 and self._forward:
            self._integral += SPEED_KI * error * dt
        accel = min(max(demand, 0.0), 1.0) if speed_cmd > 0.0 else 0.0
        brake = min(max(-demand, 0.0), 1.0)
        if speed_cmd <= 0.0 and self.speed < 0.5:
            # Hold the brake: BobLib's brakes have no static friction.
            brake, self._integral = HOLD_BRAKE, 0.0
        if not self._forward:
            # The accelerator would drive the car backwards; coast until the
            # road turns the shaft forward again, and brake a backward slide.
            accel = 0.0
            if speed_cmd > 0.0:
                brake = max(brake, min(0.5, -SPEED_KP * self.speed))
        rise = PEDAL_RATE_PER_S * dt
        self._accel = accel = min(accel, self._accel + rise)
        self._brake = brake = min(brake, self._brake + rise)
        steer = min(max(steer_cmd, -self._max_steer), self._max_steer)

        self._fmu.setReal(self._inputs, [STEERING_RATIO * steer, accel, brake])
        try:
            self._fmu.doStep(currentCommunicationPoint=self._t, communicationStepSize=dt)
        except Exception as exc:  # noqa: BLE001 - fmpy raises FMICallException
            # A failed instance cannot step or reset: start a new one where the car is.
            self.restarts += 1
            speed = max(self.speed, LAUNCH_SPEED_MPS if speed_cmd > 0.0 else 0.0)
            logging.getLogger(__name__).warning(
                'FMU step failed (%s); restart %d at %.1f m/s', exc, self.restarts, speed)
            try:
                self._fmu.freeInstance()
            except Exception:  # noqa: BLE001 - it already failed
                pass
            self._fmu = None
            self._start(self.x, self.y, self.yaw, speed)
            return self.x, self.y, self.yaw, self.speed, self.yaw_rate
        self._t += dt

        dx, dy, dyaw, self.speed, self.yaw_rate, direction, vy = self._fmu.getReal(
            self._outputs)
        # Lateral velocity at base_link, behind the CG: v + yaw rate x lever arm.
        self.lateral_speed = vy + self.yaw_rate * self._offset[0]
        self._forward = direction > 0.0
        self.yaw = self._yaw0 + dyaw
        cg_x, cg_y = self._rotate(self._yaw0, dx, dy)
        base_x, base_y = self._rotate(self.yaw, *self._offset)
        self.x = self._cg0[0] + cg_x + base_x
        self.y = self._cg0[1] + cg_y + base_y
        if self._forces:
            f = self._fmu.getReal(self._forces)
            self.tire_forces = [(*self._rotate(-dyaw, f[2 * k], f[2 * k + 1]), f[8 + k])
                                for k in range(4)]
            self.steer_angles = (f[12], f[13])
        return self.x, self.y, self.yaw, self.speed, self.yaw_rate
