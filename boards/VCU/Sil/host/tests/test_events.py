from fakes import ToyCar
from test_cosim import BrakeOnThrottle

from vcusil import cosim, events
from vcusil.firmware import Firmware
from vcusil.scenario import Launch


def run(lib, scenario, car=None):
    with Firmware(lib) as fw:
        result = cosim.run(scenario, fw, car or ToyCar())
        marks = events.derive(result, fw.state_name, fw.fault_name, fw.transition_reason)
    return result, marks


def test_a_launch_marks_each_state_change_with_its_reason(lib):
    _, marks = run(lib, Launch())
    states = [(e.t_ms, e.message) for e in marks if e.name == 'vcu.state']
    assert states == [
        (0, 'start -> IDLE (boot complete)'),
        (210, 'IDLE -> PRECHARGE (HVC precharging)'),
        (600, 'PRECHARGE -> TS_ACTIVE (precharge complete)'),
        (603, 'TS_ACTIVE -> RTD_WAIT (drive preconditions met)'),
        (1023, 'RTD_WAIT -> DRIVE (ready to drive)'),
    ]
    faults = [(e.t_ms, e.level, e.message) for e in marks if e.name == 'vcu.fault']
    # The loop reads open until its 10 ms debounce passes at boot.
    assert faults == [(0, events.WARNING, 'fault raised: SHUTDOWN_OPEN'),
                      (12, events.INFO, 'fault cleared: SHUTDOWN_OPEN')]
    assert marks[0].message == 'scenario launch starts'
    assert marks[-1].message == 'scenario launch completed'
    assert [e.t_ms for e in marks] == sorted(e.t_ms for e in marks)
    assert not [e for e in marks if e.name in ('rig.saturation', 'model.backward_spin', 'bspd')]


def test_the_bspd_condition_is_marked_once_it_lasts_500_ms(lib):
    result, marks = run(lib, BrakeOnThrottle())
    bspd = [e for e in marks if e.name == 'bspd']
    assert len(bspd) == 1 and bspd[0].level == events.ERROR
    hard = [r['t_ms'] for r in result.rows if r['fw_hard_braking']]
    assert bspd[0].t_ms - hard[0] >= cosim.BSPD_TRIP_MS - cosim.STEP_MS


def test_backward_spin_and_saturation_windows_open_and_close():
    rows = []
    for k in range(10):
        row = {c: 0 for c in cosim.COLUMNS}
        row.update(t_ms=3 * k, fw_state=2, fw_control_ms=3 * k, dcBusVoltage=574.0)
        if 3 <= k < 5:
            row['motorSpeed'] = -1.0
        if 6 <= k < 8:
            row['fw_saturated'] = 1  # wheel_speed
        rows.append(row)
    result = cosim.Result(scenario='synthetic', rows=rows)
    marks = events.derive(result, lambda s: 'DRIVE', lambda c: f'F{c}', lambda i: '')
    spin = [(e.t_ms, e.level) for e in marks if e.name == 'model.backward_spin']
    assert spin == [(9, events.WARNING), (15, events.INFO)]
    sat = [(e.t_ms, e.message) for e in marks if e.name == 'rig.saturation']
    assert sat == [(18, 'wheel_speed clamped to its sensor or field'),
                   (24, 'wheel_speed back in range')]


def test_failures_end_the_event_list():
    result = cosim.Result(scenario='x', status='plant_failed', failure='step 2: boom',
                          rows=[{**{c: 0 for c in cosim.COLUMNS}, 't_ms': 6}])
    marks = events.derive(result, lambda s: 'INIT', lambda c: '', lambda i: '')
    assert (marks[-1].name, marks[-1].level, marks[-1].t_ms) == ('model.failure', events.FATAL, 6)


def test_the_bspd_timer_restarts_when_the_condition_breaks():
    """Two 498 ms stretches with one clean step between them are not a trip;
    a 501 ms stretch is, once, and the summary reports the longest."""
    pattern = [1] * 166 + [0] + [1] * 166 + [0] + [1] * 167 + [0] * 3
    rows = []
    for k, hard in enumerate(pattern):
        row = {c: 0 for c in cosim.COLUMNS}
        row.update(t_ms=3 * k, fw_state=2, fw_control_ms=3 * k, dcBusVoltage=500.0,
                   dcBusCurrent=20.0, fw_hard_braking=hard)  # 10 kW
        rows.append(row)
    result = cosim.Result(scenario='synthetic', rows=rows)
    marks = events.derive(result, lambda s: 'DRIVE', lambda c: f'F{c}', lambda i: '')
    bspd = [e.t_ms for e in marks if e.name == 'bspd']
    start = 3 * (166 + 1 + 166 + 1)
    assert bspd == [start + 3 * 166]  # its 167th step reaches 501 ms
    assert cosim._bspd_condition_ms(rows) == 501
    # Under 5 kW it is no BSPD case at all.
    for row in rows:
        row['dcBusCurrent'] = 9.0
    assert not [e for e in events.derive(result, lambda s: 'DRIVE', lambda c: '', lambda i: '')
                if e.name == 'bspd']
