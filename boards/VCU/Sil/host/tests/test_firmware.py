import ctypes

import pytest

from vcusil import firmware
from vcusil.firmware import (HVC_ENERGIZED, STATE_DRIVE, Firmware, FirmwareError, Inputs,
                             check_mirror, library_layout)


def parked() -> Inputs:
    return Inputs(hvc_state=HVC_ENERGIZED, contactors_closed=1, shutdown_loop_v=24.0,
                  brake_front_psi=350.0, brake_rear_psi=350.0, dc_bus_voltage_v=574.0,
                  min_cell_v=4.1, max_cell_v=4.1)


def hold(fw: Firmware, inputs: Inputs, ms: int):
    out = None
    for _ in range(ms // 3):
        out = fw.advance(inputs, 3)
    return out


def test_binding_mirrors_the_library_field_by_field(lib):
    layout = library_layout(lib)
    check_mirror(layout)
    assert set(layout) == {'config', 'inputs', 'outputs', 'snapshot', 'frame'}
    assert len(layout['outputs']) == len(firmware.Outputs._fields_) + 1


def test_a_reordered_field_is_caught(lib):
    fields = list(firmware.Inputs._fields_)
    fields[3], fields[4] = fields[4], fields[3]  # brake_front_psi <-> brake_rear_psi
    swapped = type('Swapped', (ctypes.Structure,), {'_fields_': fields})
    with pytest.raises(FirmwareError, match='brake_front_psi'):
        check_mirror(library_layout(lib), {**firmware.MIRRORS, 'inputs': swapped})


def test_a_missing_field_is_caught(lib):
    fields = [f for f in firmware.Outputs._fields_ if f[0] != 'buzzer']
    short = type('Short', (ctypes.Structure,), {'_fields_': fields})
    with pytest.raises(FirmwareError, match='buzzer'):
        check_mirror(library_layout(lib), {**firmware.MIRRORS, 'outputs': short})


def test_reaches_drive_and_commands_torque(lib):
    with Firmware(lib) as fw:
        inputs = parked()
        assert fw.state_name(hold(fw, inputs, 300).state) == 'RTD_WAIT'
        inputs.rtd_pressed = 1
        hold(fw, inputs, 60)
        inputs.brake_front_psi = inputs.brake_rear_psi = 0.0
        hold(fw, inputs, 60)
        inputs.accelerator_travel = 1.0
        inputs.motor_speed_rad_s = 300.0
        out = hold(fw, inputs, 240)
        assert out.state == STATE_DRIVE
        assert out.inverter_enable == 1
        assert 150.0 < out.torque_request_nm <= 230.0
        assert out.now_ms == 660 and out.control_ms == 657
        assert out.event_mode == 4  # mode 0 boots like the car: ENDURANCE
        assert '-> DRIVE' in fw.read_console()
        assert fw.read_console() == ''
        build = fw.build_info()
        assert build['git_sha'] and isinstance(build['dirty'], bool)


def test_rejected_calls_raise_with_the_error_name(lib):
    fw = Firmware(lib)
    bad = parked()
    bad.accelerator_travel = 1.5
    with pytest.raises(FirmwareError, match='ERR_RANGE'):
        fw.advance(bad, 3)
    bad = parked()
    bad.dc_bus_current_a = float('nan')
    with pytest.raises(FirmwareError, match='ERR_NON_FINITE'):
        fw.advance(bad, 3)
    with pytest.raises(FirmwareError, match='ERR_RANGE'):
        fw.advance(parked(), 0)
    assert fw.advance(parked(), 3).now_ms == 3  # nothing above moved time
    fw.close()
    fw.close()
    with pytest.raises(FirmwareError, match='closed'):
        fw.advance(parked(), 3)
    with pytest.raises(FirmwareError, match='ERR_RANGE'):
        Firmware(lib, event_mode=9)


# One car-model value past what its sensor or CAN field can carry, per bit.
CLAMPED = {
    'wheel_speed': ('wheel_speed_rad_s', 400.0),
    'motor_speed': ('motor_speed_rad_s', 4000.0),
    'motor_torque': ('motor_torque_nm', 1e6),
    'bus_voltage': ('dc_bus_voltage_v', 7000.0),
    'bus_current': ('dc_bus_current_a', -5000.0),
    'cell_voltage': ('max_cell_v', 7.0),
    'steering': ('steering_angle_deg', 500.0),
}


def test_each_saturation_bit_is_the_one_its_clamp_sets(lib):
    """SATURATION_BITS copies vcu_sil.h by hand, where the layout check cannot see it."""
    assert set(CLAMPED) == set(firmware.SATURATION_BITS)
    with Firmware(lib) as fw:
        assert fw.advance(parked(), 3).saturated == 0
        for name, (field, value) in CLAMPED.items():
            inputs = parked()
            if field == 'wheel_speed_rad_s':
                inputs.wheel_speed_rad_s[2] = value
            else:
                setattr(inputs, field, value)
            assert fw.advance(inputs, 3).saturated == firmware.SATURATION_BITS[name], name


def test_fault_bits_are_named_from_the_spec(lib):
    with Firmware(lib) as fw:
        assert fw.fault_names(0) == []
        assert fw.fault_names(1 | 1 << 7) == ['APPS_IMPLAUSIBLE', 'BRAKE_OVER_THROTTLE']
        assert fw.state_name(99) == '?'


def test_the_snapshot_struct_is_built_from_the_library(lib):
    struct = lib.snapshot_struct
    layout = firmware.library_layout(lib)
    assert ctypes.sizeof(struct) == layout['snapshot'][''][1]
    named = [n for n, _ in struct._fields_ if not n.startswith('_pad')]
    assert len(named) == 112 and named[0] == 'struct_size'
    for name in named:
        offset, size, _, _ = layout['snapshot'][name]
        assert (getattr(struct, name).offset, getattr(struct, name).size) == (offset, size)


def test_snapshot_frames_and_reasons_read_through_the_binding(lib):
    with Firmware(lib) as fw:
        snap = fw.read_snapshot()
        assert snap['state'] == 0 and snap['last_transition_id'] == -1
        assert fw.transition_reason(-1) == 'boot'
        assert len(snap) == 111
        inputs = parked()
        out = hold(fw, inputs, 300)
        snap = fw.read_snapshot()
        assert snap['now_ms'] == out.control_ms
        assert fw.transition_reason(snap['last_transition_id']) == 'drive preconditions met'
        assert isinstance(snap['analog_vdda'], float) and isinstance(snap['din_rtd_pressed'], int)
        frames = fw.read_frames()
        assert frames == sorted(frames, key=lambda f: f[0])
        assert {(bus, fid, from_vcu) for _, bus, fid, from_vcu, _ in frames} >= {
            (firmware.BUS_CRITICAL, 0x0C0, True), (firmware.BUS_CRITICAL, 0x0B0, False),
            (firmware.BUS_DATA, 0x404, False)}
        assert all(len(data) <= 8 for *_, data in frames)
        # A frame keeps its own length: CONTACTOR_STATUS is 4 bytes.
        contactors = [data for _, _, fid, from_vcu, data in frames if fid == 0x131 and not from_vcu]
        assert contactors and {len(d) for d in contactors} == {4}
        assert fw.read_frames() == []
        assert out.frames_dropped == 0
