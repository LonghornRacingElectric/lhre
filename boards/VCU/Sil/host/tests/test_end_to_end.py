"""The real firmware against the real car model. Needs VCU_SIL_LIB and VCU_SIL_FMU."""

import dataclasses
import json
import os
from pathlib import Path

import pytest
from fakes import Launch

from vcusil import __main__ as cli
from vcusil import cosim, report, scenario_file
from vcusil.firmware import STATE_DRIVE, Firmware
from vcusil.plant import OUTPUTS, Plant, PlantInputs
from test_mcaplog import read_all

LAYOUT = Path(__file__).resolve().parents[2] / 'foxglove' / 'vcu_sil.json'


@pytest.fixture(scope='module')
def launch_run(lib, fmu_path, can_table_path, tmp_path_factory):
    out = tmp_path_factory.mktemp('launch')
    status = cli.main(['--lib', os.environ['VCU_SIL_LIB'], '--fmu', str(fmu_path),
                       '--can-table', str(can_table_path), '--out', str(out)])
    return status, out


def test_launch_completes_and_the_car_accelerates(launch_run):
    status, out = launch_run
    assert status == cli.EXIT_COMPLETED
    meta = json.loads((out / 'meta.json').read_text())
    s = meta['summary']
    assert meta['status'] == 'completed'
    assert s['states'] == ['IDLE', 'PRECHARGE', 'TS_ACTIVE', 'RTD_WAIT', 'DRIVE']
    assert 1020 <= s['first_drive_ms'] <= 1026
    assert s['left_drive_after_entering'] is False
    assert s['faults_seen'] == ['SHUTDOWN_OPEN']  # boot only, before the loop debounce
    assert s['peak_speed_m_s'] > 25.0
    # Rolling backward is invisible to the firmware (wheel sensors report |w|),
    # so it is checked here.
    assert s['min_speed_m_s'] > -0.05
    assert s['saturated_rows'] == 0
    assert s['bspd_condition_ms'] == 0
    # The stock torque map keeps DC power just under the 70 kW target; the
    # power cap itself never engages in this launch.
    assert 60.0 < s['peak_dc_power_kw'] < 70.0
    assert meta['plant']['series_cells'] == 140
    assert meta['fmu']['verified'] is True
    for name in ('trace.csv', 'run.mcap', 'plot.png', 'console.log', 'fmu.log', 'summary.txt'):
        assert (out / name).stat().st_size > 0, name


def test_the_car_gets_exactly_the_decoded_command(launch_run):
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    for r in rows:
        if r['fw_inverter_enable']:
            assert r['u_motor_torque_nm'] == r['fw_torque_request_nm']
        else:
            assert r['u_motor_torque_nm'] == 0.0
    drive = [r for r in rows if r['fw_state'] == STATE_DRIVE]
    assert all(r['fw_inverter_enable'] == 1 for r in drive)
    assert max(r['u_motor_torque_nm'] for r in rows) == pytest.approx(210.0)


def test_the_trace_replays_bit_for_bit(launch_run, fmu_path):
    """A fresh model fed the recorded inputs reproduces every recorded output,
    so the trace holds everything the model saw."""
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    with Plant(fmu_path) as car:
        for k, r in enumerate(rows):
            y = car.read()
            assert all(y[name] == r[name] for name in OUTPUTS), k
            car.step(k, PlantInputs(r['u_motor_torque_nm'], r['u_regen_limit_nm'],
                                    r['u_brake'], r['u_steering_rad']))


def test_two_runs_are_identical(lib, fmu_path):
    def once():
        with Firmware(lib) as fw, Plant(fmu_path) as car:
            return cosim.run(Launch(duration_ms=3000), fw, car).rows
    assert once() == once()


def test_launch_file_drives_the_car_model_like_the_stage_2_launch(lib, fmu_path):
    def rows(scenario):
        with Firmware(lib) as fw, Plant(fmu_path) as car:
            return cosim.run(scenario, fw, car).rows
    timeline = dataclasses.replace(scenario_file.load('launch').timeline, duration_ms=3000)
    assert rows(timeline) == rows(Launch(duration_ms=3000))


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_scenario_completes_on_the_car_model(name, fmu_path, can_table_path, tmp_path):
    status = cli.main(['--lib', os.environ['VCU_SIL_LIB'], '--fmu', str(fmu_path),
                       '--can-table', str(can_table_path), '--out', str(tmp_path),
                       '--scenario', name, '--no-plot'])
    meta = json.loads((tmp_path / 'meta.json').read_text())
    assert status == cli.EXIT_COMPLETED and meta['status'] == 'completed'
    assert meta['summary']['min_speed_m_s'] > -0.1
    # A file sets off only the warnings its [allow] names, and each of those.
    assert meta['scenario_warnings'] == []
    assert ({w['code'] for w in meta['scenario_allowed']}
            == set(scenario_file.load(name).allow))


def test_the_held_brake_reaches_the_model(launch_run):
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    held = [r for r in rows if 3 <= r['t_ms'] < 1500]
    assert all(r['u_brake'] == 350.0 / 1500.0 for r in held)
    assert all(r['mechanicalBrakeTorque'] == pytest.approx(r['u_brake'] * 1500.0) for r in held)


def test_the_firmware_sees_the_backward_spin_as_negative_rpm(launch_run):
    """The model's launch ring turns the motor backward; the inverter reports
    a signed speed, so the firmware sees it too (the wheels report |w|)."""
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    assert min(r['motorSpeed'] for r in rows) < -10.0
    assert min(r['fw_motor_rpm'] for r in rows) < -100.0
    meta = json.loads((out / 'meta.json').read_text())
    assert meta['summary']['backward_spin']['rows'] > 0


def test_model_time_runs_at_firmware_time(launch_run):
    """Distance equals speed integrated over 3 ms rows, so the model steps
    3 ms per firmware step."""
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    distance = rows[-1]['rearX'] - rows[0]['rearX']
    integral = sum(r['rearU'] for r in rows[:-1]) * cosim.STEP_MS / 1000
    assert distance == pytest.approx(integral, rel=0.01)


def test_the_run_log_holds_firmware_frames_and_model_truth(launch_run):
    _, out = launch_run
    meta = json.loads((out / 'meta.json').read_text())
    assert meta['plant']['truth_missing'] == []
    assert meta['plant']['slip_from_states'] is True
    assert meta['frames_dropped'] == 0
    assert meta['can_table']['spec_sha256']
    messages, schemas, attachments, _ = read_all(out / 'run.mcap')
    rows = report.read_trace(out / 'trace.csv')
    truth = messages['/model/truth']
    assert len(truth) == len(rows)
    for key in ('velX', 'Fz_RL', 'kappa_RL', 'alpha_FL', 'soc', 'inverterPowerOut'):
        assert key in truth[0][1], key
    # The trace and the log carry the same truth, to the bit.
    assert [m['velX'] for _, m in truth] == [r['truth_velX'] for r in rows]
    # Model truth sees what the firmware's |w| sensors cannot: slip at launch.
    kappa = [m['kappa_RL'] for _, m in truth]
    assert None not in kappa  # a real run's slip states are finite throughout
    assert max(kappa) > 0.02
    units = meta['plant']['truth_units']
    assert (units['kappa_RL'], units['alpha_FL']) == ('1', 'rad')
    # Brake truth is one corner per axle: BobLib halves each circuit, so twice
    # FL plus twice RL is the whole brake torque, and the bias favours the front.
    outputs = messages['/model/outputs']
    assert [t for t, _ in outputs] == [t for t, _ in truth]
    held = [(m, y) for (t_ns, m), (_, y) in zip(truth, outputs, strict=True)
            if 300 <= t_ns // 1_000_000 < 1500]
    assert held and all(y['mechanicalBrakeTorque'] > 0 for _, y in held)
    for m, y in held:
        assert 2 * (m['brakeTorqueFL'] + m['brakeTorqueRL']) == pytest.approx(
            y['mechanicalBrakeTorque'])
        assert m['brakeTorqueFL'] > m['brakeTorqueRL']
    # The model's motor command comes after its own torque limit.
    assert all(abs(m['motorModelTorqueCommand']) <= m['motorTorqueLimit'] + 1e-9
               for _, m in truth)
    soc = [m['soc'] for _, m in truth]
    assert soc[0] == pytest.approx(0.9) and soc[-1] < soc[0]
    # The rig's speed frames carry the model's motor speed as the inverter
    # would report it.
    for t_ns, msg in messages['/can/car/INVERTER_SPEED'][::50]:
        row = next(r for r in rows if r['t_ms'] == t_ns // 1_000_000)
        assert msg['motor_speed'] == round(row['motorSpeed'] * 30 / 3.141592653589793)
    states = {m['state_name'] for _, m in messages['/can/vcu/VCU_FAULTS']}
    assert 'DRIVE' in states
    assert set(attachments) == {'meta.json', 'summary.txt', 'scenario.toml'}
    levels = [m['level'] for _, m in messages['/events']]
    assert max(levels) == 3  # warnings (boot fault, launch ring), no errors


def _field_exists(schema: dict, path: list[str]) -> bool:
    for key in path:
        props = schema.get('properties', {})
        if key not in props:
            return False
        schema = props[key]
    return True


def test_every_layout_path_exists_in_a_run(launch_run):
    """A renamed topic or field shows up in Foxglove only as an empty panel,
    so check the committed layout against what a real run writes."""
    _, out = launch_run
    _, schemas, _, _ = read_all(out / 'run.mcap')
    layout = json.loads(LAYOUT.read_text())
    seen = 0
    for panel_id, config in layout['configById'].items():
        refs = [p['value'] for p in config.get('paths', [])]
        refs += [config[k] for k in ('topicPath', 'topicToRender') if k in config]
        for ref in refs:
            topic, _, field = ref.partition('.')
            assert topic in schemas, f'{panel_id}: no topic {topic}'
            if field:
                assert _field_exists(schemas[topic][1], field.split('.')), f'{panel_id}: {ref}'
            seen += 1
    assert seen >= 20
    placed = []

    def walk(node):
        if isinstance(node, str):
            placed.append(node)
        else:
            walk(node['first'])
            walk(node['second'])
    walk(layout['layout'])
    assert sorted(placed) == sorted(layout['configById'])
