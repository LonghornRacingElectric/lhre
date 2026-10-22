from pathlib import Path

from tools.vcu_sil import check_run, read_result, simulation_command

STOP = 5.0


def _row(time, tau_fw=0.0, tau_plant=0.0, vel_x=15.0):
    return {"time": time, "vcu.tau_fw": tau_fw, "vcu.tau_cmd_limited": tau_plant, "velX": vel_x}


def test_zero_torque_run_that_reaches_stop_time_passes():
    assert check_run([_row(0.0), _row(STOP, vel_x=14.0)], STOP) == []


def test_run_that_stops_early_fails():
    failures = check_run([_row(0.0), _row(2.0)], STOP)
    assert len(failures) == 1
    assert "stopped at 2" in failures[0]


def test_empty_result_fails():
    assert check_run([], STOP) == ["The result has no rows."]


def test_plant_torque_above_firmware_command_fails():
    failures = check_run([_row(0.0), _row(STOP, tau_plant=40.0)], STOP)
    assert any("plant" in failure for failure in failures)


def test_nonzero_firmware_torque_fails_while_app_has_no_control_law():
    failures = check_run([_row(0.0), _row(STOP, tau_fw=10.0, tau_plant=10.0)], STOP)
    assert any("control law" in failure for failure in failures)


def test_speeding_up_with_zero_torque_fails():
    failures = check_run([_row(0.0), _row(STOP, vel_x=15.5)], STOP)
    assert any("sped up" in failure for failure in failures)


def test_read_result_parses_omc_csv(tmp_path):
    csv_path = tmp_path / "result.csv"
    csv_path.write_text('"time","velX","vcu.tau_fw"\n0,15,0\n0.002,14.99,0\n')
    assert read_result(csv_path) == [
        {"time": 0.0, "velX": 15.0, "vcu.tau_fw": 0.0},
        {"time": 0.002, "velX": 14.99, "vcu.tau_fw": 0.0},
    ]


def test_stop_time_goes_to_its_own_flag_because_omc_ignores_it_as_an_override():
    command = simulation_command(Path("result.csv"), STOP)
    override = next(argument for argument in command if argument.startswith("-override="))
    assert "-stopTime=5.0" in command
    assert "stopTime" not in override
