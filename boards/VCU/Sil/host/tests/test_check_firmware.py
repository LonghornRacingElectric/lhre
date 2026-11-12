"""The lint's models of the firmware, held to the firmware itself.

check.py predicts, tick by tick, what the RTD switch's debounce, the brake
and pedal filters, brake-pressed and the shutdown loop make of a file's
samples. Each test runs small files through the library with cosim.run, the
loop and input mapping a real run uses, and compares the models with the
firmware's own snapshot at every tick. A model the firmware disagrees with
is wrong.
"""

import re

import pytest
from fakes import ToyCar
from test_check import (BASE, UNPLUGGED_RTD, at, brake_off_at, drive, hard_stop_pedal_at,
                        insert_before, press_at, released)

from vcusil import check, cosim, scenario_file
from vcusil.firmware import Firmware

LOOP_UNKNOWN, LOOP_CLOSED, LOOP_UPSTREAM_OPEN = 0, 1, 3  # lhre ShutdownLoopState
# The models leave out ADC rounding: under half a count, about 0.46 psi of
# brake and 0.00015 of pedal travel (check.SURE_PSI, check.SURE_PEDAL).
BRAKE_PSI_TOLERANCE = 0.5
PEDAL_TOLERANCE = 0.0002


# (id, file, the ticks EdgeButton reports a press on, DRIVE entries, DRIVE exits)
RTD_PROBES = [
    ('press-at-1000', BASE, [1023], [1023], []),
    ('press-at-15', press_at(15), [], [], []),
    ('press-at-21', press_at(21), [], [], []),
    ('press-at-24', press_at(24), [45], [45], []),
    ('press-1000-to-1012', drive(insert_before(1500, at(1012, 'rtd_pressed: false'))), [], [],
     []),
    ('release-for-12-ms', released(2000, 2012), [1023], [1023], []),
    ('release-for-30-ms', released(2000, 2030), [1023, 2052], [1023], [2022]),
    ('release-for-21-ms', released(2000, 2022), [1023], [1023], []),
    ('release-for-22-ms', released(2000, 2023), [1023, 2046], [1023], [2022]),
    ('release-at-the-end', released(2990, None), [1023], [1023], []),
    ('press-and-release-from-boot', drive(
        ('  - t_ms: 1000\n    rtd_pressed: true\n', ''),
        ('brake_psi: 350.0', 'brake_psi: 350.0\n    rtd_pressed: true'),
        insert_before(1500, at(1100, 'rtd_pressed: false'), at(1200, 'rtd_pressed: true'))),
     [1221], [1221], []),
    # Off the brake before the press: the filter at the 1023 edge is about
    # 27.9, 31.0 and 52.5 psi.
    *[(f'brake-off-at-{t}', brake_off_at(t), [1023], [] if t == 954 else [1023], [])
      for t in (954, 957, 972)],
    ('brake-off-just-in-time', drive(('t_ms: 1500\n    brake_psi: 0.0',
                                      't_ms: 1020\n    brake_psi: 0.0')), [1023], [1023], []),
    ('pedal-at-the-edge', drive(insert_before(1500, at(1017, 'accelerator: 0.2')),
                                then=at(2000, 'accelerator: 0.0')), [1023], [], []),
    ('pedal-in-the-deadzone', drive(insert_before(1500, at(1017, 'accelerator: 0.08')),
                                    then=at(2000, 'accelerator: 0.0')), [1023], [1023], []),
    # rtd_before_ready is a margin: RTD_WAIT is at 603, so this edge counts...
    ('press-10-ms-after-energized', drive(('t_ms: 1000\n    rtd', 't_ms: 610\n    rtd')), [633],
     [633], []),
    # ... and this one, in PRECHARGE, is used up.
    ('press-while-precharging', drive(UNPLUGGED_RTD, insert_before(600, at(300,
                                                                        'rtd_pressed: true'))),
     [321], [], []),
]


def _run(lib, text: str):
    """The file through the library: the lint's models, the rows, the
    snapshots, and the state's name at each tick."""
    sf = scenario_file.loads(text.encode(), 'probe')
    with Firmware(lib) as fw:
        result = cosim.run(sf.timeline, fw, ToyCar())
        states = [fw.state_name(int(r['fw_state'])) for r in result.rows]
    assert result.status == 'completed', result.failure
    models = check._Lint(sf)
    assert [r['t_ms'] for r in result.rows] == models.times
    return sf, models, result.snapshots, states


def _entries(states: list[str], times: list[int], state: str) -> list[int]:
    return [t for k, t in enumerate(times) if states[k] == state and k and states[k - 1] != state]


def _exits(states: list[str], times: list[int], state: str) -> list[int]:
    return [t for k, t in enumerate(times) if k and states[k - 1] == state and states[k] != state]


@pytest.mark.parametrize('text, edges, entries, exits', [p[1:] for p in RTD_PROBES],
                         ids=[p[0] for p in RTD_PROBES])
def test_the_rtd_switch_model_is_the_firmwares(lib, text, edges, entries, exits):
    sf, models, snaps, states = _run(lib, text)
    # The debounced level, at every tick.
    assert models.rtd.levels == [bool(s['din_rtd_pressed']) for s in snaps]
    assert [models.times[e] for e in models.rtd.edges] == edges
    assert _entries(states, models.times, 'DRIVE') == entries
    # RtdReleased reads the debounced level: DRIVE ends on the tick it falls.
    assert _exits(states, models.times, 'DRIVE') == exits
    falls = [models.times[k] for k in range(1, len(snaps))
             if snaps[k - 1]['din_rtd_pressed'] and not snaps[k]['din_rtd_pressed']]
    assert set(exits) <= set(falls)


@pytest.mark.parametrize('text', [p[1] for p in RTD_PROBES], ids=[p[0] for p in RTD_PROBES])
def test_the_rtd_rules_agree_with_the_firmware(lib, text):
    """A press the lint is quiet about enters DRIVE at its edge, and one it
    says the firmware refuses does not. A raw press the lint warns is never
    seen gives the firmware no edge."""
    sf, models, snaps, states = _run(lib, text)
    findings = check.lint(sf)
    warned = [p for p in (*findings.active, *(w for w, _ in findings.allowed))
              if p.code.startswith('rtd_')]
    presses = {c.seen: c for c in models.rtd.changes if c.to and c.seen is not None}
    for e in models.rtd.edges:
        s = models.times[presses[e].k]
        entered = states[e] == 'DRIVE' and states[e - 1] != 'DRIVE'
        about = [p for p in warned if p.t_ms == s]
        if not about:
            assert entered, (s, states[e - 1], states[e])
        if any('the firmware refuses it' in p.message for p in about):
            assert not entered, s
    raw = models.values['rtd_pressed']
    level = [bool(s['din_rtd_pressed']) for s in snaps]
    for p in warned:
        if p.code not in ('rtd_at_boot', 'rtd_too_short'):
            continue
        k = models.times.index(p.t_ms)
        stretch = range(k, next((j for j in range(k, len(raw)) if raw[j] != raw[k]), len(raw)))
        if p.code == 'rtd_too_short':
            # Until the raw input changes again, the level never takes it.
            assert all(level[j] != raw[k] for j in stretch), p
        else:
            # The level already reads pressed, so the press is no edge.
            assert raw[k] and all(level[j] for j in stretch), p
            assert 'DRIVE' not in [states[j] for j in stretch], p


@pytest.mark.parametrize('text', [p[1] for p in RTD_PROBES], ids=[p[0] for p in RTD_PROBES])
def test_rtd_explain_notes_give_the_firmwares_tick(lib, text):
    sf, models, snaps, _ = _run(lib, text)
    level = [bool(s['din_rtd_pressed']) for s in snaps]
    raw = models.values['rtd_pressed']
    for block in sf.blocks:
        if 'rtd_pressed' not in block.values:
            continue
        value, k = block.values['rtd_pressed'], check.first_step_ms(block.t_ms) // cosim.STEP_MS
        note = models.note(block, 'rtd_pressed')
        if k and raw[k - 1] == value:
            assert note == '', block  # no change
            continue
        held = range(k, next((j for j in range(k, len(raw)) if raw[j] != value), len(raw)))
        seen = next((j for j in held if level[j] == value), None)
        before = level[k - 1] if k else True
        if before == value:
            assert 'never sees it' in note or 'already reads' in note, (block, note)
        elif seen is None:
            assert note.startswith(' (the firmware never sees it: '), (block, note)
        else:
            assert note == f' (the firmware sees it at {models.times[seen]} ms)', block


# Files for the filters, brake-pressed and brake-over-throttle: the four in
# scenarios/ and hand-made edges.
FILTER_PROBES = [
    *[(name, (scenario_file.SCENARIO_DIR / f'{name}.yml').read_text())
      for name in ('launch', 'part-throttle', 'shutdown-loop-open', 'brake-over-throttle')],
    ('hard-stop-pedal-at-2106', hard_stop_pedal_at(2106)),
    ('hard-stop-pedal-at-2130', hard_stop_pedal_at(2130)),
    ('brake-hysteresis', drive(then=at(1800, 'brake_psi: 290.0') + at(1900, 'accelerator: 0.3')
                               + at(2000, 'brake_psi: 310.0') + at(2200, 'brake_psi: 260.0')
                               + at(2400, 'brake_psi: 240.0') + at(2600, 'accelerator: 0.0')
                               + at(2700, 'brake_psi: {to: 600.0, ramp_ms: 200}'))),
    ('pedal-sweep', drive(then=at(1800, 'accelerator: {to: 1.0, ramp_ms: 400}')
                          + at(2300, 'accelerator: {to: 0.0, ramp_ms: 300}')
                          + at(2700, 'accelerator: 0.05') + at(2800, 'accelerator: 0.95'))),
]


@pytest.mark.parametrize('text', [p[1] for p in FILTER_PROBES], ids=[p[0] for p in FILTER_PROBES])
def test_the_filter_models_are_the_firmwares(lib, text):
    sf, models, snaps, _ = _run(lib, text)
    for k, s in enumerate(snaps):
        t = models.times[k]
        assert abs(models.brake[k] - s['pedals_bse_psi_filtered']) <= BRAKE_PSI_TOLERANCE, t
        # pedal_travel is the filter through the deadzone, so invert it
        # where it is not clamped.
        travel = s['pedals_pedal_travel']
        if 0.0 < travel < 1.0:
            filtered = check.PEDAL_NONE + travel * (check.PEDAL_FULL - check.PEDAL_NONE)
            assert abs(models.pedal[k] - filtered) <= PEDAL_TOLERANCE, t
        elif travel == 0.0:
            assert models.pedal[k] <= check.PEDAL_NONE + PEDAL_TOLERANCE, t
        else:
            assert models.pedal[k] >= check.PEDAL_FULL - PEDAL_TOLERANCE, t
        # The lint's brake may be pressed wherever the firmware's is.
        if s['pedals_brake_pressed']:
            assert models.may_press[k], t
        assert bool(s['pedals_brake_for_rtd']) == (models.brake[k] >= 30.0) or (
            abs(models.brake[k] - 30.0) <= BRAKE_PSI_TOLERANCE), t


@pytest.mark.parametrize('text', [p[1] for p in FILTER_PROBES], ids=[p[0] for p in FILTER_PROBES])
def test_brake_over_throttle_warns_every_latch(lib, text):
    """Each firmware latch is inside a stretch the lint warns at the start
    of: the lint fires at or before the latch."""
    sf, models, snaps, _ = _run(lib, text)
    findings = check.lint(sf)
    fired = sorted(p.t_ms for p in (*findings.active, *(w for w, _ in findings.allowed))
                   if p.code == 'brake_over_throttle')
    latch = [bool(s['pedals_brake_over_throttle']) for s in snaps]
    pedal = models.values['accelerator']
    for k in range(len(latch)):
        if latch[k] and not (k and latch[k - 1]):
            assert models.may_press[k] and pedal[k] > check.BOT_PEDAL, models.times[k]
            start = k
            while start and models.may_press[start - 1] and pedal[start - 1] > check.BOT_PEDAL:
                start -= 1
            assert models.times[start] in fired, models.times[k]


def test_the_hard_stop_probe_fires_where_the_firmware_latches(lib):
    """brake 3000 then 200 psi: the filter is still over 250 psi 105 ms
    later, so the firmware latches; the lint's margin reaches 129 ms."""
    probes = dict(FILTER_PROBES)
    sf, models, snaps, _ = _run(lib, probes['hard-stop-pedal-at-2106'])
    latched = [t for t, s in zip(models.times, snaps, strict=True)
               if s['pedals_brake_over_throttle']]
    assert latched[0] == 2106
    assert [(p.code, p.t_ms) for p in check.lint(sf).active] == [('brake_over_throttle', 2106)]
    sf, _, snaps, _ = _run(lib, probes['hard-stop-pedal-at-2130'])
    assert not any(s['pedals_brake_over_throttle'] for s in snaps)  # inside the margin
    assert [(p.code, p.t_ms) for p in check.lint(sf).active] == [('brake_over_throttle', 2130)]


def test_the_filtered_brake_at_the_edge_is_the_firmwares(lib):
    """rtd_without_brake's number is the firmware's filtered brake at the
    debounced edge."""
    for t_ms, verdict in ((954, 'so the firmware refuses it'),
                          (957, 'and under 55 psi is too close to rely on'),
                          (972, 'and under 55 psi is too close to rely on')):
        sf, models, snaps, _ = _run(lib, brake_off_at(t_ms))
        [p] = [p for p in check.lint(sf).active if p.code == 'rtd_without_brake']
        e = models.times.index(1023)
        firmware = snaps[e]['pedals_bse_psi_filtered']
        assert abs(firmware - models.brake[e]) <= BRAKE_PSI_TOLERANCE
        assert p.message == (f'the filtered brake at the debounced edge (1023 ms) is about '
                             f'{firmware:.0f} psi; RTD needs 30 psi (brake_light_psi), {verdict}')


LOOP_PROBES = [
    ('shutdown-loop-open', (scenario_file.SCENARIO_DIR / 'shutdown-loop-open.yml').read_text()),
    ('ramps', drive(then=at(2000, 'shutdown_loop_v: {to: 0.0, ramp_ms: 50}')
                    + at(2200, 'shutdown_loop_v: {to: 24.0, ramp_ms: 50}'))),
    # Slow enough that each ADC count near a threshold gets a sample.
    ('across-each-threshold', drive(then=at(1600, 'shutdown_loop_v: 17.0')
                                    + at(1700, 'shutdown_loop_v: {to: 16.0, ramp_ms: 600}')
                                    + at(2400, 'shutdown_loop_v: 19.0')
                                    + at(2450, 'shutdown_loop_v: {to: 19.4, ramp_ms: 500}'))),
    ('a-dip-under-the-debounce', drive(then=at(2000, 'shutdown_loop_v: 0.0')
                                       + at(2006, 'shutdown_loop_v: 24.0'))),
    ('open-from-boot', drive(('brake_psi: 350.0', 'brake_psi: 350.0\n    shutdown_loop_v: 0.0'),
                             insert_before(1500, at(1200, 'shutdown_loop_v: 24.0')))),
    ('in-the-band-from-boot', drive(('brake_psi: 350.0',
                                     'brake_psi: 350.0\n    shutdown_loop_v: 18.0'),
                                    insert_before(1500, at(1200, 'shutdown_loop_v: 24.0')))),
    ('opens-before-it-reports', drive(insert_before(200, at(5, 'shutdown_loop_v: 0.0'),
                                                    at(100, 'shutdown_loop_v: 24.0')))),
]


@pytest.mark.parametrize('text', [p[1] for p in LOOP_PROBES], ids=[p[0] for p in LOOP_PROBES])
def test_the_loop_model_is_the_firmwares(lib, text):
    sf, models, snaps, _ = _run(lib, text)
    states = {None: LOOP_UNKNOWN, True: LOOP_CLOSED, False: LOOP_UPSTREAM_OPEN}
    assert [states[r] for r in models.loop.reported] == [s['loop_state'] for s in snaps]
    pins = [check._loop_pin_v(v) for v in models.values['shutdown_loop_v']]
    for k, s in enumerate(snaps):
        assert pins[k] == pytest.approx(s['analog_shutdown_in_pin_v'], abs=1e-6)
        assert pins[k] == pytest.approx(s['analog_shutdown_out_pin_v'], abs=1e-6)
    # Each explain note gives the tick the reported state takes the block's
    # side of the threshold.
    reported = [s['loop_state'] for s in snaps]
    for block in sf.blocks:
        if 'shutdown_loop_v' not in block.values or block.t_ms == 0:
            continue
        note = models.note(block, 'shutdown_loop_v')
        seen = re.fullmatch(r' \(the firmware sees it at (\d+) ms\)', note)
        if seen:
            k = models.times.index(int(seen[1]))
            assert reported[k] != reported[k - 1], (block, note)
        lo, hi = models.span(block, 'shutdown_loop_v')
        changed = [models.times[k] for k in range(max(lo, 1), min(hi + 6, len(reported)))
                   if reported[k] != reported[k - 1]]
        if not note:
            assert not [t for t in changed if t < models.times[min(hi, len(reported) - 1)]], block
        elif seen:
            assert int(seen[1]) == changed[0], (block, note, changed)
        else:  # swallowed by the debounce, or a return to what it reads
            assert ('never sees it' in note or 'already reads' in note) and not changed, (
                block, note, changed)
