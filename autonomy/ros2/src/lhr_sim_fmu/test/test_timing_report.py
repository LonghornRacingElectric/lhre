"""Timing acceptance must not turn missing measurements into a pass."""

from lhr_sim_fmu.timing_report import CAMERAS, LIDAR, summarize
import pytest


def step(wall=.03, speed=4.5):
    return {'wall_sec': wall, 'step_sec': .02, 'speed_mps': speed, 'clock_origin_sec': .002,
            'fmu_time_sec': .02}


def pose(topic=LIDAR, lag=.019, matched=True):
    return {'topic': topic, 'sensor_stamp_sec': 1., 'lag_sec': lag,
            'observation_age_sec': .001, 'matched': matched, 'lockstep': True}


def test_slow_solver_is_acceptable_when_sensor_age_is_bounded():
    result = summarize([step(.03), step(.05)], [pose()])
    assert result['timing_passed']
    assert result['steps']['all']['overruns'] == 2
    assert result['steps']['all']['plant_speedup'] == pytest.approx(.5)
    assert result['steps']['all']['wall_sec']['p95'] == .05


@pytest.mark.parametrize('steps,poses,stereo', [
    ([], [pose()], False),
    ([step(speed=0.)], [pose()], False),
    ([step(speed=-4.5)], [pose()], False),
    ([step()], [], False),
    ([step()], [pose(matched=False)], False),
    ([step()], [pose(lag=.1)], False),
    ([step()], [pose(lag=-.01), pose()], False),
    ([step()], [pose()], True),
])
def test_missing_speed_stream_or_age_violation_fails(steps, poses, stereo):
    assert not summarize(steps, poses, stereo=stereo)['timing_passed']


def test_stereo_needs_both_camera_streams():
    result = summarize([step()], [pose(), *(pose(topic) for topic in CAMERAS)], stereo=True)
    assert result['timing_passed']
    assert len(result['sensors']) == 3


def test_pre_start_frame_is_reported_separately_from_missing_pose():
    startup = {**pose(matched=False), 'sensor_stamp_sec': .001}
    bootstrap = {**pose(matched=False), 'sensor_stamp_sec': .002}
    result = summarize([step()], [startup, bootstrap, pose()])
    assert result['clock_origin_sec'] == .002
    assert result['sensors'][LIDAR]['pre_start'] == 2
    assert result['sensors'][LIDAR]['unmatched'] == 0
    assert result['timing_passed']


def test_missing_timebase_cannot_claim_timing_acceptance():
    row = step()
    del row['clock_origin_sec']
    assert not summarize([row], [pose()])['timing_passed']
