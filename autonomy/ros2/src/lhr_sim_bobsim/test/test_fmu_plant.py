"""Closed-loop checks of FmuPlant against the real BobLib vehicle FMU."""

import math
from pathlib import Path
import random

import pytest

pytest.importorskip('fmpy')
fmu_plant = pytest.importorskip('lhr_sim_bobsim.fmu_plant')
try:
    fmu_plant.find_fmu()
except FileNotFoundError:
    pytest.skip('OrionFMI.fmu not built (make -C simulation fmu)', allow_module_level=True)

DT = 0.02


@pytest.fixture
def plant():
    return fmu_plant.FmuPlant(x=25.0, y=-3.0, yaw=math.pi / 2)


def run(plant, speed, steer, seconds):
    out = None
    for _ in range(int(round(seconds / DT))):
        out = plant.step(speed, steer, DT)
    return out


def test_starts_at_the_given_pose_and_drives_along_its_heading(plant):
    assert (plant.x, plant.y) == pytest.approx((25.0, -3.0), abs=0.02)
    x, y, yaw, speed, _ = run(plant, 3.0, 0.0, 4.0)
    assert y + 3.0 > 4.0
    assert x == pytest.approx(25.0, abs=0.3)
    assert yaw == pytest.approx(math.pi / 2, abs=0.05)


def test_launches_from_rest_and_tracks_speed(plant):
    _, _, _, speed, _ = run(plant, 6.0, 0.0, 8.0)
    assert speed == pytest.approx(6.0, abs=0.5)


def test_brakes_to_a_stop_and_holds(plant):
    run(plant, 8.0, 0.0, 6.0)
    _, _, _, speed, _ = run(plant, 0.0, 0.0, 4.0)
    assert abs(speed) < 0.2


def test_turns_left_with_positive_steer(plant):
    run(plant, 6.0, 0.0, 6.0)
    *_, yaw_rate = run(plant, 6.0, 0.1, 2.0)
    assert yaw_rate > 0.2


def test_road_wheels_follow_the_steering_command(plant):
    run(plant, 3.0, 0.0, 3.0)
    for command in (0.1, 0.3):
        run(plant, 3.0, command, 2.0)
        assert sum(plant.steer_angles) / 2.0 == pytest.approx(command, rel=0.05)


def test_clamps_steering_to_what_the_linkage_can_reach(plant):
    run(plant, 3.0, 0.0, 3.0)
    run(plant, 3.0, 2.0, 3.0)
    assert plant.restarts == 0
    # About 1.05 rad/s at full lock and 3 m/s.
    assert plant.yaw_rate > 0.7


def test_a_nan_command_is_a_stop_and_not_a_failed_step(plant):
    run(plant, 4.0, 0.0, 3.0)
    run(plant, math.nan, math.nan, 3.0)
    assert plant.restarts == 0
    assert abs(plant.speed) < 0.3


def test_reads_the_wheel_positions_from_the_fmu(plant):
    front_left, front_right, rear_left, rear_right = plant.wheels
    assert rear_left[0] == rear_right[0] == 0.0
    assert 1.0 < front_left[0] == front_right[0] < 2.5
    assert 0.4 < front_left[1] == -front_right[1] < 1.0
    assert 0.1 < plant.wheel_radius < 0.4


def test_launches_after_standing_still(plant):
    run(plant, 0.0, 0.0, 3.0)
    _, _, _, speed, _ = run(plant, 1.0, 0.114, 3.0)
    assert speed > 0.5


def test_stops_and_launches_again_without_a_restart(plant):
    for _ in range(4):
        run(plant, 6.0, 0.0, 4.0)
        assert plant.speed > 4.0
        run(plant, 0.0, 0.0, 3.0)
        assert abs(plant.speed) < 0.2
    assert plant.restarts == 0


def reverse_motor(plant):
    # What BobLib's motor does when the driveline rings backwards in a stop.
    plant._fmu.setReal([plant._vr['motor.motor.shaftDirection']], [-1.0])
    plant.step(3.0, 0.0, DT)
    assert not plant._forward


def test_resets_the_fmu_and_launches_forward_after_the_motor_reverses(plant, monkeypatch):
    resets = []
    start = plant._start
    monkeypatch.setattr(plant, '_start', lambda *pose: resets.append(pose) or start(*pose))
    fmu = plant._fmu
    reverse_motor(plant)
    run(plant, 3.0, 0.0, 4.0)
    assert len(resets) == 1
    assert plant._fmu is fmu
    assert plant.y + 3.0 > 3.0
    assert plant.speed == pytest.approx(3.0, abs=0.5)
    assert plant.restarts == 0


def test_a_second_reset_within_two_seconds_starts_rolling(plant):
    reverse_motor(plant)
    plant.step(3.0, 0.0, DT)
    assert plant.speed < 0.3
    reverse_motor(plant)
    plant.step(3.0, 0.0, DT)
    assert plant.speed == pytest.approx(fmu_plant.LAUNCH_SPEED_MPS, abs=0.2)


def test_stays_off_the_accelerator_while_the_motor_is_reversed(plant):
    run(plant, 6.0, 0.0, 4.0)
    plant._forward = False
    plant.step(12.0, 0.0, DT)
    assert plant._accel == 0.0


def test_drives_random_commands_without_a_restart(plant):
    rng = random.Random(1)
    moved = 0.0
    for _ in range(200):
        x, y = plant.x, plant.y
        out = run(plant, rng.uniform(0.0, 12.0), rng.uniform(-0.55, 0.55), 0.1)
        assert all(math.isfinite(value) for value in out)
        # Forward progress: a car that runs away backwards must not pass.
        moved += (plant.x - x) * math.cos(plant.yaw) + (plant.y - y) * math.sin(plant.yaw)
    assert plant.restarts == 0
    # About 30 m; a car that cannot launch moves under 5 m.
    assert moved > 15.0


def test_pedals_move_at_a_limited_rate(plant):
    plant.step(12.0, 0.0, DT)
    assert plant._accel == pytest.approx(fmu_plant.PEDAL_RATE_PER_S * DT)
    run(plant, 12.0, 0.0, 1.0)
    plant.step(0.0, 0.0, DT)
    assert plant._brake == pytest.approx(fmu_plant.PEDAL_RATE_PER_S * DT)


def test_an_interrupted_unzip_leaves_no_partial_copy(tmp_path, monkeypatch):
    import fmpy

    def interrupted(filename, unzipdir):
        (Path(unzipdir) / 'half').write_text('')
        raise KeyboardInterrupt

    monkeypatch.setenv('HOME', str(tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr(fmpy, 'extract', interrupted)
        with pytest.raises(KeyboardInterrupt):
            fmu_plant._unzipped(fmu_plant.find_fmu())
    assert list((tmp_path / '.cache' / 'lhr_sim_bobsim').iterdir()) == []
    first = Path(fmu_plant._unzipped(fmu_plant.find_fmu()))
    assert Path(fmu_plant._unzipped(fmu_plant.find_fmu())) == first
    assert list(first.parent.iterdir()) == [first]
    assert (first / 'modelDescription.xml').is_file()


def test_tire_forces_carry_the_car_and_turn_it(plant):
    run(plant, 8.0, 0.0, 8.0)
    fz = sum(f[2] for f in plant.tire_forces)
    assert abs(sum(f[1] for f in plant.tire_forces)) < 50.0
    run(plant, 8.0, 0.08, 6.0)
    # Newton: the lateral tire forces give the car its lateral acceleration.
    mass = fz / 9.81
    lateral = sum(f[1] for f in plant.tire_forces)
    assert lateral == pytest.approx(mass * plant.speed * plant.yaw_rate, rel=0.15)
    assert lateral > 0.0


def test_restarts_with_a_new_fmu_after_a_failed_step(plant):
    run(plant, 6.0, 0.0, 4.0)
    failed = plant._fmu
    before = (plant.x, plant.y, plant.speed)

    def fail(**_):
        raise RuntimeError('simulated solver failure')

    plant._fmu.doStep = fail
    x, y, _, speed, _ = plant.step(6.0, 0.0, DT)
    assert plant.restarts == 1
    assert plant._fmu is not failed
    assert (x, y) == pytest.approx(before[:2], abs=0.05)
    assert speed == pytest.approx(before[2], abs=0.3)
    x, y, _, speed, _ = run(plant, 6.0, 0.0, 2.0)
    assert math.hypot(x - before[0], y - before[1]) > 8.0
    assert speed == pytest.approx(6.0, abs=0.7)
