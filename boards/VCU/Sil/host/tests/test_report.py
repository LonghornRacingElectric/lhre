import math

from vcusil import cosim, report


def summary(**changes):
    s = {'steps': 1, 'states': ['IDLE', 'DRIVE'], 'first_drive_ms': 1023,
         'peak_speed_m_s': 30.0, 'final_speed_m_s': 29.0, 'min_speed_m_s': 0.0,
         'peak_torque_request_nm': 210.0, 'peak_dc_power_kw': 67.0, 'faults_seen': [],
         'backward_spin': {'rows': 0, 'first_ms': None, 'last_ms': None},
         'min_motor_speed_rad_s': 0.0, 'bspd_condition_ms': 0}
    s.update(changes)
    return s


def test_summary_warns_about_an_fmu_that_is_not_this_car():
    text = report.summary_text(summary(), cosim.Result(scenario='launch'), {
        'fmu': {'verified': False},
        'repo': {'vehicle_matches_fmu': False, 'bobsim_pin_matches_fmu': False,
                 'tires_match_fmu': False}})
    assert 'does not match its provenance manifest' in text
    assert 'different vehicle.yml' in text and 'different BobSim' in text
    assert 'tire files' in text
    quiet = report.summary_text(summary(), cosim.Result(scenario='launch'), {
        'fmu': {'verified': True},
        'repo': {'vehicle_matches_fmu': True, 'bobsim_pin_matches_fmu': True,
                 'tires_match_fmu': True}})
    assert 'WARNING' not in quiet


def test_summary_flags_the_bspd_and_backward_spin():
    text = report.summary_text(
        summary(bspd_condition_ms=600,
                backward_spin={'rows': 18, 'first_ms': 2067, 'last_ms': 2166},
                min_motor_speed_rad_s=-55.4),
        cosim.Result(scenario='launch'), {})
    assert "BSPD would have tripped" in text and '600 ms' in text
    assert '18 steps between 2067 and 2166 ms' in text
    assert 'BSPD' not in report.summary_text(summary(bspd_condition_ms=497),
                                             cosim.Result(scenario='launch'), {})


def test_summary_names_the_kind_of_failure():
    plant = cosim.Result(scenario='launch', status='plant_failed', failure='step 5: boom',
                         failure_lines=['CVODE: failed'])
    assert 'plant failure: step 5: boom' in report.summary_text(summary(), plant, {})
    harness = cosim.Result(scenario='launch', status='harness_error', failure='bad')
    assert 'harness error: bad' in report.summary_text(summary(), harness, {})


def test_trace_round_trips_floats_exactly(tmp_path):
    row = {c: 0 for c in cosim.COLUMNS}
    row.update(rearU=0.1 + 0.2, dcBusVoltage=math.pi, t_ms=3)
    result = cosim.Result(scenario='launch', rows=[row])
    report.write_trace(result, tmp_path / 'trace.csv')
    back = report.read_trace(tmp_path / 'trace.csv')[0]
    assert back['rearU'] == 0.1 + 0.2 and back['dcBusVoltage'] == math.pi
    assert list(back) == list(cosim.COLUMNS)
