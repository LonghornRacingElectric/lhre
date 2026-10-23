import hashlib
from pathlib import Path

import pytest

from tools.invalidation import Layout
from tools.vcu_sil import (
    OUT_DIR,
    check_run,
    main,
    missing_variables,
    read_result,
    run_dir,
    run_provenance,
    simulation_command,
)
from tools.vcu_sil_report import VARIABLES, variable_filter
from tools.vcu_sil_tracks import TRACKS

STOP = 5.0
TRACE = Path("/out/driver.csv")


def _row(time, tau_fw=0.0, tau_plant=0.0, vel_x=15.0, accel=0.0):
    return {"time": time, "vcu.accel_fw": accel, "vcu.tau_fw": tau_fw, "vcu.tau_cmd_limited": tau_plant, "velX": vel_x}


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


def test_firmware_torque_at_full_driver_demand_passes():
    assert check_run([_row(0.0), _row(STOP, tau_fw=230.0, tau_plant=230.0, accel=1.0)], STOP) == []


def test_firmware_torque_above_driver_demand_fails():
    failures = check_run([_row(0.0), _row(STOP, tau_fw=120.0, tau_plant=120.0, accel=0.5)], STOP)
    assert any("driver demand" in failure for failure in failures)


def test_negative_firmware_torque_fails():
    failures = check_run([_row(0.0), _row(STOP, tau_fw=-5.0, tau_plant=-5.0, accel=0.5)], STOP)
    assert any("driver demand" in failure for failure in failures)


def test_read_result_parses_omc_csv(tmp_path):
    csv_path = tmp_path / "result.csv"
    csv_path.write_text('"time","velX","vcu.tau_fw"\n0,15,0\n0.002,14.99,0\n')
    assert read_result(csv_path) == [
        {"time": 0.0, "velX": 15.0, "vcu.tau_fw": 0.0},
        {"time": 0.002, "velX": 14.99, "vcu.tau_fw": 0.0},
    ]


def test_stop_time_goes_to_its_own_flag_because_omc_ignores_it_as_an_override():
    command = simulation_command(Path("result.csv"), TRACKS["coast"], TRACE, traction_control=True)
    override = next(argument for argument in command if argument.startswith("-override="))
    assert "-stopTime=5.0" in command
    assert "stopTime" not in override


def test_simulation_command_overrides_the_start_speed_steer_trace_file_and_traction_control():
    command = simulation_command(Path("result.csv"), TRACKS["corner-exit"], TRACE, traction_control=True)
    override = next(argument for argument in command if argument.startswith("-override="))
    assert override.split("=", 1)[1].split(",") == [
        "initialVel=10.0",
        "vcu.useMode=1",
        "vcu.steerStart=0.5",
        "vcu.steerFreq=0.1",
        f"vcu.steerAmp={TRACKS['corner-exit'].steer['vcu.steerAmp']}",
        "vcu.driverTraceFile=/out/driver.csv",
        "vcu.tractionControl=true",
    ]


def test_simulation_command_turns_traction_control_off_in_the_firmware():
    command = simulation_command(Path("result.csv"), TRACKS["launch"], TRACE, traction_control=False)
    override = next(argument for argument in command if argument.startswith("-override="))
    assert override.endswith(",vcu.tractionControl=false")


def test_run_with_traction_control_writes_to_the_track_directory():
    assert run_dir("launch", traction_control=True) == OUT_DIR / "launch"


def test_run_without_traction_control_writes_to_its_own_directory():
    assert run_dir("launch", traction_control=False) == OUT_DIR / "launch--tc-off"


def test_unknown_track_fails_and_lists_the_track_names(capsys):
    with pytest.raises(SystemExit):
        main(["no-such-track"])
    error = capsys.readouterr().err
    assert all(name in error for name in TRACKS)


def test_unknown_traction_control_setting_fails():
    with pytest.raises(SystemExit):
        main(["launch", "--tc", "maybe"])


def test_simulation_command_writes_every_report_variable_including_protected_ones():
    command = simulation_command(Path("result.csv"), TRACKS["coast"], TRACE, traction_control=True)
    assert f"-variableFilter={variable_filter()}" in command
    assert "-emit_protected" in command


def test_check_run_reads_only_columns_that_the_report_filter_writes():
    rows = [{**dict.fromkeys(VARIABLES, 0.0), "time": time, "velX": 15.0} for time in (0.0, STOP)]
    assert check_run(rows, STOP) == []


def test_run_provenance_hashes_the_firmware_library(tmp_path):
    library = tmp_path / "libvcu_sil.so"
    library.write_bytes(b"firmware")
    provenance = run_provenance(Layout.default(), library)
    assert provenance["libvcu_sil.so sha256"] == hashlib.sha256(b"firmware").hexdigest()[:12]


def test_run_provenance_says_when_git_cannot_read_a_repo(tmp_path):
    library = tmp_path / "libvcu_sil.so"
    library.write_bytes(b"firmware")
    provenance = run_provenance(Layout(tmp_path), library)
    assert provenance["lhre"].startswith("unknown")
    assert provenance["BobLib"].startswith("unknown")


def test_missing_variables_names_each_report_variable_that_the_result_does_not_have():
    rows = [{name: 0.0 for name in VARIABLES if name != "vcu.tau_fw"}]
    assert missing_variables(rows) == ["vcu.tau_fw"]
