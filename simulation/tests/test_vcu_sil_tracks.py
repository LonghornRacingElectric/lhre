import csv

import pytest

from tools.vcu_sil_tracks import TRACKS, Track, track_errors, write_driver_trace

STRAIGHT = {"vcu.useMode": 1, "vcu.steerAmp": 0}


def _track(pedals):
    return Track("test", initial_speed_m_s=10.0, stop_time_s=2.0, steer=STRAIGHT, pedals=pedals)


@pytest.mark.parametrize("name", TRACKS)
def test_every_track_is_valid(name):
    assert track_errors(TRACKS[name]) == []


def test_coast_is_the_straight_line_reference_run_with_no_pedals():
    coast = TRACKS["coast"]
    assert (coast.initial_speed_m_s, coast.stop_time_s, coast.steer) == (15.0, 5.0, STRAIGHT)
    assert all(accelerator == brake == 0 for _, accelerator, brake in coast.pedals)


def test_trace_that_does_not_start_at_zero_seconds_fails():
    assert track_errors(_track(((0.5, 0.0, 0.0), (1.0, 1.0, 0.0)))) == ["The trace must start at 0 s."]


def test_empty_trace_fails():
    assert track_errors(_track(())) == ["The trace must start at 0 s."]


def test_trace_with_times_that_do_not_increase_fails():
    failures = track_errors(_track(((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0))))
    assert failures == ["The trace times must increase. 1 s follows 1 s."]


def test_pedal_outside_zero_to_one_fails():
    failures = track_errors(_track(((0.0, 0.0, 0.0), (1.0, 1.2, -0.1))))
    assert failures == ["The accelerator at 1 s is 1.2, not in [0, 1].", "The brake at 1 s is -0.1, not in [0, 1]."]


def test_driver_trace_has_the_header_that_fwvcu_skips_and_one_row_per_point(tmp_path):
    path = tmp_path / "driver.csv"
    write_driver_trace(path, _track(((0.0, 0.0, 0.0), (0.5, 0.25, 1.0))))
    with path.open(newline="") as trace_file:
        rows = list(csv.reader(trace_file))
    assert rows == [["time_s", "accelerator", "brake"], ["0.0", "0.0", "0.0"], ["0.5", "0.25", "1.0"]]
